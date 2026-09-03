import tkinter as tk
from tkinter import ttk, messagebox

import clinic_repository as repo
import session
import theme
import ui_style
from money import to_money, money_sum
import ui_popups


class ClinicVisit:
    """New Visit fast-entry screen - the heart of the Clinic Ledger
    module. Built around CLINIC_LEDGER_WORKFLOW.md's "FAST + SIMPLE +
    MINIMUM DATA ENTRY" rule: search-select patient, one search box for
    medicine/injection, default qty 1, Enter-to-add, totals auto-update,
    a single Save button. All cost/profit math is only ever a PREVIEW
    here - the authoritative numbers are recomputed server-side inside
    clinic_repository.add_visit() at Save time (see that function's
    docstring on why a stale UI value must never be trusted as truth)."""

    def __init__(self, frame, on_close=None):
        self.frame = frame
        self.on_close = on_close
        self.selected_patient_id = None
        self.selected_patient_name = None
        self.pending_items = []   # list of dicts, not yet saved
        self.create_variables()
        self.create_ui()

    def create_variables(self):
        self.patient_search = tk.StringVar()
        self.patient_display = tk.StringVar(value="No patient selected")
        self.doctor = tk.StringVar()
        self.reason = tk.StringVar()
        self.consultation_amount = tk.StringVar(value="0")

        self.item_type = tk.StringVar(value="Medicine")
        self.medicine_search = tk.StringVar()
        self.qty = tk.StringVar(value="1")
        self.is_adhoc = tk.BooleanVar(value=False)
        self.manual_cost = tk.StringVar(value="0")
        self.manual_mrp = tk.StringVar(value="0")

        self.lbl_purchase_cost = tk.StringVar(value="₹ 0.00")
        self.lbl_mrp_value = tk.StringVar(value="₹ 0.00")
        self.lbl_gross_profit = tk.StringVar(value="₹ 0.00")   # = Actual Net Profit (kept as lbl_gross_profit internally - see _refresh_gross_profit_only())
        # THREE distinct profit numbers, shown together on purpose (see
        # clinic_repository.compute_profit_breakdown()'s docstring for the
        # full definition of each - these are the same official names):
        #   Actual Net Profit      = Amount Collected - Purchase Cost
        #     -> what you ACTUALLY made on this visit, real money in hand.
        #     (this is self.lbl_gross_profit above)
        #   Consulting Charge      = Amount Collected - Medicine MRP Value
        #     -> what's left of the collected amount after "paying for"
        #        the medicines at full MRP; negative means the bundled
        #        charge was below the medicines' combined MRP.
        #   Medicine Margin Profit = Medicine MRP Value - Purchase Cost
        #     -> what the margin WOULD be if every item were sold at its
        #        printed MRP, regardless of what was actually collected.
        self.lbl_consulting_charge = tk.StringVar(value="₹ 0.00")
        self.lbl_mrp_profit = tk.StringVar(value="₹ 0.00")  # = Medicine Margin Profit

        # Amount Collected is EDITABLE, not just a computed label - many
        # clinics collect one flat/bundled amount per visit (e.g. Rs.200
        # for 2 injections + 4 tablets) that does not equal the itemized
        # Consultation + MRP total. It auto-fills from Consultation+MRP
        # as items are added, but the moment staff types over it by hand
        # (_on_collection_edited below), auto-fill stops so their typed
        # figure is never silently overwritten - "Reset to Auto" brings
        # the computed figure back if needed.
        self.total_collected = tk.StringVar(value="0.00")
        self._auto_collection_active = True
        self._updating_collection_programmatically = False

        # "All-in-One" save: when checked, save_visit() also creates a
        # Pharmacy Sales invoice for the stock-tracked medicines used in
        # this visit (see clinic_repository.add_visit()'s
        # auto_generate_bill docstring for the full reconciliation
        # logic). Defaults OFF (Aug 2026 - user's stated preference is
        # "save the visit fast, decide the bill after") - the "Bill Now"
        # panel below is the normal path; check this box only when the
        # doctor wants everything done in one click instead.
        self.auto_generate_bill = tk.BooleanVar(value=False)

        # "Bill Now" panel (Aug 2026) - tracks the MOST RECENTLY saved
        # visit so its own "Create Bill" button can stay on THIS screen
        # after Save, independent of the entry form which clear_all()
        # resets right away for the next patient. None until a visit has
        # been saved this session.
        self.last_saved_visit_id = None
        self.last_saved_patient_name = None
        self.last_saved_bill_no = None
        self.last_saved_summary = tk.StringVar(value="No visit saved yet this session.")

    # ------------------------------------------------------------
    # UI
    # ------------------------------------------------------------

    def create_ui(self):
        title = tk.Label(
            self.frame, text="NEW CLINIC VISIT",
            bg=theme.PRIMARY, fg="white", font=("Segoe UI", 18, "bold"), pady=10
        )
        title.pack(fill="x")

        # ---- Patient selection ----
        pf = tk.LabelFrame(self.frame, text="Patient", font=("Segoe UI", 10, "bold"))
        pf.pack(fill="x", padx=10, pady=(10, 5))

        tk.Label(pf, text="Search (name/phone)").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        search_entry = tk.Entry(pf, textvariable=self.patient_search, width=30)
        search_entry.grid(row=0, column=1, padx=5)
        self.patient_search.trace_add("write", self._refresh_patient_suggestions)

        self.patient_listbox = tk.Listbox(pf, height=4, width=50)
        self.patient_listbox.grid(row=1, column=0, columnspan=2, padx=5, sticky="we")
        self.patient_listbox.bind("<<ListboxSelect>>", self._pick_patient)
        self._patient_lookup = {}
        self._patient_name_lookup = {}

        # ERP-wide keyboard-nav pass (Aug 2026): ArrowUp/ArrowDown now
        # move the highlighted suggestion while typing keeps focus in
        # the search box, and Enter confirms it (previously Enter did
        # nothing here at all - only a mouse click worked). See
        # ui_style.bind_listbox_navigation()'s docstring.
        ui_style.bind_listbox_navigation(search_entry, self.patient_listbox)
        search_entry.bind("<Return>", self._pick_patient)

        tk.Label(pf, textvariable=self.patient_display, font=("Segoe UI", 11, "bold"),
                 fg=theme.STATUS_SUCCESS).grid(row=0, column=2, columnspan=2, padx=20, sticky="w")

        tk.Button(pf, text="+ New Patient (Quick)", bg=theme.ACCENT_NEUTRAL, fg="white",
                  command=self._open_quick_patient).grid(row=1, column=2, columnspan=2, padx=20, sticky="w")

        # ---- Visit info ----
        vf = tk.LabelFrame(self.frame, text="Visit Details", font=("Segoe UI", 10, "bold"))
        vf.pack(fill="x", padx=10, pady=5)

        tk.Label(vf, text="Doctor").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.txtDoctor = tk.Entry(vf, textvariable=self.doctor, width=25)
        self.txtDoctor.grid(row=0, column=1)
        tk.Label(vf, text="Reason for Visit").grid(row=0, column=2, padx=5)
        tk.Entry(vf, textvariable=self.reason, width=35).grid(row=0, column=3)
        tk.Label(vf, text="Consultation Amount (₹)").grid(row=0, column=4, padx=5)
        c_entry = tk.Entry(vf, textvariable=self.consultation_amount, width=12)
        c_entry.grid(row=0, column=5)
        self.consultation_amount.trace_add("write", lambda *a: self._refresh_totals())

        # ---- Add item row ----
        # "A-la-carte" cart (Aug 2026): the doctor can add ANY combination
        # of these 5 categories in any order, any subset, or none at all
        # beyond a consultation fee - save_visit() only ever required
        # "at least one item OR a consultation amount", never a specific
        # category, so Consultation-only, Injection-only, Consumable-
        # only (dressing/nebulization) etc. all already just work.
        itf = tk.LabelFrame(self.frame, text="Add Item (Medicine / Syrup / Injection / Consumable / IV Fluid)",
                             font=("Segoe UI", 10, "bold"))
        itf.pack(fill="x", padx=10, pady=5)

        ttk.Combobox(itf, textvariable=self.item_type, values=repo.CLINIC_ITEM_CATEGORIES,
                     state="readonly", width=12).grid(row=0, column=0, padx=5, pady=5)
        # Picking a category re-filters the search results immediately
        # (see _refresh_medicine_suggestions()'s category-aware call to
        # repo.search_clinic_medicines()), same as typing a fresh search.
        self.item_type.trace_add("write", self._refresh_medicine_suggestions)

        tk.Label(itf, text="Search").grid(row=0, column=1, padx=5)
        item_search_entry = tk.Entry(itf, textvariable=self.medicine_search, width=25)
        item_search_entry.grid(row=0, column=2, padx=5)
        self.medicine_search.trace_add("write", self._refresh_medicine_suggestions)
        item_search_entry.bind("<Return>", lambda e: self.add_item())

        self.medicine_listbox = tk.Listbox(itf, height=4, width=30)
        self.medicine_listbox.grid(row=1, column=1, columnspan=2, padx=5, sticky="we")

        # ERP-wide keyboard-nav pass (Aug 2026): ArrowUp/ArrowDown now
        # move the highlighted suggestion while typing keeps focus in
        # the search box - Enter (bound above) and mouse click already
        # worked (add_item() already reads curselection()). See
        # ui_style.bind_listbox_navigation()'s docstring.
        ui_style.bind_listbox_navigation(item_search_entry, self.medicine_listbox)

        tk.Label(itf, text="Qty").grid(row=0, column=3, padx=5)
        tk.Entry(itf, textvariable=self.qty, width=6).grid(row=0, column=4)

        tk.Checkbutton(itf, text="Not-stocked (manual cost)", variable=self.is_adhoc,
                        command=self._toggle_adhoc).grid(row=0, column=5, padx=10)

        self.manual_frame = tk.Frame(itf)
        tk.Label(self.manual_frame, text="Cost/unit").pack(side="left")
        tk.Entry(self.manual_frame, textvariable=self.manual_cost, width=8).pack(side="left", padx=3)
        tk.Label(self.manual_frame, text="MRP/unit").pack(side="left")
        tk.Entry(self.manual_frame, textvariable=self.manual_mrp, width=8).pack(side="left", padx=3)
        # hidden until "Not-stocked" is checked - keeps the default flow
        # minimal per the workflow spec's "advanced info under More
        # Details" rule.

        tk.Button(itf, text="Add", bg=theme.STATUS_SUCCESS, fg="white", width=10,
                  command=self.add_item).grid(row=0, column=6, padx=10)

        # ---- Items table ----
        table = tk.Frame(self.frame)
        table.pack(fill="both", expand=True, padx=10, pady=5)
        cols = ("Type", "Item", "Batch", "Qty", "Unit Cost", "Unit MRP", "Cost Total", "MRP Total", "Profit")
        self.itemsTable = ttk.Treeview(table, columns=cols, show="headings", height=8, style="ERP.Treeview")
        for c in cols:
            self.itemsTable.heading(c, text=c)
            self.itemsTable.column(c, width=95, anchor="center")
        self.itemsTable.pack(fill="both", expand=True)

        # Guaranteed row-click highlight (Aug 2026 request): "ERP.Treeview"
        # already has a selected-row colour configured globally in
        # main.py, but that relies on ttk's own theme/style resolution,
        # which this sandbox has no live GUI to verify against the
        # user's real machine. A Treeview *tag* applied by hand on every
        # <<TreeviewSelect>> event paints the row directly, independent
        # of whichever ttk theme/style ends up active - so the light-blue
        # highlight is certain to show up regardless of that ambiguity.
        self.itemsTable.tag_configure("rowsel", background="#e0f0ff", foreground="#0D47A1")
        self.itemsTable.bind("<<TreeviewSelect>>", self._on_item_row_select)
        # Double-click a row to pull it back into the "Add Item" fields
        # for editing (qty, or cost/MRP for a not-stocked item) - the
        # original row is removed so re-adding via the normal Add
        # button/Enter never leaves a duplicate behind.
        self.itemsTable.bind("<Double-1>", self._on_item_double_click)

        item_btn_row = tk.Frame(self.frame)
        item_btn_row.pack(fill="x", padx=10)
        tk.Button(item_btn_row, text="Remove Selected Item", bg=theme.STATUS_DANGER, fg="white",
                  command=self.remove_item).pack(side="left")
        tk.Button(item_btn_row, text="Clear All", bg=theme.ACCENT_NEUTRAL, fg="white",
                  command=self.clear_items).pack(side="left", padx=(8, 0))

        # ---- Totals ----
        tf = tk.LabelFrame(self.frame, text="Visit Totals", font=("Segoe UI", 10, "bold"))
        tf.pack(fill="x", padx=10, pady=5)
        for i, (label, var) in enumerate([
            ("Medicine Purchase Cost", self.lbl_purchase_cost),
            ("Medicine MRP Value", self.lbl_mrp_value),
            ("Medicine Margin Profit (MRP − Cost)", self.lbl_mrp_profit),
        ]):
            tk.Label(tf, text=label, font=("Segoe UI", 9)).grid(row=0, column=i * 2, padx=5, pady=5)
            tk.Label(tf, textvariable=var, font=("Segoe UI", 11, "bold"), fg=theme.PRIMARY).grid(row=0, column=i * 2 + 1, padx=5)

        tk.Label(tf, text="Amount Collected (₹) - editable, actual amount received").grid(
            row=1, column=0, columnspan=2, padx=5, pady=(8, 2), sticky="w")
        collected_entry = tk.Entry(tf, textvariable=self.total_collected, width=12, font=("Segoe UI", 11, "bold"))
        collected_entry.grid(row=1, column=2, padx=5, sticky="w")
        self.total_collected.trace_add("write", self._on_collection_edited)
        tk.Button(tf, text="Reset to Auto (Consultation + MRP)", bg=theme.ACCENT_NEUTRAL, fg="white",
                  command=self._reset_collection_to_auto).grid(row=1, column=3, padx=10, sticky="w")

        tk.Label(tf, text="Consulting Charge (Amount Collected − MRP Value)",
                 font=("Segoe UI", 9)).grid(row=2, column=0, columnspan=2, padx=5, pady=(8, 2), sticky="w")
        tk.Label(tf, textvariable=self.lbl_consulting_charge, font=("Segoe UI", 11, "bold"),
                 fg=theme.PRIMARY).grid(row=2, column=2, padx=5, sticky="w")

        tk.Label(tf, text="Actual Net Profit (Amount Collected − Purchase Cost) - this is what gets saved",
                 font=("Segoe UI", 9)).grid(row=3, column=0, columnspan=3, padx=5, pady=(8, 2), sticky="w")
        tk.Label(tf, textvariable=self.lbl_gross_profit, font=("Segoe UI", 13, "bold"),
                 fg=theme.STATUS_SUCCESS).grid(row=3, column=3, padx=5, sticky="w")

        # ---- Save / Close ----
        auto_bill_row = tk.Frame(self.frame)
        auto_bill_row.pack(fill="x", padx=10)
        tk.Checkbutton(
            auto_bill_row, text="Also auto-create Pharmacy Sales Bill for medicines used (no separate Billing entry needed)",
            variable=self.auto_generate_bill, font=("Segoe UI", 9)
        ).pack(anchor="w")

        btn = tk.Frame(self.frame)
        btn.pack(fill="x", padx=10, pady=10)
        tk.Button(btn, text="Save Visit", bg=theme.STATUS_SUCCESS, fg="white", width=16,
                  font=("Segoe UI", 10, "bold"), command=self.save_visit).pack(side="left", padx=5)
        tk.Button(btn, text="Clear", bg=theme.ACCENT_NEUTRAL, fg="white", width=12,
                  command=self.clear_all).pack(side="left", padx=5)
        if self.on_close:
            tk.Button(btn, text="Close", bg=theme.STATUS_DANGER, fg="white", width=12,
                      command=self.on_close).pack(side="right", padx=5)

        # ---- Last Saved Visit / Bill Now (Aug 2026) ----
        # Stays on THIS screen after a Save, independent of the entry
        # form above (which clear_all() already reset for the next
        # patient) - lets the doctor save fast now and decide the bill
        # right after, without hunting for the visit in Patient History.
        # See clinic_repository.generate_bill_for_visit()'s docstring for
        # why this never double-deducts stock or double-bills.
        lb = tk.LabelFrame(self.frame, text="Last Saved Visit", font=("Segoe UI", 10, "bold"))
        lb.pack(fill="x", padx=10, pady=(0, 10))
        tk.Label(lb, textvariable=self.last_saved_summary, font=("Segoe UI", 10),
                 wraplength=650, justify="left").pack(side="left", padx=10, pady=8, fill="x", expand=True)
        self.btn_bill_now = tk.Button(
            lb, text="Bill Now", bg=theme.STATUS_SUCCESS, fg="white", width=14,
            font=("Segoe UI", 10, "bold"), state="disabled", command=self._bill_now
        )
        self.btn_bill_now.pack(side="right", padx=10, pady=8)

    def _toggle_adhoc(self):
        if self.is_adhoc.get():
            self.manual_frame.grid(row=1, column=3, columnspan=3, sticky="w")
        else:
            self.manual_frame.grid_forget()

    # ------------------------------------------------------------
    # Patient search / select
    # ------------------------------------------------------------

    def _refresh_patient_suggestions(self, *args):
        self.patient_listbox.delete(0, "end")
        self._patient_lookup = {}
        self._patient_name_lookup = {}
        text = self.patient_search.get().strip()
        if not text:
            return
        for row in repo.search_patients(text, limit=8):
            pid, code, name, age, gender, phone, address = row
            display = f"{name} ({code}) - {phone or 'no phone'}"
            self.patient_listbox.insert("end", display)
            self._patient_lookup[display] = pid
            self._patient_name_lookup[display] = name

    def _pick_patient(self, event=None):
        sel = self.patient_listbox.curselection()
        if not sel and self.patient_listbox.size() >= 1:
            # ERP-wide keyboard-nav pass (Aug 2026): pressing Enter right
            # after typing, before ever pressing Down, now still picks
            # the top suggestion - matching how Billing/Purchase's Enter
            # confirms the first live match without requiring an explicit
            # arrow-key press first.
            sel = (0,)
        if not sel:
            return
        display = self.patient_listbox.get(sel[0])
        pid = self._patient_lookup.get(display)
        if pid is None:
            return
        self.selected_patient_id = pid
        self.selected_patient_name = self._patient_name_lookup.get(display, display)
        self.patient_display.set(f"Selected: {display}")
        # Auto-focus the next logical field, same as every other
        # "add item"/"pick a record" flow touched in this pass.
        if hasattr(self, "txtDoctor"):
            self.txtDoctor.focus_set()
            self.txtDoctor.select_range(0, tk.END)

    def _open_quick_patient(self):
        win = tk.Toplevel(self.frame)
        win.title("New Patient (Quick)")
        win.resizable(False, False)

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring) - purely cosmetic,
        # this popup's modal-ness (none, same as before) is unchanged.
        outer = ui_style.popup_header(win, "New Patient (Quick)", icon="🩺")
        body = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=20, pady=16)
        body.pack(fill="both", expand=True)

        name_v, age_v, gender_v, phone_v = tk.StringVar(), tk.StringVar(), tk.StringVar(value="Male"), tk.StringVar()

        def _field_label(text):
            tk.Label(
                body, text=text, bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
                font=("Segoe UI", 10), anchor="w",
            ).pack(fill="x", pady=(10, 2))

        def _entry_kwargs():
            return dict(
                font=("Segoe UI", 10), bg=theme.SURFACE_FIELD, relief="flat",
                highlightthickness=1, highlightbackground=theme.BORDER_DEFAULT,
                highlightcolor=theme.BORDER_FOCUS,
            )

        _field_label("Name *")
        tk.Entry(body, textvariable=name_v, **_entry_kwargs()).pack(fill="x", ipady=3)
        _field_label("Age")
        tk.Entry(body, textvariable=age_v, **_entry_kwargs()).pack(fill="x", ipady=3)
        _field_label("Gender")
        ttk.Combobox(body, textvariable=gender_v, values=["Male", "Female", "Other"], state="readonly").pack(fill="x")
        _field_label("Phone")
        tk.Entry(body, textvariable=phone_v, **_entry_kwargs()).pack(fill="x", ipady=3)

        def save_and_select():
            if not name_v.get().strip():
                ui_popups.show_error(win, "Error", "Name is required")
                return
            try:
                pid, code = repo.create_patient(
                    name_v.get().strip(),
                    int(age_v.get()) if age_v.get().strip().isdigit() else None,
                    gender_v.get(), phone_v.get().strip(), "",
                    created_by=session.get_current_user(),
                )
                self.selected_patient_id = pid
                self.selected_patient_name = name_v.get().strip()
                self.patient_display.set(f"Selected: {name_v.get().strip()} ({code})")
                win.destroy()
            except Exception as e:
                ui_popups.show_error(win, "Database Error", str(e))

        ui_style.flat_button(
            body, "Save & Select", theme.STATUS_SUCCESS, save_and_select, width=16,
        ).pack(pady=(18, 0))

        # No explicit width/height (the restyle made this taller than
        # the old fixed 350x260 guess) - see ui_style.center_window()'s
        # own docstring for why sizing to real packed content is safer.
        ui_style.center_window(win, parent=self.frame.winfo_toplevel())

    # ------------------------------------------------------------
    # Medicine search / add item
    # ------------------------------------------------------------

    def _refresh_medicine_suggestions(self, *args):
        self.medicine_listbox.delete(0, "end")
        text = self.medicine_search.get().strip()
        if not text or self.is_adhoc.get():
            return
        # Filtered by the currently selected category (Medicine/Syrup/
        # Injection/Consumable/IV Fluids) - see search_clinic_medicines()'s
        # docstring for the safe-fallback rule (a medicine with no Dosage
        # Form set on it yet still shows up in every category).
        for name in repo.search_clinic_medicines(text, limit=8, category=self.item_type.get()):
            self.medicine_listbox.insert("end", name)

    def add_item(self):
        try:
            qty = float(self.qty.get())
        except ValueError:
            ui_popups.show_error(self.frame, "Error", "Invalid quantity")
            return
        if qty <= 0:
            ui_popups.show_error(self.frame, "Error", "Quantity must be greater than 0")
            return

        item_type = self.item_type.get()

        if self.is_adhoc.get():
            name = self.medicine_search.get().strip()
            if not name:
                ui_popups.show_error(self.frame, "Error", "Enter an item name")
                return
            unit_cost = to_money(self.manual_cost.get() or 0)
            unit_mrp = to_money(self.manual_mrp.get() or 0)
            cost_total = to_money(unit_cost * qty)
            mrp_total = to_money(unit_mrp * qty)
            self.pending_items.append({
                "item_type": item_type, "name": name, "qty": qty,
                "medicine_id": None, "manual_unit_cost": unit_cost, "manual_unit_mrp": unit_mrp,
            })
            self.itemsTable.insert("", "end", values=(
                item_type, name, "-", qty, unit_cost, unit_mrp, cost_total, mrp_total,
                to_money(mrp_total - cost_total)
            ))
        else:
            sel = self.medicine_listbox.curselection()
            name = self.medicine_listbox.get(sel[0]) if sel else self.medicine_search.get().strip()
            if not name:
                ui_popups.show_error(self.frame, "Error", "Select a medicine from the suggestion list")
                return
            try:
                cost_total, mrp_total = repo.preview_item_cost(name, qty)
            except repo.InsufficientStockError as e:
                ui_popups.show_error(self.frame, "Insufficient Stock", str(e))
                return
            except Exception as e:
                ui_popups.show_error(self.frame, "Error", str(e))
                return
            self.pending_items.append({
                "item_type": item_type, "name": name, "qty": qty, "medicine_id": True,
            })
            unit_cost = to_money(cost_total / qty) if qty else 0
            unit_mrp = to_money(mrp_total / qty) if qty else 0
            self.itemsTable.insert("", "end", values=(
                item_type, name, "(FEFO auto)", qty, unit_cost, unit_mrp, cost_total, mrp_total,
                to_money(mrp_total - cost_total)
            ))

        self.medicine_search.set("")
        self.qty.set("1")
        self._refresh_totals()

    def remove_item(self):
        selected = self.itemsTable.selection()
        if not selected:
            return
        index = self.itemsTable.index(selected[0])
        self.itemsTable.delete(selected[0])
        del self.pending_items[index]
        self._refresh_totals()

    def clear_items(self):
        """"Clear All" - empties just the item cart/grid and zeroes the
        totals. Deliberately does NOT touch the selected patient/doctor/
        reason fields above it (that's what the separate "Clear" button
        + clear_all() does, for starting a whole new visit) - this one
        only resets the cart, matching the literal request scope."""
        if not self.pending_items:
            return
        if not ui_popups.show_confirmation(self.frame, "Clear All", "Remove all items from this visit's cart?"):
            return
        self.pending_items = []
        self.itemsTable.delete(*self.itemsTable.get_children())
        self._auto_collection_active = True
        self._refresh_totals()

    def _on_item_row_select(self, event=None):
        """Paints the currently selected row(s) with the "rowsel" tag
        (light blue, see create_ui()) and clears it from every other
        row - kept as an explicit tag rather than relying only on ttk's
        built-in "selected" state styling, so the highlight is guaranteed
        visible regardless of theme/style resolution."""
        selected = set(self.itemsTable.selection())
        for iid in self.itemsTable.get_children():
            self.itemsTable.item(iid, tags=("rowsel",) if iid in selected else ())

    def _on_item_double_click(self, event=None):
        """Click-to-edit: loads the double-clicked row back into the
        "Add Item" fields (category, name/search text, qty, and - for a
        not-stocked item - its manual cost/MRP) and removes the original
        row, so the qty can be adjusted and the item re-added via the
        normal Add button/Enter without leaving a duplicate behind."""
        selected = self.itemsTable.selection()
        if not selected:
            return
        item_id = selected[0]
        index = self.itemsTable.index(item_id)
        if index >= len(self.pending_items):
            return
        values = self.itemsTable.item(item_id)["values"]
        item_type, name, qty = values[0], values[1], values[3]
        pending = self.pending_items[index]

        self.itemsTable.delete(item_id)
        del self.pending_items[index]

        self.item_type.set(item_type)
        self.qty.set(str(qty))
        if pending.get("medicine_id") is None:
            self.is_adhoc.set(True)
            self._toggle_adhoc()
            self.manual_cost.set(str(pending.get("manual_unit_cost", 0)))
            self.manual_mrp.set(str(pending.get("manual_unit_mrp", 0)))
        else:
            self.is_adhoc.set(False)
            self._toggle_adhoc()
        self.medicine_search.set(name)

        self._refresh_totals()

    def _set_collected_programmatically(self, value):
        """Sets self.total_collected without triggering the "user typed
        over it" detection in _on_collection_edited() below."""
        self._updating_collection_programmatically = True
        self.total_collected.set(f"{value:.2f}")
        self._updating_collection_programmatically = False

    def _on_collection_edited(self, *args):
        """Fires on every change to the Amount Collected field, whether
        from our own auto-fill or from the user typing. Only a REAL user
        edit should turn auto-fill off - self._updating_collection_
        programmatically distinguishes the two (see the trace_add() call
        in create_ui())."""
        if not self._updating_collection_programmatically:
            self._auto_collection_active = False
        self._refresh_gross_profit_only()

    def _reset_collection_to_auto(self):
        self._auto_collection_active = True
        self._refresh_totals()

    def _refresh_gross_profit_only(self):
        """Recomputes the Actual Net Profit and Consulting Charge labels
        from whatever is currently in the (possibly hand-typed) Amount
        Collected field - called on every keystroke there, without
        touching Amount Collected itself (that would fight the user's
        typing)."""
        purchase_cost = money_sum(float(self.itemsTable.item(i)["values"][6]) for i in self.itemsTable.get_children())
        mrp_value = money_sum(float(self.itemsTable.item(i)["values"][7]) for i in self.itemsTable.get_children())
        try:
            collected = to_money(self.total_collected.get() or 0)
        except ValueError:
            collected = 0.0
        self.lbl_gross_profit.set(f"₹ {to_money(collected - purchase_cost):,.2f}")  # Actual Net Profit
        self.lbl_consulting_charge.set(f"₹ {to_money(collected - mrp_value):,.2f}")

    def _refresh_totals(self, *args):
        purchase_cost = money_sum(float(self.itemsTable.item(i)["values"][6]) for i in self.itemsTable.get_children())
        mrp_value = money_sum(float(self.itemsTable.item(i)["values"][7]) for i in self.itemsTable.get_children())
        try:
            consultation = to_money(self.consultation_amount.get() or 0)
        except ValueError:
            consultation = 0.0
        auto_collection = to_money(consultation + mrp_value)

        self.lbl_purchase_cost.set(f"₹ {purchase_cost:,.2f}")
        self.lbl_mrp_value.set(f"₹ {mrp_value:,.2f}")
        self.lbl_mrp_profit.set(f"₹ {to_money(mrp_value - purchase_cost):,.2f}")  # Medicine Margin Profit

        # Only overwrite the (possibly hand-typed) Amount Collected field
        # while auto-fill is still active - see _on_collection_edited().
        if self._auto_collection_active:
            self._set_collected_programmatically(auto_collection)
        self._refresh_gross_profit_only()

    # ------------------------------------------------------------
    # Save
    # ------------------------------------------------------------

    def save_visit(self):
        if self.selected_patient_id is None:
            ui_popups.show_error(self.frame, "Error", "Select or create a patient first")
            return
        if not self.pending_items and to_money(self.consultation_amount.get() or 0) <= 0:
            ui_popups.show_error(self.frame, "Error", "Add at least one item or a consultation amount")
            return
        try:
            consultation = to_money(self.consultation_amount.get() or 0)
        except ValueError:
            ui_popups.show_error(self.frame, "Error", "Invalid consultation amount")
            return
        try:
            collected = to_money(self.total_collected.get() or 0)
        except ValueError:
            ui_popups.show_error(self.frame, "Error", "Invalid Amount Collected")
            return

        try:
            visit_id, visit_no, bill_no = repo.add_visit(
                self.selected_patient_id, self.doctor.get().strip(), self.reason.get().strip(),
                consultation, self.pending_items, created_by=session.get_current_user(),
                total_collected=collected,
                auto_generate_bill=self.auto_generate_bill.get(),
                patient_name=self.selected_patient_name,
            )
            # Remember this visit BEFORE clear_all() wipes the entry
            # form's own selected_patient_name - the "Last Saved Visit"
            # panel is independent of the form and must survive it.
            self.last_saved_visit_id = visit_id
            self.last_saved_patient_name = self.selected_patient_name
            self.last_saved_bill_no = bill_no
            self._update_last_saved_panel(visit_no)

            if bill_no:
                ui_popups.show_info(self.frame, "Success", f"Visit Saved ({visit_no})\nPharmacy Bill Auto-Created ({bill_no})")
            else:
                ui_popups.show_info(self.frame, "Success", f"Visit Saved ({visit_no})")
            self.clear_all()
        except repo.InsufficientStockError as e:
            ui_popups.show_error(self.frame, "Insufficient Stock", str(e))
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))

    def _update_last_saved_panel(self, visit_no):
        """Refreshes the "Last Saved Visit" label + Bill Now button state
        to match self.last_saved_bill_no - called right after Save, and
        again after _bill_now() succeeds."""
        if self.last_saved_bill_no:
            self.last_saved_summary.set(
                f"Visit {visit_no} ({self.last_saved_patient_name}) - "
                f"Pharmacy Bill already created: {self.last_saved_bill_no}"
            )
            self.btn_bill_now.config(state="disabled")
        else:
            self.last_saved_summary.set(
                f"Visit {visit_no} ({self.last_saved_patient_name}) saved. "
                f"No Pharmacy Bill yet - click Bill Now when ready."
            )
            self.btn_bill_now.config(state="normal")

    def _bill_now(self):
        """Bills the most recently saved visit on demand - the after-
        the-fact counterpart to the auto_generate_bill checkbox, for
        when the doctor saved the visit without it (or wants to double-
        check before billing). See clinic_repository.generate_bill_for_visit()'s
        docstring: this never re-deducts stock (already deducted at
        Save) and refuses to bill the same visit twice."""
        if self.last_saved_visit_id is None:
            return
        try:
            bill_no = repo.generate_bill_for_visit(
                self.last_saved_visit_id, patient_name=self.last_saved_patient_name,
                created_by=session.get_current_user(),
            )
        except repo.AlreadyBilledError as e:
            ui_popups.show_warning(self.frame, "Already Billed", str(e))
            return
        except ValueError as e:
            ui_popups.show_error(self.frame, "Cannot Bill", str(e))
            return
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))
            return

        if bill_no:
            self.last_saved_bill_no = bill_no
            ui_popups.show_info(self.frame, "Success", f"Pharmacy Bill Created ({bill_no})")
        else:
            ui_popups.show_info(self.frame, 
                "Nothing To Bill",
                "This visit has no stock-tracked medicine/injection/consumable items "
                "to put on a Pharmacy Bill (consultation-only or ad-hoc-only visit)."
            )
        # Re-fetch the visit_no for the label (visit_no isn't stored on
        # self outside save_visit()'s local scope, so look it up fresh).
        header, _ = repo.get_visit(self.last_saved_visit_id)
        self._update_last_saved_panel(header[1] if header else self.last_saved_visit_id)

    def clear_all(self):
        self.selected_patient_id = None
        self.selected_patient_name = None
        self.patient_display.set("No patient selected")
        self.patient_search.set("")
        self.doctor.set("")
        self.reason.set("")
        self.consultation_amount.set("0")
        self.pending_items = []
        self.itemsTable.delete(*self.itemsTable.get_children())
        self.is_adhoc.set(False)
        self._toggle_adhoc()
        self._auto_collection_active = True
        self._refresh_totals()
