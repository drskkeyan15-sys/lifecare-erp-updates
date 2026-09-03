import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta
from pricing_utils import get_unit_price, allocate_fifo
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer
)
from reportlab.lib.styles import getSampleStyleSheet
import tempfile
import subprocess
import os
import textwrap

try:
    import importlib
    _escpos_mod = importlib.import_module("escpos.printer")
    Usb = getattr(_escpos_mod, "Usb", None)
except Exception:
    Usb = None

# Aug 2026 repository-layer pass: all direct sqlite3 access has since
# moved into billing_repository.py (see that module's docstring) -
# DB_NAME itself is no longer imported here, only by the repository.
import billing_repository as repo
import generic_mapping
import ddi_checker
from icon_loader import get_icon
import prescription_scan_gui
from money import money_sum, to_money
import theme
import ui_style
import ui_popups


def _merge_dl_numbers(dl20, dl21):
    """
    "TN/DPI/20/00085" + "TN/DPI/21/00085" -> "TN/DPI/20/21/00085" for a
    compact thermal-receipt D.L. No. line (per user request - the two
    licenses share every segment except the "20"/"21" type code, no
    need to print the shared prefix/suffix twice). Only merges when both
    numbers actually have multiple "/"-separated segments (a real
    "STATE/TYPE/CODE/NUMBER"-style license) AND differ in exactly one of
    those segments; anything else (single-token numbers with no "/" at
    all, different segment counts, more than one differing segment)
    falls back to printing them as-is so two genuinely different or
    oddly-formatted license numbers never get silently concatenated into
    something wrong.
    """
    if dl20 and dl21:
        p20, p21 = dl20.split("/"), dl21.split("/")
        if len(p20) == len(p21) and len(p20) > 1:
            diff = [i for i in range(len(p20)) if p20[i] != p21[i]]
            if len(diff) == 1:
                i = diff[0]
                merged = p20[:]
                merged[i] = f"{p20[i]}/{p21[i]}"
                return "/".join(merged)
        return f"{dl20} , {dl21}"
    return dl20 or dl21 or ""


def _clean_doctor_name(raw):
    """
    "Dr.G.VIGNESH, M.B.B.S," -> "G.VIGNESH" for the thermal receipt's
    "Dr : NAME" line (per user request - drop the "Prescribed by"
    label, a leading "Dr."/"Dr" the cashier may have typed, and any
    qualification suffix after the first comma). Safe to run on an
    already-clean "G.VIGNESH" too - no leading Dr./no comma means it's
    returned unchanged.
    """
    name = raw.strip()
    for prefix in ("Dr.", "Dr ", "DR.", "DR "):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break
    name = name.split(",")[0].strip()
    return name


class Billing:

    # Quick Picks (fast counter billing) tuning - how far back to look at
    # sales history and how many top-selling medicines to show as one-click
    # buttons. Class attributes (not a module CONST) so they're easy to find
    # right next to the class that uses them.
    QUICK_PICK_DAYS = 90
    QUICK_PICK_COUNT = 20

    def __init__(self, frame):
        self.frame = frame
        self._shop_default_doctor = ""
        self.create_variables()
        self.create_ui()
        self.load_medicines()
        self.load_quick_picks()
        self.generate_bill_no()
        self._apply_default_doctor()
        self._bind_shortcuts()
        # Unbind the moment this screen is torn down (dashboard.py
        # destroys the container Frame when the user switches modules) -
        # otherwise F1/F2/etc pressed on a completely different screen
        # would still try to call save_bill()/new_bill() on this
        # already-gone Billing instance. Same cleanup-on-<Destroy> idea
        # dashboard.py already uses for its mousewheel bind_all.
        self.frame.bind("<Destroy>", self._unbind_shortcuts)

    # =====================================
    # STATIC HELPER FOR GENERIC FORMATTING
    # =====================================

    @staticmethod
    def get_formatted_billing_item(medicine_name):
        """
        பில்லிங் பிரிண்டில் பிராண்ட் பெயருடன் ஜெனரிக் மற்றும் டோசேஜை 
        பிராக்கெட்டுக்குள் இணைத்து வழங்க உதவுகிறது.
        """
        row = repo.get_medicine_generic(medicine_name)

        if row and row[0]:
            generic_composition = row[0].strip()
            formatted_text = f"{medicine_name} ({generic_composition})"
        else:
            formatted_text = medicine_name
            
        return formatted_text

    # =====================================
    # VARIABLES
    # =====================================

    def create_variables(self):
        self.bill_no = tk.StringVar()
        self.bill_date = tk.StringVar(value=datetime.now().strftime("%d-%m-%Y"))
        self.customer = tk.StringVar()
        # Doctor referring/treating this bill's customer - captured at
        # billing time so Reports' Doctor/Patient-wise Sales report has
        # something to group by. Auto-suggested from Customer Master's
        # own `doctor` field when the typed customer name matches an
        # existing record (see _autofill_doctor()), but always editable -
        # walk-in customers or a different doctor this visit shouldn't be
        # blocked by whatever's on file.
        self.doctor_name = tk.StringVar()
        # Patient address - captured alongside Doctor/Patient(customer) so
        # the H1 Register report has a full drug-inspector-ready record
        # (Schedule H1 rules expect doctor, patient AND address). Optional,
        # always editable, same treatment as Doctor - a walk-in customer's
        # address isn't always available or needed for every sale.
        self.patient_address = tk.StringVar()
        # Loyalty discount % - auto-suggested from Customer Master's own
        # discount_percent field when the typed customer matches an
        # existing record (see _autofill_discount()), always editable
        # per-bill same as Doctor. Applied to subtotal in
        # calculate_total(); the computed rupee amount (not the percent)
        # is what actually gets saved to sales.discount, since that's
        # what reports.py's report table already expects to display.
        self.discount_percent = tk.DoubleVar(value=0.0)
        self.discount_amt = tk.DoubleVar(value=0.0)
        self.barcode = tk.StringVar()
        self.medicine = tk.StringVar()
        self.batch = tk.StringVar()
        self.stock = tk.IntVar()
        self.price = tk.DoubleVar()
        self.qty = tk.IntVar(value=1)
        self.gst = tk.DoubleVar(value=0)
        self.grand_total = tk.DoubleVar(value=0)
        self.subtotal = tk.DoubleVar(value=0)
        self._medicine_names = []  # கேச்சிங் லிஸ்ட்
        self._substitute_for = None  # currently out-of-stock medicine the "View Substitutes" button applies to

        # Payment Mode + Cash Received/Balance(Change) - Cash mode lets
        # the cashier type what the customer handed over and see the
        # change due; Card/UPI/Wallet auto-fill received = grand total
        # (digital payments settle exact, there's no change to compute).
        self.payment_mode = tk.StringVar(value="Cash")
        self.received_amt = tk.DoubleVar(value=0.0)
        self.balance_display = tk.StringVar(value="₹ 0.00")

    # =====================================
    # USER INTERFACE
    # =====================================

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="BILLING",
            bg=theme.PRIMARY,
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        # ---------------- Customer ----------------
        customer_frame = tk.LabelFrame(
            self.frame,
            text="Customer Details",
            font=("Segoe UI", 10, "bold")
        )
        customer_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(customer_frame, text="Bill No").grid(row=0, column=0, padx=5, pady=5)
        tk.Entry(customer_frame, textvariable=self.bill_no, width=18, state="readonly", takefocus=0).grid(row=0, column=1)

        tk.Label(customer_frame, text="Date").grid(row=0, column=2, padx=5)
        tk.Entry(customer_frame, textvariable=self.bill_date, width=15, state="readonly", takefocus=0).grid(row=0, column=3)

        tk.Label(customer_frame, text="Customer").grid(row=0, column=4, padx=5)
        customer_entry = tk.Entry(customer_frame, textvariable=self.customer, width=35)
        customer_entry.grid(row=0, column=5, padx=5)
        customer_entry.bind("<FocusOut>", self._autofill_doctor)

        tk.Label(customer_frame, text="Doctor").grid(row=0, column=6, padx=5)
        tk.Entry(customer_frame, textvariable=self.doctor_name, width=25).grid(row=0, column=7, padx=5)

        tk.Label(customer_frame, text="Discount %").grid(row=1, column=4, padx=5, pady=(0, 5))
        discount_entry = tk.Entry(customer_frame, textvariable=self.discount_percent, width=10)
        discount_entry.grid(row=1, column=5, padx=5, pady=(0, 5), sticky="w")
        discount_entry.bind("<KeyRelease>", lambda e: self.calculate_total())
        customer_entry.bind("<FocusOut>", self._autofill_discount, add="+")

        tk.Label(customer_frame, text="Address").grid(row=1, column=6, padx=5, pady=(0, 5))
        tk.Entry(customer_frame, textvariable=self.patient_address, width=25).grid(row=1, column=7, padx=5, pady=(0, 5))

        # ---------------- Medicine ----------------
        med = tk.LabelFrame(
            self.frame,
            text="Medicine Entry",
            font=("Segoe UI", 10, "bold")
        )
        med.pack(fill="x", padx=10, pady=10)

        tk.Label(med, text="Scan Barcode").grid(row=0, column=0, padx=5, pady=5)
        self.txtBarcode = tk.Entry(med, textvariable=self.barcode, width=20)
        self.txtBarcode.grid(row=0, column=1, sticky="w")
        self.txtBarcode.bind("<Return>", self.scan_barcode)

        tk.Label(med, text="Medicine").grid(row=1, column=0, padx=5, pady=5)

        self.cmbMedicine = ttk.Combobox(
            med,
            textvariable=self.medicine,
            width=35,
            state="normal"
        )
        self.cmbMedicine.grid(row=1, column=1)

        tk.Label(med, text="Batch (earliest)").grid(row=1, column=2)
        tk.Entry(med, textvariable=self.batch, width=15, state="readonly", takefocus=0).grid(row=1, column=3)

        tk.Label(med, text="Stock (all batches)").grid(row=1, column=4)
        tk.Entry(med, textvariable=self.stock, width=10, state="readonly", takefocus=0).grid(row=1, column=5)

        # Hidden by default - only shown when the selected medicine is a
        # REAL, recognised name (in self._medicine_names) with genuinely
        # zero usable stock. Deliberately not an auto-popup: get_medicine()
        # also fires on <FocusOut>, which happens on every partially-typed
        # search too, so popping a modal on every keystroke would make
        # billing unusable. This button lets the pharmacist check
        # substitutes only when they choose to, without breaking the
        # scan-and-go keyboard flow.
        self.btnSubstitutes = tk.Button(
            med, text="⚠ View Substitutes", bg=theme.ACCENT_SUBSTITUTE, fg="white", width=20,
            command=self.show_substitutes
        )
        self.btnSubstitutes.grid(row=1, column=6, padx=5)
        self.btnSubstitutes.grid_remove()

        tk.Label(med, text="Unit Price").grid(row=2, column=0)
        tk.Entry(med, textvariable=self.price, width=15, state="readonly", takefocus=0).grid(row=2, column=1)

        tk.Label(med, text="Qty").grid(row=2, column=2)
        self.txtQty = tk.Entry(med, textvariable=self.qty, width=10)
        self.txtQty.grid(row=2, column=3)
        self.txtQty.bind("<Return>", lambda e: self.add_item())

        # ERP-wide keyboard-nav pass (Aug 2026): typing, Enter, Tab-away
        # AND a mouse click on a suggestion now all resolve through the
        # same _confirm_medicine() path and advance to Qty together -
        # previously only the Enter key advanced focus, so picking a
        # medicine with the mouse silently left the pharmacist stuck in
        # the medicine box. See ui_style.bind_search_combo()'s docstring.
        # (Wired here, AFTER self.txtQty is created above - bind_search_combo
        # needs the real Entry widget as next_widget, not just its name.)
        ui_style.bind_search_combo(
            self.cmbMedicine,
            on_filter=lambda text: self.search_medicine(),
            on_confirm=self._confirm_medicine,
            next_widget=self.txtQty,
        )

        tk.Button(
            med,
            text="Add Item (F5)",
            bg="green",
            fg="white",
            width=15,
            command=self.add_item
        ).grid(row=2, column=5, padx=5)

        tk.Button(
            med,
            # No fixed width - "Clear Selection (Esc)" is long enough that
            # a guessed character-count width (was 15, then 20) still
            # clipped the closing ")" on the user's real machine/font.
            # Letting the button auto-size to its own text is the only
            # way to guarantee the full label is visible on any font/DPI.
            text="Clear Selection (Esc)",
            bg=theme.ACCENT_NEUTRAL,
            fg="white",
            padx=10,
            command=self.clear_fields_and_reset_dropdown
        ).grid(row=2, column=6, padx=5)

        self.txtBarcode.focus_set()

        # ---------------- Quick Picks (fast counter billing) ----------------
        # One-click add buttons for this shop's own top-selling medicines -
        # saves searching/typing entirely for the 15-20 items that make up
        # most day-to-day counter sales (OTC painkillers, antacids, cold
        # syrups etc). Recomputed once per Billing screen open, not after
        # every single bill - a full scan of sales_items is cheap once, but
        # pointless to repeat on every Save Bill; the Refresh button covers
        # anyone who wants the ranking updated mid-session (e.g. after a
        # long shift).
        quick_frame = tk.LabelFrame(
            self.frame,
            text=f"Quick Picks - Top Sellers (last {self.QUICK_PICK_DAYS} days)",
            font=("Segoe UI", 10, "bold")
        )
        quick_frame.pack(fill="x", padx=10, pady=(0, 10))

        tk.Button(
            quick_frame, text=" Refresh", image=get_icon("refresh"), compound="left",
            bg=theme.ACCENT_NEUTRAL, fg="white", padx=14, pady=4,
            command=self.load_quick_picks, takefocus=0
        ).pack(side="right", padx=5, pady=5, anchor="n")

        # Photo -> review-grid -> add-to-bill (see prescription_scan_gui.py's
        # module docstring). Placed next to Refresh as another "helper
        # action" for this row, not tied to the single-item entry fields
        # above it.
        tk.Button(
            quick_frame, text="\U0001F4F7 Scan Prescription", bg=theme.ACCENT_PRESCRIPTION, fg="white",
            padx=14, pady=4, command=self.open_prescription_scanner, takefocus=0
        ).pack(side="right", padx=5, pady=5, anchor="n")

        self._quickPickButtonsFrame = tk.Frame(quick_frame)
        self._quickPickButtonsFrame.pack(side="left", fill="x", expand=True, padx=5, pady=5)

        # ---------------- Prescription (Schedule H1) warning ----------------
        # Hidden by default (like btnSubstitutes above), shown/refreshed by
        # _refresh_rx_warning() whenever the bill contents change - see
        # calculate_total(), which every add/remove/new-bill path already
        # calls, so hooking the refresh there covers all of them for free.
        self.rxWarningLabel = tk.Label(
            self.frame,
            text="",
            bg=theme.WARNING_BANNER_BG,
            fg=theme.WARNING_BANNER_FG,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
            justify="left",
            wraplength=900,
            padx=10,
            pady=6
        )
        self.rxWarningLabel.pack(fill="x", padx=10, pady=(0, 5))
        self.rxWarningLabel.pack_forget()

        # ---------------- Bill Table ----------------
        table_frame = tk.Frame(self.frame)
        table_frame.pack(fill="both", expand=True, padx=10, pady=10)
        # Kept so _refresh_rx_warning() can re-pack the warning label
        # ABOVE the table (pack_forget() doesn't remember position like
        # grid_remove() does - a plain re-.pack() would otherwise append
        # it at the bottom of the whole screen instead of back here).
        self._billTableFrame = table_frame

        columns = ("Medicine", "Batch", "Qty", "Price", "Total")
        self.billTable = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=12,
            style="ERP.Treeview"
        )

        for c in columns:
            self.billTable.heading(c, text=c)
            self.billTable.column(c, width=120, anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.billTable.yview)
        self.billTable.configure(yscrollcommand=scrollbar.set)
        self.billTable.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ---------------- Totals ----------------
        total_frame = tk.Frame(self.frame)
        total_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(total_frame, text="Subtotal").grid(row=0, column=0)
        self.lblSubtotal = tk.Label(total_frame, text="₹ 0.00", font=("Segoe UI", 11, "bold"))
        self.lblSubtotal.grid(row=0, column=1, padx=20)

        tk.Label(total_frame, text="GST").grid(row=0, column=2)
        self.lblGST = tk.Label(total_frame, text="₹ 0.00", font=("Segoe UI", 11, "bold"))
        self.lblGST.grid(row=0, column=3, padx=20)

        tk.Label(total_frame, text="Discount").grid(row=0, column=4)
        self.lblDiscount = tk.Label(total_frame, text="₹ 0.00", fg=theme.STATUS_DANGER, font=("Segoe UI", 11, "bold"))
        self.lblDiscount.grid(row=0, column=5, padx=20)

        tk.Label(total_frame, text="Grand Total").grid(row=0, column=6)
        self.lblGrand = tk.Label(total_frame, text="₹ 0.00", fg="blue", font=("Segoe UI", 12, "bold"))
        self.lblGrand.grid(row=0, column=7, padx=20)

        # ---------------- Payment ----------------
        payment_frame = tk.LabelFrame(
            self.frame,
            text="Payment",
            font=("Segoe UI", 10, "bold")
        )
        payment_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(payment_frame, text="Payment Mode").grid(row=0, column=0, padx=5, pady=5)
        self.cmbPaymentMode = ttk.Combobox(
            payment_frame,
            textvariable=self.payment_mode,
            values=("Cash", "Card", "UPI", "Wallet"),
            width=12,
            state="readonly"
        )
        self.cmbPaymentMode.grid(row=0, column=1, padx=5)
        self.cmbPaymentMode.bind("<<ComboboxSelected>>", self.on_payment_mode_change)

        tk.Label(payment_frame, text="Received Amt (₹)").grid(row=0, column=2, padx=5)
        self.txtReceived = tk.Entry(payment_frame, textvariable=self.received_amt, width=12)
        self.txtReceived.grid(row=0, column=3, padx=5)
        self.txtReceived.bind("<KeyRelease>", self.calculate_balance)

        tk.Label(payment_frame, text="Balance / Change").grid(row=0, column=4, padx=5)
        self.lblBalance = tk.Label(payment_frame, textvariable=self.balance_display, font=("Segoe UI", 11, "bold"))
        self.lblBalance.grid(row=0, column=5, padx=20)

        # ---------------- Buttons ----------------
        bottom = tk.Frame(self.frame)
        bottom.pack(fill="x", padx=10, pady=10)

        tk.Button(bottom, text="Save Bill (F1)", bg="green", fg="white", width=15, command=self.save_bill).pack(side="left", padx=5)
        tk.Button(bottom, text="Print Bill (F4)", bg=theme.ACCENT_PRINT, fg="white", font=("Segoe UI", 10, "bold"), width=15, command=self.print_thermal_bill).pack(side="right", padx=5)
        tk.Button(bottom, text="New Bill (F2)", bg="blue", fg="white", width=15, command=self.new_bill).pack(side="left", padx=5)
        tk.Button(bottom, text="Remove Item (F3)", bg="red", fg="white", width=15, command=self.remove_item).pack(side="left", padx=5)

    def load_medicines(self):
        self._medicine_names = repo.list_medicine_names()
        self.cmbMedicine["values"] = self._medicine_names

    def load_quick_picks(self):
        """
        Recomputes the Quick Picks button list - top QUICK_PICK_COUNT
        medicines by total quantity sold in the last QUICK_PICK_DAYS days.
        sales.bill_date is stored as free-text "YYYY-MM-DD" (save_bill()
        below hardcodes datetime.now().strftime("%Y-%m-%d") on INSERT,
        regardless of what self.bill_date's DD-MM-YYYY display StringVar
        shows) - confirmed against live data. Same manual strptime
        approach as reports.py's slow_moving_report()/expiry_report():
        parsed by hand rather than trusted via SQL date-range filtering
        on the raw column. A fresh install with no sales yet just gets an
        empty/hint panel, never an error.
        """
        rows = repo.get_sales_items_with_dates()

        cutoff = datetime.now() - timedelta(days=self.QUICK_PICK_DAYS)
        totals = {}
        for medicine, qty, bill_date in rows:
            try:
                dt = datetime.strptime(bill_date, "%Y-%m-%d")
            except Exception:
                continue
            if dt < cutoff:
                continue
            totals[medicine] = totals.get(medicine, 0) + (qty or 0)

        ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
        self._quick_pick_names = [name for name, _ in ranked[:self.QUICK_PICK_COUNT]]
        self._build_quick_pick_buttons()

    def _build_quick_pick_buttons(self):
        for child in self._quickPickButtonsFrame.winfo_children():
            child.destroy()

        if not self._quick_pick_names:
            tk.Label(
                self._quickPickButtonsFrame,
                text="No sales yet - Quick Picks will appear here after your first few bills.",
                fg=theme.TEXT_MUTED_ALT
            ).pack(anchor="w")
            return

        COLUMNS = 5
        for idx, name in enumerate(self._quick_pick_names):
            row, col = divmod(idx, COLUMNS)
            label = name if len(name) <= 18 else name[:16] + "…"
            tk.Button(
                self._quickPickButtonsFrame,
                text=label,
                width=18,
                bg=theme.QUICK_PICK_BG,
                activebackground=theme.TABLE_SELECT_BG,
                command=lambda n=name: self._quick_pick_add(n),
                # takefocus=0 - these are created AFTER every other widget
                # on this screen (load_quick_picks() runs post-create_ui()
                # in __init__), which put up to QUICK_PICK_COUNT (20)
                # mouse-click shortcuts directly in the Tab chain between
                # "Clear Selection" and Payment Mode - a cashier tabbing
                # through the form to reach Save/Print had to tab past
                # every single Quick Pick button first. These are
                # deliberately one-click-only (see _quick_pick_add) - Tab
                # now skips straight from Add Item/Clear Selection to
                # Payment Mode instead.
                takefocus=0
            ).grid(row=row, column=col, padx=3, pady=3, sticky="w")

    def _quick_pick_add(self, name):
        """
        One-click add from the Quick Picks panel - runs the exact same
        FEFO batch/stock/price lookup as the dropdown (get_medicine()) and
        adds 1 unit via the normal add_item() path. Out-of-stock/expired
        medicines show the same warning as manual entry instead of
        silently failing - a Quick Pick button doesn't bypass any check
        add_item() already does.
        """
        self.cmbMedicine.set(name)
        self.medicine.set(name)
        self.get_medicine()
        if self.stock.get() <= 0:
            return
        self.qty.set(1)
        self.add_item()

    def get_fifo_batches(self, name):
        rows = repo.get_batches_in_stock(name)

        today = datetime.today().replace(day=1)
        batches = []
        for batch, stock, sale, pack_size_raw, expiry in rows:
            unit_price = get_unit_price(sale, pack_size_raw)

            expiry_dt = None
            is_expired = False
            if expiry:
                try:
                    expiry_dt = datetime.strptime(expiry, "%m/%y").replace(day=1)
                    is_expired = expiry_dt < today
                except Exception:
                    expiry_dt = None

            batches.append({
                "batch": batch, "stock": int(stock or 0), "price": unit_price,
                "expiry": expiry, "expiry_dt": expiry_dt, "expired": is_expired
            })

        batches.sort(key=lambda b: (b["expiry_dt"] is None, b["expiry_dt"] or datetime.max))
        return batches

    def get_medicine(self, event=None):
        """Returns True when a real, usable (non-expired, in-stock)
        batch was resolved for the current medicine text, False
        otherwise - used by _confirm_medicine() below (bind_search_combo's
        on_confirm) so a mistyped/unrecognized/expired/out-of-stock name
        never auto-advances focus to Qty the way a real match does."""
        if self.medicine.get().strip() == "":
            self._set_substitute_hint(None)
            return False

        batches = self.get_fifo_batches(self.medicine.get())
        usable = [b for b in batches if not b["expired"]]

        if not usable:
            self.batch.set("")
            self.stock.set(0)
            self.price.set(0)
            if batches:
                ui_popups.show_warning(self.frame, "Expired", f"All available stock of {self.medicine.get()} has expired.")
            # Only offer substitutes for a name that's a real, recognised
            # medicine (exists in medicine_master) - see the button's
            # creation comment for why this guard matters.
            if self.medicine.get() in self._medicine_names:
                self._set_substitute_hint(self.medicine.get())
            else:
                self._set_substitute_hint(None)
            return False

        self._set_substitute_hint(None)
        earliest = usable[0]
        self.batch.set(earliest["batch"])
        self.stock.set(sum(b["stock"] for b in usable))
        self.price.set(earliest["price"])

        if self.stock.get() <= 10:
            ui_popups.show_warning(self.frame, "Low Stock", f"Only {self.stock.get()} unit(s) left in stock.")
        return True

    def _set_substitute_hint(self, name):
        """Shows/hides the "View Substitutes" button for the given
        out-of-stock medicine name (None hides it)."""
        self._substitute_for = name
        if name:
            self.btnSubstitutes.grid()
        else:
            self.btnSubstitutes.grid_remove()

    def show_substitutes(self):
        if not self._substitute_for:
            return

        row = repo.get_medicine_generic(self._substitute_for)

        generic_text = (row[0] or "").strip() if row else ""
        if not generic_text:
            ui_popups.show_info(self.frame, 
                "No Composition Info",
                f'"{self._substitute_for}" has no generic/composition saved, '
                "so substitutes can't be suggested.\n\n"
                "Add its composition in Medicine Master first."
            )
            return

        generic_mapping.show_substitute_selector(
            self.frame, generic_text, exclude_name=self._substitute_for,
            on_select=self._pick_substitute, in_stock_only=True
        )

    def _pick_substitute(self, name):
        """Called when the pharmacist double-clicks a substitute in the
        popup - swaps the medicine field to the chosen brand and re-runs
        the normal stock/price lookup, same as if they'd selected it
        from the dropdown themselves."""
        self.cmbMedicine.set(name)
        self.medicine.set(name)
        self.get_medicine()
        self.txtQty.focus_set()
        self.txtQty.select_range(0, tk.END)

    def add_item(self):
        if self.medicine.get().strip() == "":
            ui_popups.show_warning(self.frame, "Warning", "Select Medicine")
            return

        qty_needed = self.qty.get()
        if qty_needed <= 0:
            ui_popups.show_warning(self.frame, "Warning", "Invalid Quantity")
            return

        batches = self.get_fifo_batches(self.medicine.get())
        usable = [b for b in batches if not b["expired"]]

        if not usable:
            if batches:
                ui_popups.show_warning(self.frame, "Expired", f"All available stock of {self.medicine.get()} has expired.")
            else:
                ui_popups.show_error(self.frame, "Error", f'"{self.medicine.get()}" is not a recognised medicine or is out of stock.')
            return

        total_available = sum(b["stock"] for b in usable)
        if qty_needed > total_available:
            ui_popups.show_warning(self.frame, "Insufficient Stock", f"Only {total_available} unit(s) available across all batches.")
            return

        # Allocation math (which batch, how much of each) lives in
        # pricing_utils.allocate_fifo() so it's covered by a unit test
        # independent of this Treeview - see test_billing_fifo.py.
        allocations = allocate_fifo(usable, qty_needed)
        batches_used = len(allocations)
        for alloc in allocations:
            b_batch, take, price, total = alloc["batch"], alloc["qty"], alloc["price"], alloc["total"]

            merged = False
            for item in self.billTable.get_children():
                values = self.billTable.item(item)["values"]
                if str(values[0]) == self.medicine.get() and str(values[1]) == b_batch:
                    existing_qty = int(values[2])
                    new_qty = existing_qty + take
                    new_total = to_money(new_qty * price)
                    self.billTable.item(item, values=(values[0], values[1], new_qty, price, new_total))
                    merged = True
                    break

            if not merged:
                self.billTable.insert("", "end", values=(self.medicine.get(), b_batch, take, price, total))

        if batches_used > 1:
            ui_popups.show_info(self.frame, "FIFO Split", f"Stock split across {batches_used} batches (earliest expiry sold first).")

        # Drug-Drug Interaction check (Aug 2026 framework - see
        # ddi_checker.py's own module docstring for the "reference only,
        # not comprehensive" caveat). Checked here, right as the item
        # enters the cart, rather than only at Save - so the pharmacist
        # sees the warning at the moment a risky combination actually
        # forms, and can act (remove the item) before it's ever billed.
        self._check_ddi_for_new_item(self.medicine.get())

        self.calculate_total()
        self.clear_fields_and_reset_dropdown()

    def _check_ddi_for_new_item(self, new_medicine):
        """Cross-checks `new_medicine` (just added to the cart) against
        every OTHER distinct medicine already in the bill for a known
        severe interaction (see ddi_checker.py). Shows a blocking
        warning popup requiring the pharmacist to explicitly acknowledge
        before the item stays in the cart - choosing "Remove Item"
        undoes the add. This is a FRAMEWORK/DEMO safety net built on a
        small, explicitly non-comprehensive reference dataset, NOT a
        substitute for pharmacist/doctor judgment - see ddi_checker.py's
        module docstring. Any failure here (DB error, missing table)
        never blocks normal billing - it just skips the check silently."""
        other_medicines = {
            self.billTable.item(i)["values"][0]
            for i in self.billTable.get_children()
            if self.billTable.item(i)["values"][0] != new_medicine
        }
        if not other_medicines:
            return

        try:
            findings = ddi_checker.check_cart_interactions([new_medicine] + list(other_medicines))
        except Exception:
            return

        relevant = [f for f in findings if new_medicine in (f[0], f[1])]
        if not relevant:
            return

        self._show_ddi_warning(new_medicine, relevant)

    def _show_ddi_warning(self, new_medicine, findings):
        win = tk.Toplevel(self.frame)
        win.title("Severe Drug Interaction Warning")
        win.grab_set()
        win.transient(self.frame.winfo_toplevel())

        # Aug 2026 visual refresh: this dialog already had its own
        # colored header (danger-red, matching ui_popups.py's "warning/
        # error" kind coloring) - now built through the same shared
        # ui_style.popup_header() every other hand-built popup app-wide
        # uses, and the body/rows explicitly white (SURFACE_WHITE)
        # instead of the system default grey they'd fall back to.
        outer = ui_style.popup_header(win, "Severe Drug Interaction Warning", bg=theme.STATUS_DANGER, icon="⚠")
        body = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=16, pady=12)
        body.pack(fill="both", expand=True)

        tk.Label(
            body, bg=theme.SURFACE_WHITE, justify="left", wraplength=460, font=("Segoe UI", 10),
            text=f'"{new_medicine}" may interact with (an)other item(s) already in this bill:'
        ).pack(anchor="w")

        for med_a, med_b, severity, description in findings:
            other = med_b if med_a == new_medicine else med_a
            row = tk.Frame(body, bg=theme.SURFACE_WHITE, pady=6)
            row.pack(fill="x")
            tk.Label(
                row, text=f"{new_medicine}  +  {other}  [{severity}]", bg=theme.SURFACE_WHITE,
                font=("Segoe UI", 10, "bold"), fg=theme.STATUS_DANGER, anchor="w", justify="left"
            ).pack(anchor="w")
            tk.Label(
                row, text=description, bg=theme.SURFACE_WHITE, wraplength=460, justify="left", anchor="w", font=("Segoe UI", 9)
            ).pack(anchor="w")

        tk.Label(
            body, bg=theme.SURFACE_WHITE, text=(
                "Reference-only list (not a comprehensive/verified drug database) - "
                "use pharmacist judgment. Verify with the prescribing doctor if unsure."
            ),
            wraplength=460, justify="left", fg=theme.TEXT_MUTED, font=("Segoe UI", 8, "italic")
        ).pack(anchor="w", pady=(10, 0))

        ack_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            body, bg=theme.SURFACE_WHITE, activebackground=theme.SURFACE_WHITE,
            text="I have reviewed this interaction and confirm this combination is intended.",
            variable=ack_var, wraplength=440, justify="left", anchor="w"
        ).pack(anchor="w", pady=(12, 0))

        def _remove_and_close():
            for i in list(self.billTable.get_children()):
                if self.billTable.item(i)["values"][0] == new_medicine:
                    self.billTable.delete(i)
            win.destroy()
            self.calculate_total()

        def _keep():
            if not ack_var.get():
                ui_popups.show_warning(win, 
                    "Acknowledgment Required",
                    "Please tick the checkbox to confirm you've reviewed this interaction, "
                    "or choose 'Remove Item' instead."
                )
                return
            win.destroy()

        btns = tk.Frame(body, bg=theme.SURFACE_WHITE)
        btns.pack(fill="x", pady=(16, 0))
        ui_style.flat_button(btns, "Remove Item", theme.STATUS_DANGER, _remove_and_close, width=15).pack(side="left")
        ui_style.flat_button(btns, "Keep Item (Reviewed)", theme.STATUS_SUCCESS, _keep, width=20).pack(side="right")

        # Sized to its own real required content, same "build widgets
        # first, size window after" convention as the Factory Reset
        # dialog fix elsewhere in this app (never guess a fixed geometry
        # for text that must fully render).
        win.update_idletasks()
        req_w = max(500, win.winfo_reqwidth())
        req_h = win.winfo_reqheight() + 20
        ui_style.center_window(win, req_w, req_h, parent=self.frame.winfo_toplevel())
        win.wait_window()

    def open_prescription_scanner(self):
        """Opens the photo -> OCR -> review-grid dialog (see
        prescription_scan_gui.py's module docstring). Nothing from that
        dialog ever touches the bill directly - confirmed rows come back
        through _add_scanned_items() below, which reuses add_item()
        unmodified for the actual work."""
        prescription_scan_gui.PrescriptionScanDialog(
            self.frame.winfo_toplevel(), on_add_items=self._add_scanned_items
        )

    def _add_scanned_items(self, items):
        """Callback from PrescriptionScanDialog - `items` is a list of
        (medicine_name, qty) tuples the pharmacist explicitly ticked
        "Include" on after reviewing the OCR read (never auto-added).
        Feeds each one through the EXACT SAME entry point manual
        counter billing uses (self.medicine/self.qty -> get_medicine()
        -> add_item()), so FIFO batch-splitting, stock validation, and
        the Schedule H1 prescription warning all apply automatically -
        none of that logic is duplicated here. add_item() already shows
        its own error popup per row (unknown medicine / out of stock /
        insufficient stock), same as if the pharmacist had typed and
        added each item by hand one at a time."""
        for name, qty in items:
            self.medicine.set(name)
            self.get_medicine()
            self.qty.set(qty)
            self.add_item()

    def scan_barcode(self, event=None):
        code = self.barcode.get().strip()
        if not code:
            return

        row = repo.get_medicine_name_by_barcode(code)

        self.barcode.set("")

        if not row:
            ui_popups.show_error(self.frame, "Barcode Not Found", f"No medicine registered with barcode: {code}")
            self.txtBarcode.focus_set()
            return

        self.medicine.set(row[0])
        self.cmbMedicine.set(row[0])
        self.get_medicine()
        self.qty.set(1)
        self.txtQty.focus_set()
        self.txtQty.select_range(0, tk.END)

    def _confirm_medicine(self, event=None):
        """bind_search_combo()'s on_confirm for self.cmbMedicine - resolves
        the typed/selected medicine and reports success/failure so Enter,
        Tab-away, and a mouse-click selection all advance to Qty ONLY on a
        real match (see get_medicine()'s True/False return above)."""
        return self.get_medicine(event)

    def clear_fields_and_reset_dropdown(self):
        self.cmbMedicine.set("")
        self.medicine.set("")
        self.batch.set("")
        self.stock.set(0)
        self.price.set(0)
        self.qty.set(1)
        self._set_substitute_hint(None)

        self.cmbMedicine["values"] = self._medicine_names
        self.txtBarcode.focus_set()

    def remove_item(self):
        selected = self.billTable.selection()
        if not selected: return
        self.billTable.delete(selected)
        self.calculate_total()
        self.clear_fields_and_reset_dropdown()

    def calculate_total(self):
        # money_sum() adds every line via Decimal instead of raw float
        # addition, then rounds once at the end - avoids the tiny
        # per-line float drift that can accumulate on a bill with many
        # items (see money.py's module docstring).
        subtotal = money_sum(
            float(self.billTable.item(item)["values"][4])
            for item in self.billTable.get_children()
        )

        self.subtotal.set(subtotal)
        self.gst.set(0)

        try:
            discount_pct = float(self.discount_percent.get() or 0)
        except (ValueError, tk.TclError):
            discount_pct = 0.0
        # Clamp to a sane range - a mistyped "500" shouldn't be able to
        # zero out (or invert) the bill.
        discount_pct = max(0.0, min(discount_pct, 100.0))
        discount_amt = to_money(subtotal * discount_pct / 100)
        self.discount_amt.set(discount_amt)

        grand = to_money(subtotal - discount_amt)
        self.grand_total.set(grand)

        self._refresh_rx_warning()

    def _get_rx_required_items(self):
        """Habit-forming (Schedule H1-style) medicines currently in the
        bill, via composition_master.habit_forming through
        medicine_master's composition_id link (set up earlier this
        project - benzodiazepines, opioids, Z-drugs, etc. are flagged).
        Used to warn/require prescriber details before saving, since
        Schedule H1 drugs legally need prescription record-keeping."""
        names = {self.billTable.item(i)["values"][0] for i in self.billTable.get_children()}
        return repo.get_habit_forming_names(names)

    def _refresh_rx_warning(self):
        flagged = self._get_rx_required_items()
        if flagged:
            self.rxWarningLabel.config(
                text="⚠ Prescription required (Schedule H1 / habit-forming): "
                + ", ".join(flagged)
                + " — please enter the Doctor's name before saving."
            )
            self.rxWarningLabel.pack(fill="x", padx=10, pady=(0, 5), before=self._billTableFrame)
        else:
            self.rxWarningLabel.pack_forget()

        # BUG FIX: this used to reference bare `subtotal`/`discount_amt`/
        # `grand` names - those are calculate_total()'s own LOCAL
        # variables (the caller), not visible in this separate method.
        # Every single call crashed with NameError ("Exception in Tkinter
        # callback"), meaning these labels never once successfully
        # updated - Subtotal/GST/Discount/Grand Total stayed stuck at
        # their initial "₹ 0.00" on screen even though the bill itself
        # (self.grand_total etc, already correctly set by
        # calculate_total() BEFORE this crash) saved and printed with
        # the right amounts. Fixed by reading the same StringVar/
        # DoubleVar calculate_total() itself just updated.
        self.lblSubtotal.config(text=f"₹ {self.subtotal.get():.2f}")
        self.lblGST.config(text="₹ 0.00")
        self.lblDiscount.config(text=f"- ₹ {self.discount_amt.get():.2f}" if self.discount_amt.get() else "₹ 0.00")
        self.lblGrand.config(text=f"₹ {self.grand_total.get():.2f}")

        # Items changed -> grand total changed -> balance/change must be
        # recalculated too, otherwise it'd still show a stale figure
        # from before the last item was added/removed.
        if self.payment_mode.get() != "Cash":
            self.received_amt.set(self.grand_total.get())
        self.calculate_balance()

    def on_payment_mode_change(self, event=None):
        if self.payment_mode.get() == "Cash":
            # Cash needs the actual amount handed over, typed fresh each
            # time - don't guess it.
            self.received_amt.set(0.0)
        else:
            # Card/UPI/Wallet settle for the exact amount - no change to
            # compute, so there's nothing for the cashier to type here.
            self.received_amt.set(self.grand_total.get())
        self.calculate_balance()

    def calculate_balance(self, event=None):
        try:
            received = float(self.received_amt.get() or 0)
        except (ValueError, tk.TclError):
            received = 0.0

        diff = round(received - self.grand_total.get(), 2)
        if diff >= 0:
            self.balance_display.set(f"₹ {diff:.2f} (Change)")
            self.lblBalance.config(fg="green")
        else:
            self.balance_display.set(f"₹ {abs(diff):.2f} (Due)")
            self.lblBalance.config(fg="red")

    # =====================================
    # FUNCTION-KEY SHORTCUTS (fast counter billing)
    # =====================================

    def _bind_shortcuts(self):
        """
        F1 Save, F2 New Bill, F3 Remove Item, F4 Print, F5 Add Item,
        Esc Clear Selection - bound on the shared root window (not
        bind_all), so it fires whenever any widget on THIS window has
        focus but never steals keys from a separate Toplevel popup
        (Substitute Medicine, Create Distributor Return, etc. each run
        in their own window with their own focus). Return is deliberately
        left alone - it's already wired per-widget for barcode/qty entry
        and a global Return bind here would double-fire those.
        """
        top = self.frame.winfo_toplevel()
        self._shortcut_map = {
            "<F1>": lambda e: self.save_bill(),
            "<F2>": lambda e: self.new_bill(),
            "<F3>": lambda e: self.remove_item(),
            "<F4>": lambda e: self.print_thermal_bill(),
            "<F5>": lambda e: self.add_item(),
            "<Escape>": lambda e: self.clear_fields_and_reset_dropdown(),
        }
        for seq, handler in self._shortcut_map.items():
            top.bind(seq, handler)

    def _unbind_shortcuts(self, event=None):
        # <Destroy> fires for this exact widget only (plain .bind(), not
        # bind_all) - but guard anyway in case that ever changes, so a
        # child widget's teardown can't accidentally unbind shortcuts
        # while the Billing screen itself is still open.
        if event is not None and event.widget is not self.frame:
            return
        top = self.frame.winfo_toplevel()
        for seq in getattr(self, "_shortcut_map", {}):
            try:
                top.unbind(seq)
            except Exception:
                pass

    def _apply_default_doctor(self):
        """
        Pre-fills Doctor with Settings' Default Doctor (see settings.py -
        the real-world reference is the pharmacy's own paper "Cash Bill"
        pad, which has "Prescribed by Dr. ..." fixed-printed on every
        bill). Runs at screen open and on every new_bill() - a fresh bill
        with no customer typed yet should still show the shop's standing
        doctor, matching the paper pad, instead of a blank field. Never
        overwrites a doctor already present (e.g. mid-edit state some
        future caller might reach this from).
        """
        self._shop_default_doctor = repo.get_default_doctor()
        if self._shop_default_doctor and not self.doctor_name.get().strip():
            self.doctor_name.set(self._shop_default_doctor)

    def _autofill_doctor(self, event=None):
        """Looks up the typed customer in Customer Master and fills in
        their usual doctor - a customer-specific match always wins over
        the shop's Default Doctor (_apply_default_doctor() above), and
        NEVER overwrites anything the cashier typed by hand for this
        specific visit. Distinguishing those two cases is why the guard
        compares against _shop_default_doctor instead of just checking
        "is the field empty" - the field is usually already pre-filled
        with the default by the time this runs. Silent no-op if the
        customer isn't found or has no doctor on file."""
        current = self.doctor_name.get().strip()
        if current and current != self._shop_default_doctor:
            return
        name = self.customer.get().strip()
        if not name:
            return
        row = repo.get_customer_doctor(name)
        if row and row[0]:
            self.doctor_name.set(row[0])

    def _autofill_discount(self, event=None):
        """Same idea as _autofill_doctor() - suggests the customer's
        loyalty discount % from Customer Master, only when the field is
        still at 0 (untouched), so it never overwrites a discount the
        cashier already typed for this specific bill. Recalculates the
        total immediately so the suggestion actually takes effect
        without needing an extra keystroke."""
        if self.discount_percent.get():
            return
        name = self.customer.get().strip()
        if not name:
            return
        row = repo.get_customer_discount_percent(name)
        if row and row[0]:
            self.discount_percent.set(row[0])
            self.calculate_total()

    def new_bill(self):
        for row in self.billTable.get_children():
            self.billTable.delete(row)
        self.generate_bill_no()
        self.customer.set("")
        self.doctor_name.set("")
        self._apply_default_doctor()
        self.patient_address.set("")
        self.discount_percent.set(0.0)
        self.discount_amt.set(0.0)
        self.barcode.set("")
        self.medicine.set("")
        self.batch.set("")
        self.stock.set(0)
        self.price.set(0)
        self.qty.set(1)
        self._set_substitute_hint(None)
        self.payment_mode.set("Cash")
        self.received_amt.set(0.0)
        self.calculate_total()
        self.load_medicines()
        self.txtBarcode.focus_set()

    def generate_bill_no(self):
        next_id = repo.get_next_sales_id()
        bill_no = f"BILL-{datetime.now().strftime('%Y%m%d')}-{next_id:04d}"
        self.bill_no.set(bill_no)

    def save_bill(self):
        if len(self.billTable.get_children()) == 0:
            ui_popups.show_error(self.frame, "Error", "No items added.")
            return

        # Schedule H1 / habit-forming medicines legally need prescriber
        # details on record. Block the save (not just warn) if the
        # Doctor field is empty - the on-screen warning banner already
        # gave a heads-up before this point, so this isn't a surprise.
        rx_items = self._get_rx_required_items()
        if rx_items and not self.doctor_name.get().strip():
            ui_popups.show_error(self.frame, 
                "Prescription Required",
                "This bill has Schedule H1 / habit-forming medicine(s):\n\n"
                + "\n".join(f"  • {m}" for m in rx_items)
                + "\n\nPlease enter the prescribing Doctor's name before saving."
            )
            return

        # Customer Credit Limit check - advisory (confirm-to-override),
        # not a hard block like the H1 check above, since exceeding a
        # limit is a business judgment call for the pharmacist, not a
        # legal requirement. Uses the exact same "every sale counts as
        # credit until offset by a customer_payments entry" formula
        # customer_ledger.py's own Khata view uses, so this always
        # agrees with what that screen shows.
        customer_name = self.customer.get().strip()
        if customer_name:
            status = repo.get_customer_credit_status(customer_name)
            limit = (status["limit"] or 0) if status else 0
            if limit > 0:
                current_outstanding = status["total_credit"] - status["total_paid"]
                projected_outstanding = current_outstanding + self.grand_total.get()
                if projected_outstanding > limit:
                    if not ui_popups.show_confirmation(self.frame, 
                        "Credit Limit Exceeded",
                        f'"{customer_name}" credit limit: ₹{limit:,.2f}\n\n'
                        f"Current outstanding: ₹{current_outstanding:,.2f}\n"
                        f"This bill: ₹{self.grand_total.get():,.2f}\n"
                        f"New outstanding would be: ₹{projected_outstanding:,.2f}\n\n"
                        "Save anyway?"
                    ):
                        return

        try:
            received = float(self.received_amt.get() or 0)
        except (ValueError, tk.TclError):
            received = 0.0
        balance = round(received - self.grand_total.get(), 2)

        # Cash short of the total isn't necessarily wrong (could be a
        # deliberate partial/credit sale) - confirm rather than block,
        # so a genuine mistake gets caught without stopping a real
        # partial-payment sale the pharmacist meant to make.
        if self.payment_mode.get() == "Cash" and balance < 0:
            if not ui_popups.show_confirmation(self.frame, 
                "Amount Short",
                f"Received amount is ₹{abs(balance):.2f} less than the bill total.\n\n"
                "Save anyway as a partial/credit payment?"
            ):
                return

        items = []
        for item in self.billTable.get_children():
            values = self.billTable.item(item)["values"]
            items.append({
                "medicine": values[0], "batch": values[1],
                "qty": int(values[2]), "price": float(values[3]), "total": float(values[4]),
            })

        try:
            repo.save_bill(
                self.bill_no.get(), datetime.now().strftime("%Y-%m-%d"), self.customer.get(),
                self.doctor_name.get().strip(),
                self.subtotal.get(), self.discount_amt.get(), self.grand_total.get(),
                self.payment_mode.get(), received, balance, self.patient_address.get().strip(),
                items
            )
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))
            return

        try: self.generate_invoice()
        except Exception: pass
        try: self.print_thermal_bill()
        except Exception: pass

        ui_popups.show_info(self.frame, "Success", "Bill Saved Successfully")
        self.new_bill()

    def get_shop_details(self):
        row = repo.get_shop_details_row()

        if row and row[0]:
            return {
                "name": row[0], "address": row[1] or "", "city": row[2] or "", "phone": row[3] or "",
                "gstin": row[4] or "", "dl20": row[5] or "", "dl21": row[6] or "", "fssai": row[7] or "", "footer": row[8] or "",
                # NULL (pre-migration settings row) defaults True, same
                # rule as settings.py's own load_data().
                "show_payment_on_receipt": bool(row[9]) if row[9] is not None else True,
                "default_doctor": (row[10] or "") if len(row) > 10 else "",
                # UPI ID for the payment QR on the A4 PDF invoice only -
                # see generate_invoice()'s own comment on why the thermal
                # receipt can't carry a QR image.
                "upi_id": (row[11] or "") if len(row) > 11 else "",
                # Thermal receipt "Plan B" (Aug 2026) - see Settings'
                # own comment on these two fields and print_thermal_
                # bill()/_print_thermal_graphic() below for how they're
                # used. Blank = no logo pasted / use Windows' default
                # printer.
                "receipt_logo_path": (row[12] or "") if len(row) > 12 else "",
                "thermal_printer_name": (row[13] or "") if len(row) > 13 else ""
            }
        return {
            "name": "LIFE CARE PHARMACY", "address": "", "city": "", "phone": "", "gstin": "",
            "dl20": "", "dl21": "", "fssai": "", "footer": "", "show_payment_on_receipt": True,
            "default_doctor": "", "upi_id": "", "receipt_logo_path": "", "thermal_printer_name": ""
        }

    def generate_invoice(self):
        if not os.path.exists("Invoices"): os.makedirs("Invoices")
        from app_paths import app_path
        filename = app_path("Invoices", f"{self.bill_no.get()}.pdf")
        shop = self.get_shop_details()

        c = canvas.Canvas(filename)
        y = 800
        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(300, y, shop["name"])
        y -= 18
        c.setFont("Helvetica", 9)
        address_line = ", ".join(filter(None, [shop["address"], shop["city"]]))
        if address_line: c.drawCentredString(300, y, address_line); y -= 14
        if shop["phone"]: c.drawCentredString(300, y, f"Phone : {shop['phone']}"); y -= 14
        if shop["gstin"]: c.drawCentredString(300, y, f"GSTIN : {shop['gstin']}"); y -= 14
        # D.L. No. (Drug License) - a drug inspector checking a bill on
        # the spot expects to see this printed, same as the shop's own
        # paper "Cash Bill" pad already does. Both dl20 and dl21 printed
        # together (comma-separated) when both are on file, matching how
        # the paper pad shows two license numbers.
        dl_parts = [d for d in (shop.get("dl20"), shop.get("dl21")) if d]
        if dl_parts:
            c.drawCentredString(300, y, "D.L. No. : " + " , ".join(dl_parts)); y -= 14

        c.setFont("Helvetica", 11)
        y -= 10
        c.drawString(40, y, f"Bill No : {self.bill_no.get()}")
        c.drawString(300, y, f"Date : {self.bill_date.get()}")
        y -= 20
        c.drawString(40, y, f"Customer : {self.customer.get()}")
        if self.doctor_name.get().strip():
            c.drawString(300, y, f"Prescribed by : {self.doctor_name.get().strip()}")
        y -= 30

        # Batch + Expiry are printed on the customer-facing bill itself
        # (not just kept internally) - real-world reason: the pharmacy's
        # own paper "Cash Bill" pad already has "Batch/Mfg" and "Exp"
        # columns, needed at drug-inspection time to trace exactly which
        # batch was sold on which bill.
        c.setFont("Helvetica", 9)
        c.drawString(40, y, "Medicine")
        c.drawString(260, y, "Batch")
        c.drawString(320, y, "Exp")
        c.drawString(370, y, "Qty")
        c.drawString(410, y, "Price")
        c.drawString(460, y, "Total")
        y -= 18

        c.setFont("Helvetica", 9)
        for item in self.billTable.get_children():
            values = self.billTable.item(item)["values"]
            name, batch, qty, price, total = values[0], values[1], values[2], values[3], values[4]

            exp_row = repo.get_medicine_expiry(name, batch)
            expiry = (exp_row[0] or "") if exp_row else ""

            # PDF இன்வாய்ஸில் பிராண்ட் பெயருடன் ஜெனரிக் மற்றும் டோசேஜை அச்சிடுதல்
            full_medicine_name = Billing.get_formatted_billing_item(name)
            c.drawString(40, y, str(full_medicine_name))

            c.drawString(260, y, str(batch))
            c.drawString(320, y, str(expiry))
            c.drawString(370, y, str(qty))
            c.drawString(410, y, str(price))
            c.drawString(460, y, str(total))
            y -= 25

        y -= 20
        if self.discount_amt.get():
            c.setFont("Helvetica", 10)
            c.drawString(300, y, f"Discount : - ₹ {self.discount_amt.get():.2f}")
            y -= 20
        c.setFont("Helvetica-Bold", 12)
        c.drawString(300, y, f"Grand Total : ₹ {self.grand_total.get():.2f}")
        y -= 20
        c.setFont("Helvetica", 10)
        c.drawString(40, y, f"Payment Mode : {self.payment_mode.get()}")
        # Anchored BEFORE the optional Received/Change line below so the
        # QR (drawn on the right, x=460+) lines up with "Payment Mode"
        # regardless of whether that extra line is shown - it sits in a
        # column nothing else on this row ever reaches into.
        qr_anchor_y = y
        if self.payment_mode.get() == "Cash" and shop.get("show_payment_on_receipt", True):
            try:
                received = float(self.received_amt.get() or 0)
            except (ValueError, tk.TclError):
                received = 0.0
            diff = round(received - self.grand_total.get(), 2)
            y -= 16
            c.drawString(40, y, f"Received : ₹ {received:.2f}    " + (f"Change : ₹ {diff:.2f}" if diff >= 0 else f"Due : ₹ {abs(diff):.2f}"))

        # UPI payment QR - A4 PDF invoice ONLY. The thermal receipt
        # (print_thermal_bill(), below) prints plain text via Notepad's
        # /p flag and structurally cannot embed an image - putting a QR
        # there would need switching to real ESC/POS thermal printing,
        # which is a separate, bigger change not done here. Blank UPI ID
        # in Settings = skipped entirely. Any failure here (qrcode not
        # installed, bad UPI ID text, image error) must never block the
        # invoice PDF itself - printing the bill is the core function,
        # the QR is a bonus.
        if shop.get("upi_id"):
            try:
                import qrcode
                from reportlab.lib.utils import ImageReader
                from urllib.parse import quote
                import io as _io

                upi_url = (
                    f"upi://pay?pa={quote(shop['upi_id'])}&pn={quote(shop['name'])}"
                    f"&am={self.grand_total.get():.2f}&cu=INR"
                )
                qr_img = qrcode.make(upi_url)
                buf = _io.BytesIO()
                qr_img.save(buf, format="PNG")
                buf.seek(0)

                qr_size = 80
                qr_x = 460
                qr_top = qr_anchor_y + 10
                c.drawImage(
                    ImageReader(buf), qr_x, qr_top - qr_size, width=qr_size, height=qr_size,
                    preserveAspectRatio=True, mask="auto"
                )
                c.setFont("Helvetica", 7)
                c.drawCentredString(qr_x + qr_size / 2, qr_top - qr_size - 10, "Scan & Pay via UPI")
            except Exception:
                pass

        c.save()
        return filename

    def print_thermal_bill(self):
        # Layout confirmed with a rendered sample before this was coded
        # (see chat) - all lines are exactly 36 chars wide (matches the
        # "----...----" separators) so columns line up under Notepad's
        # single uniform print font.
        #
        # Aug 2026 redesign ("Plan A", per chat - the user shared a
        # mockup of a stylized store receipt with a logo, bold header,
        # single Item/Qty/Total header row, Date+Time, "Walk-in"
        # default customer, and a "Get Well Soon!" footer message):
        #   - shop name wrapped in "** ... **" instead of plain text -
        #     the closest a plain-text/Notepad print can get to a bold
        #     header (Notepad's /p print can't mix font sizes/weights on
        #     one page, so true bold still isn't possible here - see
        #     _print_thermal_graphic() below for the path that CAN).
        #   - ONE shared "Item / Qty / Total" header row above the whole
        #     list, instead of repeating "Qty:"/"Total" on every item.
        #   - Date + Time together (time = the moment of printing).
        #   - blank Customer now defaults to "Walk-in".
        #   - the Settings "Footer" field (saved/loaded for a while but
        #     never actually printed anywhere) is now printed at the
        #     very bottom - so a message like "** Get Well Soon! **"
        #     appears automatically without hardcoding it here. Blank
        #     Footer setting = no footer block at all.
        #
        # "Plan B" (same chat - "after buy usb printer when connected
        # its work all printer and thermal print support"):
        # _print_thermal_graphic() below is a dormant, best-effort
        # SECOND path that renders this exact same content as a real
        # image (true bold shop name, a real drawn line instead of
        # "====", the optional Settings "Receipt Logo" pasted at the
        # top) and sends it through the normal Windows print spooler
        # (win32print/win32ui) to whatever printer Windows already
        # knows about - NOT a vendor-specific ESC/POS SDK (python-
        # escpos's Usb class needs an exact USB vendor_id/product_id per
        # printer model, which is the opposite of "works with all
        # printer"). Any USB thermal receipt printer that shows up
        # normally in Windows' own Printers list works this way, same
        # as a regular inkjet/laser printer would - no vendor-ID hunting
        # needed, and no code change needed here when that printer is
        # connected later. It needs the `pywin32` package (`pip install
        # pywin32` - not installed yet, checked via grep across this
        # whole project); until then it silently does nothing and this
        # method falls straight through to the plain-text Notepad print
        # below, completely unchanged from before.
        shop = self.get_shop_details()

        customer_name = self.customer.get().strip() or "Walk-in"
        doctor = _clean_doctor_name(self.doctor_name.get())

        receipt = "====================================\n"
        receipt += f"** {shop['name']} **".center(36).rstrip() + "\n"
        # Address/Phone/GSTIN under the shop name - the A4 PDF invoice
        # (generate_invoice() above) already prints these; wrapped to
        # the same 36-char width as everything else on this receipt,
        # each line centered like the shop name above it.
        address_line = ", ".join(filter(None, [shop.get("address"), shop.get("city")]))
        if address_line:
            for wrapped in textwrap.wrap(address_line, width=36):
                receipt += f"{wrapped.center(36)}\n"
        if shop.get("phone"):
            receipt += f"{('Ph: ' + shop['phone']).center(36)}\n"
        if shop.get("gstin"):
            receipt += f"{('GSTIN: ' + shop['gstin']).center(36)}\n"
        receipt += "====================================\n"
        # D.L. No. + Dr - same reasoning as generate_invoice(): the
        # shop's own paper "Cash Bill" pad prints these on every bill, a
        # drug inspector expects to see them on the spot. DL20/DL21
        # merged into one compact line (e.g. "TN/DPI/20/21/00085")
        # instead of printing both full numbers - see _merge_dl_numbers().
        dl_merged = _merge_dl_numbers(shop.get("dl20"), shop.get("dl21"))
        if dl_merged:
            receipt += f"D.L. No.: {dl_merged}\n"
        # Full bill_no (not a shortened form) - kept identical to what's
        # shown on-screen and in every report/ledger, so staff can match
        # a printed receipt back to its record without any mental
        # conversion (user explicitly asked to keep "old style" full
        # number after trying the shortened form).
        receipt += f"Bill No : {self.bill_no.get()}\n"
        # Date + Time together, matching the mockup ("Date :
        # 28-08-2026 17:40") instead of the old date-only line - time is
        # taken at the moment of printing (the bill is printed right
        # after it's saved, so this is effectively the sale time).
        receipt += f"Date    : {self.bill_date.get()} {datetime.now().strftime('%H:%M')}\n"
        receipt += f"Customer: {customer_name}\n"
        if doctor:
            receipt += f"Dr : {doctor}\n"
        receipt += "------------------------------------\n"

        # ONE shared header row (was repeated on every item before), now
        # with a leading "Sn" (serial number) column - the exact layout
        # the user reviewed and approved via a Gemini mockup (Aug 2026
        # chat): "Sn | Item | Qty | Total" on one line per item (name
        # truncated to fit), Batch/Exp on its own indented line below,
        # no qty/total repeated there.
        receipt += "{:<3}{:<20}{:>5}{:>8}\n".format("Sn", "Item", "Qty", "Total")
        receipt += "------------------------------------\n"
        for idx, item in enumerate(self.billTable.get_children(), start=1):
            values = self.billTable.item(item)["values"]
            brand_name = str(values[0])
            batch = str(values[1])
            qty = values[2]
            total = values[4]

            receipt += "{:<3}{:<20}{:>5}{:>8}\n".format(idx, brand_name[:20], qty, f"Rs.{total}")

            row = repo.get_medicine_expiry(brand_name, batch)
            expiry = (row[0] or "") if row else ""
            bracket = f"   ({batch}, {expiry})"
            receipt += f"{bracket[:36]}\n"

        receipt += "------------------------------------\n"
        if self.discount_amt.get():
            receipt += "{:<18}{:>18}\n".format("Discount    :", f"- Rs.{self.discount_amt.get():.2f}")
        # Grand Total + Payment mode merged onto one line, e.g.
        # "Grand Total :        Rs.18.32 (Cash)" - Payment mode is how
        # they paid (not sensitive), unlike Received/Change/Due below
        # which stays gated by the Settings toggle since that reveals
        # actual cash handled.
        receipt += "{:<18}{:>18}\n".format(
            "Grand Total :", f"Rs.{self.grand_total.get():.2f} ({self.payment_mode.get()})"
        )
        if self.payment_mode.get() == "Cash" and shop.get("show_payment_on_receipt", True):
            try:
                received = float(self.received_amt.get() or 0)
            except (ValueError, tk.TclError):
                received = 0.0
            diff = round(received - self.grand_total.get(), 2)
            receipt += f"Received    : Rs.{received:.2f}\n"
            if diff >= 0:
                receipt += f"Change      : Rs.{diff:.2f}\n"
            else:
                receipt += f"Due         : Rs.{abs(diff):.2f}\n"
        receipt += "====================================\n"
        # Footer (Settings screen's "Footer" field) - saved/loaded for a
        # while but never actually printed anywhere until now; a custom
        # sign-off like "** Get Well Soon! **" now appears automatically,
        # wrapped to the same 36-char width as the address above. Blank
        # Footer setting = this whole block is skipped, no stray blank
        # lines left behind.
        if shop.get("footer", "").strip():
            for wrapped in textwrap.wrap(shop["footer"].strip(), width=36):
                receipt += f"{wrapped.center(36)}\n"
            receipt += "====================================\n"

        # Plan B: try a real graphic/thermal print first (true bold
        # header, a drawn line instead of "====", optional logo) - see
        # this method's own comment above and _print_thermal_graphic()'s
        # docstring for exactly what it needs to activate. ANY failure
        # at all (library missing, no printer, bad logo file, printer
        # offline, ...) must fall through to the plain-text Notepad
        # print below exactly as before - printing SOMETHING is more
        # important than printing it beautifully, and this must never
        # be the reason a bill fails to print.
        try:
            if self._print_thermal_graphic(shop, receipt):
                return
        except Exception:
            pass

        file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
        file.write(receipt)
        file.close()
        subprocess.Popen(["notepad.exe", "/p", file.name])

    def _print_thermal_graphic(self, shop, receipt_text):
        """Best-effort SECOND print path ("Plan B", Aug 2026 chat) -
        renders the exact same receipt content as print_thermal_bill()'s
        plain text above, but as a real image: the shop-name line is
        drawn bold/larger, any line made only of "=" or "-" characters
        is drawn as an actual ruled line instead of literal characters,
        and the optional Settings "Receipt Logo Image" is pasted
        centered at the top if one is set. The image is then sent
        through the normal Windows print spooler (win32print/win32ui)
        to the printer named in Settings' "Thermal Printer Name" field,
        or the Windows DEFAULT printer if that's left blank.

        Deliberately NOT built on a vendor-specific ESC/POS SDK
        (python-escpos's Usb class, imported-but-unused near the top of
        this file, needs an exact USB vendor_id/product_id per printer
        model) - going through the normal Windows print spooler instead
        means this works with whatever printer Windows already has a
        driver for, thermal or not, the same way the A4 PDF invoice
        above already prints to any regular printer. That's what makes
        "plug in a USB thermal printer later and it just works" true in
        general, instead of needing a new code change per printer model.

        Needs BOTH:
          - `Pillow` (already used elsewhere in this app - see
            bulk_import.py / splash_screen.py / generate_icons.py, so
            it's already installed here).
          - `pywin32` (NOT installed as of this writing, per a grep of
            this whole project - run `pip install pywin32` on the
            pharmacy's Windows machine to turn this on).
        Returns False (never raises) the instant either is missing, or
        on ANY other failure along the way (bad logo path, no printer
        reachable, printer offline, permission error, unsupported font,
        ...) - the caller then falls back to the guaranteed-safe plain-
        text Notepad print, so this method activating or not only ever
        changes how nice the receipt looks, never whether it prints."""
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageWin
            import win32print
            import win32ui
            import win32con
        except ImportError:
            return False

        try:
            printer_name = (shop.get("thermal_printer_name") or "").strip()
            if not printer_name:
                printer_name = win32print.GetDefaultPrinter()
            if not printer_name:
                return False
        except Exception:
            return False

        try:
            char_w, line_h = 11, 26
            width_px = 36 * char_w

            def pick_font(bold, size):
                for name in (["consolab.ttf", "courbd.ttf"] if bold else ["consola.ttf", "cour.ttf"]):
                    try:
                        return ImageFont.truetype(name, size)
                    except Exception:
                        continue
                return ImageFont.load_default()

            normal_font = pick_font(False, 18)
            bold_font = pick_font(True, 24)

            lines = receipt_text.split("\n")
            shop_name_stripped = (shop.get("name") or "").strip()

            logo_img = None
            logo_h = 0
            logo_path = (shop.get("receipt_logo_path") or "").strip()
            if logo_path and os.path.exists(logo_path):
                try:
                    logo_img = Image.open(logo_path).convert("RGBA")
                    scale = min(1.0, (width_px - 20) / logo_img.width)
                    logo_img = logo_img.resize(
                        (max(1, int(logo_img.width * scale)), max(1, int(logo_img.height * scale)))
                    )
                    logo_h = logo_img.height + 10
                except Exception:
                    logo_img = None
                    logo_h = 0

            height_px = logo_h + line_h * (len(lines) + 2)
            img = Image.new("RGB", (width_px, height_px), "white")
            draw = ImageDraw.Draw(img)

            y = 0
            if logo_img is not None:
                img.paste(logo_img, ((width_px - logo_img.width) // 2, 5), logo_img)
                y = logo_h

            for raw_line in lines:
                line = raw_line.rstrip("\n")
                stripped = line.strip()
                is_rule = stripped != "" and (set(stripped) <= {"="} or set(stripped) <= {"-"})
                is_shop_name = bool(shop_name_stripped) and stripped.strip("*").strip() == shop_name_stripped
                if is_rule:
                    draw.line([(0, y + line_h // 2), (width_px, y + line_h // 2)], fill="black", width=2)
                elif is_shop_name:
                    text = stripped.strip("*").strip()
                    bbox = draw.textbbox((0, 0), text, font=bold_font)
                    text_w = bbox[2] - bbox[0]
                    draw.text((max(0, (width_px - text_w) // 2), y), text, font=bold_font, fill="black")
                else:
                    draw.text((0, y), line, font=normal_font, fill="black")
                y += line_h

            hdc = win32ui.CreateDC()
            hdc.CreatePrinterDC(printer_name)
            hdc.StartDoc("Thermal Receipt")
            hdc.StartPage()
            printer_width = hdc.GetDeviceCaps(win32con.HORZRES)
            scale = printer_width / img.width
            target_h = max(1, int(img.height * scale))
            dib = ImageWin.Dib(img)
            dib.draw(hdc.GetHandleOutput(), (0, 0, printer_width, target_h))
            hdc.EndPage()
            hdc.EndDoc()
            hdc.DeleteDC()
            return True
        except Exception:
            return False

    def clear(self):
        self.customer.set("")
        self.doctor_name.set("")
        self.patient_address.set("")
        self.discount_percent.set(0.0)
        self.discount_amt.set(0.0)
        self.cmbMedicine.set("")
        self.medicine.set("")
        self.batch.set("")
        self.stock.set(0)
        self.price.set(0)
        self.qty.set(1)

    def search_medicine(self, event=None):
        if self.medicine.get().strip() == "":
            self.cmbMedicine["values"] = self._medicine_names
            return
        self.cmbMedicine["values"] = repo.search_medicine_names(self.medicine.get())

    def get_expiry_date(self):
        row = repo.get_medicine_expiry_by_name(self.medicine.get())
        return row[0] if row else ""