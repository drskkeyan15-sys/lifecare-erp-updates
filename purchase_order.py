"""
purchase_order.py
LifeCare Pharmacy ERP - Purchase Order (PO) generation.

Turns Smart Alerts' low-stock/reorder list into an actual document you
can act on and track, instead of just "copy to clipboard" (that stayed
useful for a quick WhatsApp message to a supplier; this is for when you
want a real record of what was ordered, from whom, and whether it's
arrived yet).

Reuses the same low-stock query and "last supplier used for this
medicine" lookup as stock_alerts_gui.py's load_low_stock() (see that
method's own comments for why it's a last-purchase lookup rather than a
stored preferred-supplier field - medicine_master has no such column).
Kept as its own copy here rather than importing from stock_alerts_gui
because that class builds the query result straight into its own
Treeview/instance state - there's no standalone function to import.
"""

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

# Aug 2026 repository-layer pass: all direct sqlite3 access has since
# moved into purchase_order_repository.py (see that module's docstring)
# - DB_NAME itself is no longer imported here, only by the repository.
import purchase_order_repository as repo
import audit_log
import session
import ui_style
import theme
import ui_popups

LOW_STOCK_THRESHOLD = 10  # matches stock_alerts_gui.py's own default
STATUS_OPTIONS = ["Draft", "Sent", "Received", "Cancelled"]


class PurchaseOrder:

    def __init__(self, frame, pending_items=None):
        self.frame = frame
        self._working_items = []  # list of (medicine, qty) for the PO being built
        self._suppliers = []
        self._po_groups = []  # cached rows behind the history table

        self.create_variables()
        self.create_ui()
        self.load_suppliers()
        self.load_history()

        # Predictive Inventory hand-off (Aug 2026) - Smart Alerts' new
        # "Reorder Predictions" tab (stock_alerts_gui.py) can send its
        # demand-based suggested items straight here via dashboard.py's
        # open_purchase_order_with_items(), instead of the pharmacist
        # re-typing each medicine/qty by hand. Optional and additive -
        # opening this screen normally from the sidebar still passes
        # nothing here and behaves exactly as before.
        if pending_items:
            for name, qty in pending_items:
                self._upsert_working_item(name, qty)
            self._refresh_item_table()
            ui_popups.show_info(self.frame, 
                "Items Loaded",
                f"{len(pending_items)} suggested item(s) from Smart Alerts' Reorder "
                "Predictions have been added below - pick a Supplier, adjust "
                "quantities if needed, then Save Purchase Order."
            )

    def create_variables(self):
        self.po_date = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.supplier = tk.StringVar()
        self.medicine = tk.StringVar()
        self.qty = tk.IntVar(value=1)
        self.note = tk.StringVar()

    def create_ui(self):
        tk.Label(
            self.frame, text="PURCHASE ORDER - Create & Track Supplier Orders",
            bg="#1565C0", fg="white", font=("Segoe UI", 18, "bold"), pady=10
        ).pack(fill="x")

        # ---- New PO form ----
        form = tk.LabelFrame(self.frame, text="Create New Purchase Order", font=("Segoe UI", 11, "bold"))
        form.pack(fill="x", padx=10, pady=10)

        tk.Label(form, text="Date").grid(row=0, column=0, padx=5, pady=6, sticky="w")
        tk.Entry(form, textvariable=self.po_date, width=15).grid(row=0, column=1, padx=5, pady=6, sticky="w")

        tk.Label(form, text="Supplier").grid(row=0, column=2, padx=5, pady=6, sticky="w")
        self.cmbSupplier = ttk.Combobox(form, textvariable=self.supplier, width=25)
        self.cmbSupplier.grid(row=0, column=3, padx=5, pady=6, sticky="w")

        tk.Button(
            form, text="Load Low-Stock Items for this Supplier", bg="#EF6C00", fg="white",
            width=32, command=self.load_low_stock_for_supplier
        ).grid(row=0, column=4, padx=10, pady=6, sticky="w")

        tk.Label(form, text="Medicine").grid(row=1, column=0, padx=5, pady=6, sticky="w")
        self.cmbMedicine = ttk.Combobox(form, textvariable=self.medicine, width=25, state="normal")
        self.cmbMedicine.grid(row=1, column=1, padx=5, pady=6, sticky="w")

        tk.Label(form, text="Qty").grid(row=1, column=2, padx=5, pady=6, sticky="w")
        self.txtQty = tk.Entry(form, textvariable=self.qty, width=10)
        self.txtQty.grid(row=1, column=3, padx=5, pady=6, sticky="w")
        self.txtQty.bind("<Return>", lambda e: self.add_item())

        tk.Button(form, text="Add Item", bg="#2E7D32", fg="white", width=12, command=self.add_item).grid(
            row=1, column=4, padx=10, pady=6, sticky="w"
        )

        # ERP-wide keyboard-nav pass (Aug 2026): this screen previously
        # had NO keyboard chain at all - Supplier/Medicine were static,
        # unfiltered dropdowns with no <<ComboboxSelected>>/<Return>
        # handling whatsoever, and Qty had no Enter-to-add. Now: typing
        # narrows both boxes live, selecting a Supplier (mouse or
        # keyboard) advances to Medicine, selecting/typing a Medicine
        # advances to Qty, and Enter on Qty adds the item and returns
        # focus to Medicine for the next one - matching every other
        # "add item" screen in the app (Billing, Purchase Entry).
        ui_style.bind_search_combo(
            self.cmbSupplier,
            on_filter=self._filter_supplier_dropdown,
            on_confirm=lambda e=None: bool(self.supplier.get().strip()),
            next_widget=self.cmbMedicine,
        )
        ui_style.bind_search_combo(
            self.cmbMedicine,
            on_filter=self._filter_medicine_dropdown,
            on_confirm=self._on_medicine_confirm,
            next_widget=self.txtQty,
        )

        # Supplier Price Comparison (Sep 2026) - once a Medicine is
        # resolved (Enter/Tab/click, same on_confirm trigger as above,
        # not on every keystroke), show which Supplier has historically
        # charged the least for it, right here, before the pharmacist
        # even picks a Supplier at the top of this form. See
        # purchase_order_repository.get_medicine_price_by_supplier()'s
        # docstring for why it's each Supplier's own latest price, not
        # an average across time.
        hint_frame = tk.Frame(form, bg=theme.SURFACE_FIELD)
        hint_frame.grid(row=2, column=0, columnspan=5, padx=5, pady=(0, 6), sticky="we")
        self.price_hint_label = tk.Label(
            hint_frame, text="", bg=theme.SURFACE_FIELD, fg=theme.TEXT_LABEL,
            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=760
        )
        self.price_hint_label.pack(side="left", padx=8, pady=4, fill="x", expand=True)
        self.btnUseBestSupplier = tk.Button(
            hint_frame, text="Use This Supplier", bg=theme.PRIMARY, fg="white",
            font=("Segoe UI", 9), relief="flat", bd=0, cursor="hand2",
            activebackground=theme.PRIMARY_HOVER, activeforeground="white",
            command=self._use_best_supplier
        )
        self._best_price_supplier = None
        # Not packed yet - only shown once _update_price_hint() actually
        # finds more than one Supplier to choose between (see there).

        tk.Label(form, text="Note").grid(row=3, column=0, padx=5, pady=6, sticky="w")
        tk.Entry(form, textvariable=self.note, width=60).grid(row=3, column=1, columnspan=3, padx=5, pady=6, sticky="w")

        # Working item list for the PO being built
        item_frame = tk.Frame(form)
        item_frame.grid(row=4, column=0, columnspan=5, padx=5, pady=(6, 10), sticky="we")

        cols = ("Medicine", "Qty")
        self.itemTable = ttk.Treeview(item_frame, columns=cols, show="headings", height=6, style="ERP.Treeview")
        self.itemTable.heading("Medicine", text="Medicine")
        self.itemTable.column("Medicine", width=320, anchor="w")
        self.itemTable.heading("Qty", text="Qty")
        self.itemTable.column("Qty", width=80, anchor="center")
        self.itemTable.pack(side="left", fill="x", expand=True)

        btns = tk.Frame(item_frame)
        btns.pack(side="left", padx=10)
        tk.Button(btns, text="Remove Selected", bg="#C62828", fg="white", width=15, command=self.remove_item).pack(pady=2, fill="x")
        tk.Button(btns, text="Clear All", width=15, command=self.clear_items).pack(pady=2, fill="x")
        tk.Button(
            btns, text="Save Purchase Order", bg="green", fg="white", font=("Segoe UI", 10, "bold"),
            command=self.save_po
        ).pack(pady=(10, 2), fill="x")

        # ---- History ----
        hist_frame = tk.LabelFrame(self.frame, text="Purchase Order History", font=("Segoe UI", 10, "bold"))
        hist_frame.pack(fill="both", expand=True, padx=10, pady=10)

        cols2 = ("PO No", "Date", "Supplier", "Items", "Status")
        self.historyTable = ttk.Treeview(hist_frame, columns=cols2, show="headings", height=10, style="ERP.Treeview")
        widths = {"PO No": 160, "Date": 100, "Supplier": 180, "Items": 70, "Status": 100}
        for c in cols2:
            self.historyTable.heading(c, text=c)
            self.historyTable.column(c, width=widths[c], anchor="w" if c in ("PO No", "Supplier") else "center")
        self.historyTable.pack(fill="both", expand=True, padx=5, pady=5)

        # Status row colors (Aug 2026) - this list used to show every PO
        # in the same plain black-on-white row regardless of status,
        # meaning "is this waiting on the supplier or already done"
        # could only be read by squinting at the Status column's text.
        # Same idea as the Expired/Low Stock row highlights already
        # added to Medicine Master/Purchase's item grid, but deliberately
        # softer pastel tones here rather than those screens' bold
        # red/orange alert colors - THIS list is a normal, expected mix
        # of every status on a healthy day (most rows end up Received),
        # not a short list of rare problem rows that need to visually
        # shout. "Draft" gets no tag at all (still being edited, nothing
        # to flag yet) and keeps the table's plain zebra striping.
        self.historyTable.tag_configure("po-sent", background="#FFF3E0")       # awaiting supplier
        self.historyTable.tag_configure("po-received", background="#E8F5E9")   # done, stock in hand
        self.historyTable.tag_configure("po-cancelled", background="#FFEBEE", foreground="#757575")  # dead, de-emphasized

        hbtns = tk.Frame(hist_frame)
        hbtns.pack(fill="x", padx=5, pady=(0, 5))
        tk.Button(hbtns, text="View Items", width=15, command=self.view_selected_po).pack(side="left", padx=5)
        tk.Button(hbtns, text="Mark as Sent", bg="#1565C0", fg="white", width=15, command=lambda: self.set_status("Sent")).pack(side="left", padx=5)
        tk.Button(hbtns, text="Mark as Received", bg="#2E7D32", fg="white", width=15, command=lambda: self.set_status("Received")).pack(side="left", padx=5)
        tk.Button(hbtns, text="Cancel PO", bg="#C62828", fg="white", width=15, command=lambda: self.set_status("Cancelled")).pack(side="left", padx=5)

    # ---------------- DATA ----------------

    def load_suppliers(self):
        self._suppliers = repo.list_supplier_names()
        self._medicine_names = repo.list_medicine_names()
        self.cmbSupplier["values"] = self._suppliers
        self.cmbMedicine["values"] = self._medicine_names

    def _filter_supplier_dropdown(self, typed_text):
        """bind_search_combo()'s on_filter for cmbSupplier - in-memory
        substring narrowing (this screen has no live DB-backed search of
        its own, unlike Purchase Entry's repo.search_medicine_names())."""
        typed = typed_text.lower()
        self.cmbSupplier["values"] = (
            self._suppliers if not typed
            else [n for n in self._suppliers if typed in n.lower()]
        )

    def _filter_medicine_dropdown(self, typed_text):
        """bind_search_combo()'s on_filter for cmbMedicine - same
        in-memory substring narrowing as Purchase Entry's
        on_medicine_keyrelease()."""
        typed = typed_text.lower()
        self.cmbMedicine["values"] = (
            self._medicine_names if not typed
            else [n for n in self._medicine_names if typed in n.lower()]
        )

    def _on_medicine_confirm(self, event=None):
        """bind_search_combo()'s on_confirm for cmbMedicine - resolves
        the typed/picked Medicine (same success/failure contract as the
        plain lambda this replaced) AND, on success, refreshes the
        Supplier Price Comparison hint below the form for that
        medicine."""
        name = self.medicine.get().strip()
        ok = bool(name)
        if ok:
            self._update_price_hint(name)
        else:
            self._clear_price_hint()
        return ok

    def _update_price_hint(self, name):
        rows = repo.get_medicine_price_by_supplier(name)
        self.btnUseBestSupplier.pack_forget()
        self._best_price_supplier = None

        if not rows:
            self.price_hint_label.config(
                text=f'"{name}" has no purchase price history yet - no Supplier to compare.'
            )
            return

        best_supplier, best_price, best_date = rows[0]
        text = f'"{name}" - Best Price: {best_supplier} @ ₹{best_price:.2f} (last bought {best_date})'

        if len(rows) > 1:
            others = ", ".join(f"{s} ₹{p:.2f}" for s, p, _d in rows[1:4])
            text += f"   |   Also bought from: {others}"
            self._best_price_supplier = best_supplier
            self.btnUseBestSupplier.pack(side="right", padx=8, pady=4)

        self.price_hint_label.config(text=text)

    def _clear_price_hint(self):
        self.price_hint_label.config(text="")
        self.btnUseBestSupplier.pack_forget()
        self._best_price_supplier = None

    def _use_best_supplier(self):
        """One-click fill of the top Supplier field from the price-hint
        row's cheapest match - saves re-typing/re-picking a name that's
        already right there on screen."""
        if self._best_price_supplier:
            self.supplier.set(self._best_price_supplier)

    def load_low_stock_for_supplier(self):
        """Same low-stock + last-supplier-used logic as Smart Alerts'
        load_low_stock() - see this module's docstring for why it's not
        just imported from there directly."""
        supplier_filter = self.supplier.get().strip()

        rows, last_supplier = repo.get_low_stock_with_last_supplier(LOW_STOCK_THRESHOLD)

        added = 0
        for name, stock, threshold in rows:
            supplier_for_med = last_supplier.get(name)
            if supplier_filter and supplier_for_med != supplier_filter:
                continue
            suggested_qty = max(threshold - stock, 1)
            self._upsert_working_item(name, suggested_qty)
            added += 1

        self._refresh_item_table()
        if added == 0:
            ui_popups.show_info(self.frame, 
                "No Matching Items",
                "No low-stock medicine found whose last purchase supplier matches "
                f'"{supplier_filter}".' if supplier_filter else
                "No low-stock medicines found right now."
            )
        else:
            ui_popups.show_info(self.frame, "Items Loaded", f"{added} low-stock item(s) added to this PO (suggested quantities - adjust as needed).")

    def _upsert_working_item(self, name, qty):
        for i, (existing_name, existing_qty) in enumerate(self._working_items):
            if existing_name == name:
                self._working_items[i] = (name, qty)
                return
        self._working_items.append((name, qty))

    def _refresh_item_table(self):
        self.itemTable.delete(*self.itemTable.get_children())
        for name, qty in self._working_items:
            self.itemTable.insert("", "end", values=(name, qty))

    def add_item(self):
        name = self.medicine.get().strip()
        if not name:
            ui_popups.show_error(self.frame, "Error", "Select a medicine.")
            return
        try:
            qty = int(self.qty.get())
        except (tk.TclError, ValueError):
            qty = 0
        if qty <= 0:
            ui_popups.show_error(self.frame, "Error", "Enter a quantity greater than zero.")
            return

        self._upsert_working_item(name, qty)
        self._refresh_item_table()
        self.medicine.set("")
        self.qty.set(1)
        self._clear_price_hint()
        # ERP-wide keyboard-nav pass (Aug 2026): return focus to Medicine
        # (this screen's "main search box") for continuous entry, same
        # as Billing/Purchase Entry re-focusing their own top field after
        # Add Item.
        self.cmbMedicine["values"] = self._medicine_names
        self.cmbMedicine.focus_set()

    def remove_item(self):
        selected = self.itemTable.selection()
        if not selected:
            ui_popups.show_warning(self.frame, "Select a Row", "Select an item to remove.")
            return
        name = self.itemTable.item(selected[0])["values"][0]
        self._working_items = [(n, q) for n, q in self._working_items if n != name]
        self._refresh_item_table()

    def clear_items(self):
        self._working_items = []
        self._refresh_item_table()

    def save_po(self):
        supplier = self.supplier.get().strip()
        date = self.po_date.get().strip()
        if not supplier:
            ui_popups.show_error(self.frame, "Error", "Select or type a Supplier.")
            return
        if not date:
            ui_popups.show_error(self.frame, "Error", "Enter a date.")
            return
        if not self._working_items:
            ui_popups.show_error(self.frame, "Error", "Add at least one item to this Purchase Order.")
            return

        note = self.note.get().strip()
        try:
            po_no = repo.save_purchase_order(date, supplier, self._working_items, note, session.get_current_user())
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))
            return

        audit_log.log_action(
            "Purchase Order", "Create",
            f"Created {po_no} for supplier '{supplier}' with {len(self._working_items)} item(s)"
        )

        ui_popups.show_info(self.frame, "Saved", f"Purchase Order {po_no} saved as Draft with {len(self._working_items)} item(s).")
        self.clear_items()
        self.note.set("")
        self.load_history()

    def load_history(self):
        self.historyTable.delete(*self.historyTable.get_children())
        self._po_groups = repo.list_po_groups()

        status_tag = {"Sent": "po-sent", "Received": "po-received", "Cancelled": "po-cancelled"}
        for po_no, po_date, supplier, status, item_count in self._po_groups:
            tags = (status_tag[status],) if status in status_tag else ()
            self.historyTable.insert("", "end", values=(po_no, po_date, supplier, item_count, status), tags=tags)

    def _get_selected_po_no(self):
        selected = self.historyTable.selection()
        if not selected:
            ui_popups.show_warning(self.frame, "Select a PO", "Select a Purchase Order from the history list first.")
            return None
        return self.historyTable.item(selected[0])["values"][0]

    def view_selected_po(self):
        po_no = self._get_selected_po_no()
        if not po_no:
            return

        rows = repo.get_po_items(po_no)

        win = tk.Toplevel(self.frame)
        win.title(f"Purchase Order {po_no}")
        ui_style.center_window(win, 500, 440, parent=self.frame.winfo_toplevel())
        # Esc key also closes this popup, same as the Close button.
        win.bind("<Escape>", lambda event: win.destroy())
        win.focus_force()

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring).
        body = ui_style.popup_header(win, po_no, icon="📋")

        cols = ("Medicine", "Qty")
        table = ttk.Treeview(body, columns=cols, show="headings", height=14)
        table.heading("Medicine", text="Medicine")
        table.column("Medicine", width=320, anchor="w")
        table.heading("Qty", text="Qty")
        table.column("Qty", width=80, anchor="center")
        table.pack(fill="both", expand=True, padx=10, pady=10)

        for medicine, qty, note, status in rows:
            table.insert("", "end", values=(medicine, qty))

        if rows and rows[0][2]:
            tk.Label(
                body, text=f"Note: {rows[0][2]}", bg=theme.SURFACE_WHITE, fg=theme.TEXT_PRIMARY,
                wraplength=460, justify="left",
            ).pack(padx=10, pady=(0, 10))

        ui_style.flat_button(body, "Close", theme.PRIMARY, win.destroy, width=15).pack(pady=(0, 10))

    def set_status(self, new_status):
        po_no = self._get_selected_po_no()
        if not po_no:
            return

        if not ui_popups.show_confirmation(self.frame, "Confirm", f'Mark {po_no} as "{new_status}"?'):
            return

        repo.update_po_status(po_no, new_status)

        audit_log.log_action("Purchase Order", "Status Change", f"{po_no} -> {new_status}")

        ui_popups.show_info(self.frame, "Updated", f"{po_no} marked as {new_status}.")
        self.load_history()
