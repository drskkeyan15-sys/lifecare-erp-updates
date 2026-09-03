import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import csv
import os
import json
from datetime import datetime, timedelta

from bulk_import import BulkImportWindow
import generic_mapping
import brand_mapping
import medicine_matcher
import ui_style
import audit_log
import theme
from icon_loader import get_icon

# FIX (Aug 2026): this used to be a local "DB_NAME = 'pharmacy.db'" - a
# bare RELATIVE filename, completely bypassing app_paths.py's shared
# DB_NAME (which every other screen - bulk_import.py, medicine_master.py,
# stock.py, etc. - already imports). A relative "pharmacy.db" resolves
# against the process's current working directory, not necessarily the
# folder the exe/pharmacy.db actually lives in - so Purchase Entry's own
# Save Purchase (INSERT INTO purchase + the stock UPDATE) was silently
# writing into a DIFFERENT, likely-empty pharmacy.db than every other
# screen reads from. That's exactly why "Purchase Saved Successfully"
# kept appearing while Medicine Master/Stock Management kept showing
# stock=0 for the same medicines - two different files, not a real save
# failure. Importing the shared DB_NAME here fixes Purchase Entry to
# finally point at the same one file as the rest of the app.
#
# Aug 2026 repository-layer pass: all direct sqlite3 access has since
# moved into purchase_repository.py (see that module's docstring) -
# DB_NAME itself is no longer imported here, only by the repository.
import purchase_repository as repo
import ui_popups

# ======================================
# EXPORT SETTINGS (Aug 2026) - which columns appear in the CSV/PDF
# invoice export, and their header labels. This is the FULL toggleable
# set - "Medicine"/"Description of Goods" and "Amount" are always shown
# (an invoice with no item name or no amount isn't useful) and aren't in
# this list at all. "composition" doubles as the toggle for whether the
# PDF's Description of Goods column shows "(Generic)" in brackets too -
# see export_pdf()/_draw_purchase_invoice_pdf().
#
# Persisted per-install as JSON in settings.purchase_export_columns (see
# get_export_column_config()/save_export_column_config()) - this literal
# list is only the factory default / the source of truth for column
# ORDER and which keys exist; a saved config only overrides "visible"
# and "label" per key, never introduces new keys the code doesn't know
# about.
DEFAULT_EXPORT_COLUMNS = [
    {"key": "composition", "label": "Composition", "visible": True},
    {"key": "hsn",         "label": "HSN",          "visible": True},
    {"key": "batch",       "label": "Batch",        "visible": True},
    {"key": "expiry",      "label": "Expiry",       "visible": True},
    {"key": "pack_size",   "label": "Pack Size",    "visible": True},
    {"key": "gst_pct",     "label": "GST%",         "visible": True},
    {"key": "qty",         "label": "Qty",          "visible": True},
    {"key": "rate",        "label": "Rate",         "visible": True},
    {"key": "mrp",         "label": "MRP",          "visible": True},
]

# Relative PDF column widths (points, before the usual proportional
# scale-to-page-width step) - keeps the table looking sensibly
# proportioned no matter which subset of columns is currently visible.
_EXPORT_COL_PDF_WIDTH = {
    "composition": 95, "hsn": 40, "batch": 46, "expiry": 34,
    "pack_size": 50, "gst_pct": 28, "qty": 26, "rate": 42, "mrp": 42,
}


class Purchase:

    def __init__(self, frame, on_close=None):
        self.frame = frame
        self.on_close = on_close
        self.create_variables()
        self.create_ui()
        # DEFERRED DATA LOAD (Aug 2026, perceived-speed pass) - same
        # pattern as medicine_master.py/brand_master_gui.py's identical
        # change: create_ui() above builds the ledger/item-entry widgets
        # with no DB calls, so scheduling the medicine/supplier dropdown
        # loads and the bill number lookup one Tk idle tick later lets the
        # real screen structure get painted first (Bill No/Date/Grand
        # Total etc. all appear immediately), with the dropdowns and Bill
        # No filling in a moment after - not a "Loading" placeholder, the
        # real screen just appears sooner.
        self.frame.after(1, self._load_initial_data)
        self._bind_shortcuts()
        # Unbind the moment this screen is torn down - same reasoning as
        # billing.py's own _bind_shortcuts()/_unbind_shortcuts(): without
        # this, F5/Escape pressed on a completely different screen later
        # would still try to call add_item()/clear_item_fields() on this
        # already-gone Purchase instance.
        self.frame.bind("<Destroy>", self._unbind_shortcuts)

    def _load_initial_data(self):
        self.load_medicines()
        self.load_suppliers()
        self.generate_bill_no()

    def _dashboard_refresh(self):
        """Called by dashboard.py's screen cache (Aug 2026) when this
        already-built screen is shown again instead of being rebuilt.
        Deliberately only refreshes the medicine/supplier reference lists
        (so anything added elsewhere shows up in the dropdowns) - does
        NOT touch the bill number or any items already added to this
        in-progress purchase, so navigating away and back never loses
        entry work already done on this draft."""
        self.load_medicines()
        self.load_suppliers()

    def _handle_close(self):
        """Destroys this screen's own frame, then hands control back to
        whoever opened it (Dashboard's on_close=self.open_dashboard) so
        the app returns to the dashboard home view instead of leaving
        the content area empty."""
        self.frame.destroy()
        if self.on_close:
            self.on_close()

# ======================================
# VARIABLES
# ======================================

    def create_variables(self):
        self.bill_no = tk.StringVar()
        self.bill_date = tk.StringVar(
            value=datetime.now().strftime("%d-%m-%Y")
        )
        # 2026-09-01 real bug report: pharmacist entered a LATE/backdated
        # purchase (physically bought 18-08-2026, typed into the ERP on
        # 01-09-2026). They correctly filled "Supp. Inv. Date" as
        # 18-08-2026, but never touched THIS field ("Purchase Date" -
        # labelled just "Date" in the UI, easy to miss as a second,
        # separate date) - it silently stayed at today's default, so the
        # Supplier Ledger (which reads bill_date, not supplier_invoice_
        # date) kept showing today's date/bill-no instead of 18-08-2026.
        # Remembering the untouched default lets _sync_purchase_date_
        # from_supplier_invoice() below tell "pharmacist deliberately
        # left this as today" apart from "pharmacist never got to this
        # field yet" - only the latter gets auto-filled.
        self._bill_date_default = self.bill_date.get()
        self.supplier = tk.StringVar()
        self.medicine = tk.StringVar()
        self.batch = tk.StringVar()
        self.expiry = tk.StringVar()
        # HSN - auto-filled from medicine_master (like batch/expiry/gst
        # already are, see fetch_medicine()) when an existing medicine is
        # picked; stays blank for a brand-new medicine (offer_create_
        # medicine() doesn't ask for HSN either - matches its own comment
        # that HSN/Rack always need a pharmacist's eyes later). Not shown
        # as its own entry field in the Add Item bar (no room) - carried
        # straight into the item grid instead.
        self.hsn = tk.StringVar(value="")
        # Pack Size ("15'S", "10*10" etc.) - auto-filled from medicine_
        # master (like HSN/GST above) when an existing medicine is
        # picked, EDITABLE this time (unlike HSN) since this directly
        # drives the stock-quantity multiplier for THIS purchase (see
        # save_purchase()'s "NEW SMART STOCK UPDATE" section) - a
        # pharmacist who knows Medicine Master's value is wrong/blank for
        # this batch needs to be able to correct it right here rather
        # than stopping to fix Medicine Master mid-purchase. Defaults to
        # "1" for a brand-new medicine (matches offer_create_medicine()'s
        # own default), not blank, since this field - unlike HSN - is
        # actively used in this purchase's own arithmetic immediately.
        self.pack_size = tk.StringVar(value="1")
        self.purchase = tk.DoubleVar(value=0)
        self.sale = tk.DoubleVar(value=0)
        self.gst = tk.DoubleVar(value=0)
        self.qty = tk.IntVar(value=1)
        self.total = tk.DoubleVar(value=0)
        self._medicine_names = []

        # Supplier contact display - auto-filled read-only from the
        # supplier table when a Supplier is picked (new; the old screen
        # showed nothing but the name). Purely informational.
        self.supplier_address = tk.StringVar(value="")
        self.supplier_phone = tk.StringVar(value="")

        # Supplier's OWN invoice number/date - different from Bill No
        # above (which is OUR internal PUR-YYYYMMDD-NNNN reference). The
        # BharatERP-style invoice print/export needs both shown side by
        # side (see the Aug 2026 CSV/PDF export conversation) - a real
        # purchase invoice always cites the supplier's own bill number so
        # it can be matched against their paper/GST records. Optional -
        # left blank is fine, print/export just shows "-" for it.
        self.supplier_invoice_no = tk.StringVar(value="")
        self.supplier_invoice_date = tk.StringVar(value="")

        # "Inclusive of Taxes" - deliberately COSMETIC ONLY for now (see
        # the redesign conversation). medicine_master.purchase is treated
        # as a GST-EXCLUSIVE rate everywhere else in the app (billing.py's
        # save_bill() and medicine_master.py's calculate_profit() both do
        # purchase * (1 + gst/100) to get landed cost) - flipping what
        # this checkbox actually stores would need those two other files
        # changed in lockstep, which is out of scope for a layout
        # redesign. This just relabels the Purchase/Rate column header so
        # it's not silently misleading; the checked/unchecked state does
        # not change any saved value.
        self.inclusive_tax = tk.BooleanVar(value=False)

        # Breakdown line (Total Items/Qty/Subtotal/GST/Net) shown in the
        # totals footer - split out from lblGrand, which now shows just
        # the big top-right Net Amount figure instead of this whole
        # concatenated string (see calculate_grand_total()).
        self.grand_breakdown = tk.StringVar(value="Total Items: 0  |  Total Qty: 0  |  Subtotal: ₹ 0.00  |  GST: ₹ 0.00")

# ======================================
# USER INTERFACE
# ======================================

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="PURCHASE ENTRY",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

# ======================================
# LEDGER PANEL - Supplier/Bill/Date on the left, Grand Total prominent
# on the right (redesigned to the BharatERP-style single-page layout -
# see the Aug 2026 redesign conversation). Every field/behaviour below
# that existed before (Bill No editable, Date editable, Supplier
# combobox) is unchanged - only laid out differently, plus two NEW
# additions: the "+ Add New" supplier button and the Address/Phone
# auto-fill display.
# ======================================

        ledger = tk.LabelFrame(
            self.frame,
            text="Purchase Details (Ledger)",
            font=("Segoe UI", 10, "bold")
        )
        ledger.pack(fill="x", padx=10, pady=10)

        left = tk.Frame(ledger)
        left.pack(side="left", fill="x", expand=True)

        tk.Label(left, text="Supplier").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.cmbSupplier = ttk.Combobox(
            left,
            textvariable=self.supplier,
            width=28,
            state="readonly"
        )
        self.cmbSupplier.grid(row=0, column=1, padx=5)
        # NOTE: the ERP-wide keyboard-nav wiring for this combobox (auto-
        # advance to Medicine on selection) is added further down, right
        # after self.cmbMedicine itself is created - see that comment.

        tk.Button(
            left, text="+ Add New", bg="#2E7D32", fg="white", width=9,
            font=("Segoe UI", 9, "bold"), command=self.open_add_supplier_popup
        ).grid(row=0, column=2, padx=(0, 4))

        # Edit - fixes the SELECTED supplier's own Supplier Master record
        # (GSTIN/Address/Phone/DL No/Credit Period) without leaving
        # Purchase Entry - new this redesign. Deliberately updates
        # Supplier Master itself (not a one-off invoice override), so a
        # corrected GSTIN is right on every future purchase too, not just
        # this one - see open_edit_supplier_popup().
        tk.Button(
            left, text="Edit", bg="#1565C0", fg="white", width=6,
            font=("Segoe UI", 9, "bold"), command=self.open_edit_supplier_popup
        ).grid(row=0, column=3, padx=(0, 15))

        tk.Label(left, text="Bill No").grid(row=0, column=4, padx=5, sticky="w")
        tk.Entry(left, textvariable=self.bill_no, width=20).grid(row=0, column=5, padx=5)

        # 2026-09-01: relabelled from plain "Date" - a pharmacist doing a
        # backdated entry read "Date" and "Supp. Inv. Date" (two rows
        # below) as the same thing, edited only the second one, and never
        # noticed this one silently staying at today - see the
        # _bill_date_default comment in create_variables() for the full
        # story and the auto-sync fix below.
        tk.Label(left, text="Purchase Date").grid(row=0, column=6, padx=5, sticky="w")
        tk.Entry(left, textvariable=self.bill_date, width=13).grid(row=0, column=7, padx=5)

        tk.Label(left, text="Address").grid(row=1, column=0, padx=5, pady=(0, 5), sticky="w")
        tk.Entry(
            left, textvariable=self.supplier_address, width=28,
            state="readonly", takefocus=0
        ).grid(row=1, column=1, padx=5, pady=(0, 5))

        tk.Label(left, text="Phone").grid(row=1, column=4, pady=(0, 5), sticky="w")
        tk.Entry(
            left, textvariable=self.supplier_phone, width=18,
            state="readonly", takefocus=0
        ).grid(row=1, column=5, pady=(0, 5), sticky="w")

        # Supplier's Invoice No/Date - the number printed on the physical
        # bill the supplier handed over, distinct from OUR OWN Bill No
        # above. Both editable, optional (blank shows "-" on export).
        tk.Label(left, text="Supp. Inv. No").grid(row=2, column=0, padx=5, pady=(0, 5), sticky="w")
        tk.Entry(
            left, textvariable=self.supplier_invoice_no, width=28
        ).grid(row=2, column=1, padx=5, pady=(0, 5))

        tk.Label(left, text="Supp. Inv. Date").grid(row=2, column=4, pady=(0, 5), sticky="w")
        supp_inv_date_entry = tk.Entry(
            left, textvariable=self.supplier_invoice_date, width=13
        )
        supp_inv_date_entry.grid(row=2, column=5, pady=(0, 5), sticky="w")
        # 2026-09-01 fix - see _bill_date_default comment above: as soon
        # as the pharmacist finishes typing the SUPPLIER's invoice date,
        # if "Purchase Date" is still sitting at its untouched today-
        # default, assume this is a backdated/late entry for that same
        # date and fill it in automatically. Never overwrites a Purchase
        # Date the pharmacist already edited themselves.
        supp_inv_date_entry.bind("<FocusOut>", self._sync_purchase_date_from_supplier_invoice, add="+")

        right = tk.Frame(ledger)
        right.pack(side="right", padx=15)
        tk.Label(right, text="Grand Total", font=("Segoe UI", 10)).pack(anchor="e")
        self.lblGrand = tk.Label(
            right, text="₹ 0.00", fg="#1565C0", font=("Segoe UI", 20, "bold")
        )
        self.lblGrand.pack(anchor="e")

# ======================================
# ITEM ENTRY BAR - same fields as before (Medicine/Batch/Expiry/
# Purchase/MRP/Qty/GST/Total), same Enter-key fast-entry chain, same
# add_item()/offer_create_medicine() logic - just restyled into a
# single compact bar instead of a 3-row grid, with the new "Inclusive
# of Taxes" cosmetic checkbox and the Bulk Import button relabeled to
# match the "Import Invoice" wording from the reference layout.
# ======================================

        entry = tk.LabelFrame(
            self.frame,
            text="Add Item",
            font=("Segoe UI", 10, "bold")
        )
        entry.pack(fill="x", padx=10, pady=10)

        row1 = tk.Frame(entry)
        row1.pack(fill="x", padx=5, pady=(5, 2))

        tk.Label(row1, text="Medicine").pack(side="left")
        self.cmbMedicine = ttk.Combobox(
            row1, textvariable=self.medicine, width=32, state="normal"
        )
        self.cmbMedicine.pack(side="left", padx=(5, 15))

        tk.Label(row1, text="Batch").pack(side="left")
        self.txtBatch = tk.Entry(row1, textvariable=self.batch, width=13)
        self.txtBatch.pack(side="left", padx=(5, 15))

        tk.Label(row1, text="Expiry").pack(side="left")
        self.txtExpiry = tk.Entry(row1, textvariable=self.expiry, width=10)
        self.txtExpiry.pack(side="left", padx=(5, 15))

        self.lblPurchase = tk.Label(row1, text="Purchase (excl. GST)")
        self.lblPurchase.pack(side="left")
        self.txtPurchase = tk.Entry(row1, textvariable=self.purchase, width=10)
        self.txtPurchase.pack(side="left", padx=(5, 15))

        tk.Label(row1, text="MRP").pack(side="left")
        self.txtMrp = tk.Entry(row1, textvariable=self.sale, width=10)
        self.txtMrp.pack(side="left", padx=(5, 15))

        tk.Label(row1, text="Pack Size").pack(side="left")
        self.txtPackSize = tk.Entry(row1, textvariable=self.pack_size, width=10)
        self.txtPackSize.pack(side="left", padx=(5, 15))

        row2 = tk.Frame(entry)
        row2.pack(fill="x", padx=5, pady=(2, 5))

        tk.Label(row2, text="Qty").pack(side="left")
        self.txtQty = tk.Entry(row2, textvariable=self.qty, width=8)
        self.txtQty.pack(side="left", padx=(5, 15))
        self.qty.trace_add("write", self.calculate_total)

        tk.Label(row2, text="GST%").pack(side="left")
        self.txtGst = tk.Entry(row2, textvariable=self.gst, width=8)
        self.txtGst.pack(side="left", padx=(5, 15))
        # Purchase/GST also recompute Total live now - needed so Total
        # stays correct the moment "Inclusive of Taxes" changes what
        # Purchase actually means (see _get_exclusive_purchase_rate()) -
        # previously Total only refreshed on Qty changing.
        self.purchase.trace_add("write", self.calculate_total)
        self.gst.trace_add("write", self.calculate_total)

        tk.Label(row2, text="Total").pack(side="left")
        tk.Entry(
            row2, textvariable=self.total, width=12, state="readonly", takefocus=0
        ).pack(side="left", padx=(5, 20))

        tk.Checkbutton(
            row2, text="Inclusive of Taxes", variable=self.inclusive_tax,
            command=self._on_inclusive_tax_toggle
        ).pack(side="left", padx=(0, 20))

        tk.Button(
            row2, text="+ Add Item (F5)", bg="green", fg="white", width=18,
            font=("Segoe UI", 10, "bold"), command=self.add_item
        ).pack(side="left", padx=5)

        tk.Button(
            row2, text=" Import Invoice", image=get_icon("download"), compound="left",
            bg="#6A1B9A", fg="white", padx=14, pady=4,
            font=("Segoe UI", 10, "bold"), command=self.open_bulk_import
        ).pack(side="left", padx=5)

        # ERP-wide keyboard-nav pass (Aug 2026): Supplier's mouse-click/
        # arrow-key selection now advances straight into Medicine (was
        # previously mouse-click-only, no focus advance at all).
        ui_style.bind_search_combo(
            self.cmbSupplier,
            on_confirm=self._on_supplier_selected,
            next_widget=self.cmbMedicine,
        )
        # Medicine: typing/mouse-click/Enter all now resolve through the
        # same _confirm_purchase_medicine() path and advance to Batch -
        # previously only this next line's plain <Return> bind did that
        # (now folded into bind_search_combo), so picking a match with
        # the mouse left focus stuck here. A brand-new (unrecognized)
        # name still advances too - typing one in here is a normal
        # "first purchase of a new item" flow (see offer_create_medicine()
        # in add_item()), unlike Billing where an unrecognized name is
        # always a mistake.
        ui_style.bind_search_combo(
            self.cmbMedicine,
            on_filter=lambda text: self.on_medicine_keyrelease(),
            on_confirm=self._confirm_purchase_medicine,
            next_widget=self.txtBatch,
        )
        self.txtBatch.bind("<Return>", lambda e: self._focus_next(self.txtExpiry))
        self.txtExpiry.bind("<Return>", lambda e: self._focus_next(self.txtPurchase))
        self.txtPurchase.bind("<Return>", lambda e: self._focus_next(self.txtMrp))
        self.txtMrp.bind("<Return>", lambda e: self._focus_next(self.txtPackSize))
        self.txtPackSize.bind("<Return>", lambda e: self._focus_next(self.txtGst))
        self.txtGst.bind("<Return>", lambda e: self._focus_next(self.txtQty))
        self.txtQty.bind("<Return>", lambda e: self.add_item())

# ======================================
# PURCHASE TABLE
# ======================================

        columns = (
            "Medicine",
            "Batch",
            "Expiry",
            "HSN",
            "GST%",
            "Purchase",
            "MRP",
            "Pack Size",
            "Qty",
            "Total",
            # Batch-wise expiry status (Expired / Expiring Soon / OK) -
            # same convention Medicine Master's Status column already
            # uses (see add_item()/_purchase_item_status()). Deliberately
            # the LAST column, same as before this was added - the
            # existing last-column-stretch fix below now stretches (and,
            # since the Aug 2026 pass, caps - see ui_style.
            # MAX_STRETCH_COLUMN_WIDTH) THIS column instead of "Total",
            # so a pharmacist entering a batch immediately sees if
            # they're about to receive already-expired or soon-to-expire
            # stock, instead of the column just being blank fill space.
            "Status",
        )

        # tksheet, not ttk.Treeview - see the Aug 2026 UI redesign
        # conversation for why: Treeview cannot draw real vertical grid
        # lines between columns (a hard Tk limitation, not a styling
        # gap), which is what a genuine "looks like Excel" table needs.
        # See ui_style.make_excel_sheet() - every API call it makes was
        # checked against the actual installed tksheet 7.6.0 source, not
        # guessed, since this sandbox has no tkinter to test against
        # live before handing it over.
        col_widths = {"Medicine": 190, "Batch": 100, "Expiry": 80, "HSN": 80, "GST%": 55, "Purchase": 85, "MRP": 85, "Pack Size": 80, "Qty": 60, "Total": 100, "Status": 110}
        # 2026-08-30: switched from make_excel_sheet() (tksheet) to
        # make_plain_sheet() (plain ttk.Treeview) - see medicine_master.py's
        # identical comment / ui_style.PlainSheet's docstring for why.
        self.purchaseTable = ui_style.make_plain_sheet(
            self.frame, columns, col_widths,
            text_columns=("Medicine", "Batch", "Expiry", "HSN", "Pack Size", "Status"),
        )
        self.purchaseTable.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=10
        )
        self.purchaseTable.enable_bindings(*ui_style.READONLY_BINDINGS)
        ui_style.enable_row_highlight_on_select(self.purchaseTable)

        # Stretch the last column ("Total") to fill the leftover width -
        # without this, make_excel_sheet()'s fixed pixel column widths
        # leave a blank strip past the last real column once the sheet
        # is packed fill="both" wider than its own columns sum to (same
        # bug fixed in medicine_master.py - see that file's comment for
        # the full explanation). Bound to the ROOT window's <Configure>,
        # not the Sheet's own, so normal scrolling inside the grid can't
        # re-trigger this (that was the "scroll time slow and struck"
        # regression caught on Medicine Master - same fix applied here
        # from the start this time).
        self._purchase_last_col_width = None

        def _stretch_purchase_last_column(event=None):
            try:
                if not self.purchaseTable.winfo_exists():
                    return
                self.purchaseTable.update_idletasks()
                widget_width = self.purchaseTable.winfo_width()
            except tk.TclError:
                return
            if widget_width <= 1:
                return
            fixed = sum(
                col_widths.get(c, 120) + ui_style.CENTER_PAD_WIDTH
                for c in columns[:-1]
            )
            new_width = max(
                120 + ui_style.CENTER_PAD_WIDTH,
                min(
                    widget_width - fixed - ui_style._SCROLLBAR_ALLOWANCE,
                    ui_style.MAX_STRETCH_COLUMN_WIDTH
                )
            )
            if new_width == self._purchase_last_col_width:
                return
            self._purchase_last_col_width = new_width
            try:
                self.purchaseTable.column_width(column=len(columns) - 1, width=new_width)
            except tk.TclError:
                pass

        self.purchaseTable.after(200, _stretch_purchase_last_column)
        self.frame.winfo_toplevel().bind("<Configure>", _stretch_purchase_last_column, add=True)

# ======================================
# TOTALS FOOTER - breakdown line on the left (Items/Qty/Subtotal/GST/
# Net - same figures the old single lblGrand label used to hold all
# together; the Net Amount now ALSO shows big and prominent up in the
# Ledger panel, see create_ui() above), action buttons on the right.
# ======================================

        totalFrame = tk.Frame(self.frame)
        totalFrame.pack(fill="x", padx=10, pady=10)

        tk.Label(
            totalFrame,
            textvariable=self.grand_breakdown,
            font=("Segoe UI", 10, "bold"),
            fg="#37474F"
        ).pack(side="left")

        tk.Button(
            totalFrame, text="Remove Item", bg="red", fg="white",
            width=14, command=self.remove_item
        ).pack(side="right")

        tk.Button(
            totalFrame, text="Clear All", bg="orange", fg="black",
            width=14, command=self.clear_purchase
        ).pack(side="right", padx=10)

# ======================================
# BOTTOM BUTTONS
# ======================================

        bottom = tk.Frame(self.frame)
        bottom.pack(fill="x", padx=10, pady=(0, 10))

        tk.Button(
            bottom,
            text="Save Purchase",
            bg="green",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            width=18,
            command=self.save_purchase
        ).pack(side="left", padx=10)

        tk.Button(
            bottom,
            text="Clear",
            bg="orange",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            width=15,
            command=self.clear_fields
        ).pack(side="left")

        # Export CSV/PDF - share the CURRENT invoice on screen with the
        # supplier (e.g. "give Srinivasa a CSV of this purchase") -
        # deliberately work off the items grid/fields as they stand right
        # now, not a re-query of the saved purchase table, so they're
        # usable both before AND after clicking Save Purchase.
        tk.Button(
            bottom,
            text="Export CSV",
            bg="#00695C",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            width=15,
            command=self.export_csv
        ).pack(side="left", padx=10)

        tk.Button(
            bottom,
            text="Export PDF",
            bg="#6A1B9A",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            width=15,
            command=self.export_pdf
        ).pack(side="left")

        # Export Settings - BharatERP-style "Invoice Format Setup"
        # (show/hide columns + rename headers), applies to both Export
        # CSV and Export PDF - see open_export_settings().
        tk.Button(
            bottom,
            text="⚙ Export Settings",
            bg="#455A64",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            width=16,
            command=self.open_export_settings
        ).pack(side="left", padx=10)

        tk.Button(
            bottom,
            text="Close",
            bg="red",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            width=15,
            command=self._handle_close
        ).pack(side="right", padx=10)

# ======================================
# KEYBOARD-SHORTCUT HINT STRIP - only lists shortcuts that actually
# work (see _bind_shortcuts()) - no "F4 = New Purchase" or "Ctrl+P =
# Print" like the reference layout had, since neither of those actions
# exists in this screen (no invoice-print here, and there's no
# separate "new purchase" action beyond Clear/Clear All already
# visible as buttons above).
# ======================================

        shortcuts = tk.Label(
            self.frame,
            text="ENTER = Next Field (Medicine → Batch → Expiry → Purchase → MRP → Pack Size → GST% → Qty → Add)"
                 "     F5 = Add Item     ESC = Clear Item Fields",
            bg="#263238",
            fg="#FFD54F",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            padx=10,
            pady=4
        )
        shortcuts.pack(fill="x", side="bottom")

# ======================================
# FAST-ENTRY HELPERS
# ======================================

    def _focus_next(self, widget):
        widget.focus_set()
        widget.select_range(0, tk.END)

    def open_bulk_import(self):
        BulkImportWindow(self, self.frame)

    def _update_purchase_label(self):
        """Relabels the Purchase/Rate field so it's never silently
        ambiguous about what the number the pharmacist just typed
        actually means, on top of _get_exclusive_purchase_rate() below
        actually converting it."""
        if self.inclusive_tax.get():
            self.lblPurchase.config(text="Purchase (incl. GST)")
        else:
            self.lblPurchase.config(text="Purchase (excl. GST)")

    def _on_inclusive_tax_toggle(self):
        self._update_purchase_label()
        self.calculate_total()

    def _get_exclusive_purchase_rate(self):
        """
        Converts the entered Purchase rate to GST-EXCLUSIVE before it's
        used for Total/storage, when "Inclusive of Taxes" is checked -
        matches the convention medicine_master.purchase already uses
        everywhere else in this app (billing.py's save_bill() and
        medicine_master.py's calculate_profit() both do
        purchase * (1 + gst/100) to get landed cost). Without this
        conversion, ticking the checkbox and entering an already-GST-
        included rate would get GST added a SECOND time downstream,
        inflating landed cost and silently wrecking the profit % shown
        in Medicine Master.

        Returns the raw entered rate unchanged when the checkbox is off
        (the app's normal/default convention), or when GST is 0/blank
        (nothing to strip out, and dividing by 1.0 would be a no-op
        anyway - skipped explicitly so a blank GST field never raises).
        """
        try:
            raw = float(self.purchase.get() or 0)
        except (ValueError, tk.TclError):
            raw = 0.0
        if not self.inclusive_tax.get():
            return raw
        try:
            gst_rate = float(self.gst.get() or 0)
        except (ValueError, tk.TclError):
            gst_rate = 0.0
        if gst_rate <= 0:
            return raw
        return round(raw / (1 + gst_rate / 100), 2)

    def _on_supplier_selected(self, event=None):
        """Auto-fills the read-only Address/Phone display when a
        Supplier is picked - purely informational, new in this redesign
        (the old screen showed nothing but the supplier name)."""
        name = self.supplier.get().strip()
        if not name:
            self.supplier_address.set("")
            self.supplier_phone.set("")
            return
        row = repo.get_supplier_contact(name)
        self.supplier_address.set((row[0] or "") if row else "")
        self.supplier_phone.set((row[1] or "") if row else "")

    def open_add_supplier_popup(self):
        """Quick-add a new Supplier without leaving Purchase Entry (new
        capability this redesign adds - the Supplier combobox is
        state="readonly", so previously the only way to add one was to
        go to the separate Supplier Master screen first). Only asks for
        the fields genuinely useful to fill in on the spot; everything
        else (GSTIN, DL No, Credit Period, City, Email) is left for the
        pharmacist to fill in later via Supplier Master itself, same
        "fill in details later" pattern used elsewhere in this app."""
        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring) - already modal
        # (grab_set() below), so only the look changes here.
        win = tk.Toplevel(self.frame)
        win.title("Add New Supplier")
        win.resizable(False, False)
        win.grab_set()
        # Esc key also closes this popup (same as Cancel/the window's X).
        win.bind("<Escape>", lambda event: (win.grab_release(), win.destroy()))
        win.focus_force()

        outer = ui_style.popup_header(win, "Add New Supplier", icon="🏭")

        name_var = tk.StringVar()
        mobile_var = tk.StringVar()
        address_var = tk.StringVar()

        form = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=15, pady=15)
        form.pack(fill="both", expand=True)

        def _field_kwargs():
            return dict(
                font=("Segoe UI", 10), bg=theme.SURFACE_FIELD, relief="flat",
                highlightthickness=1, highlightbackground=theme.BORDER_DEFAULT,
                highlightcolor=theme.BORDER_FOCUS,
            )

        tk.Label(
            form, text="Supplier Name *", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
        ).grid(row=0, column=0, sticky="w", pady=5)
        name_entry = tk.Entry(form, textvariable=name_var, width=28, **_field_kwargs())
        name_entry.grid(row=0, column=1, pady=5, ipady=3)
        name_entry.focus_set()

        tk.Label(
            form, text="Mobile", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
        ).grid(row=1, column=0, sticky="w", pady=5)
        tk.Entry(form, textvariable=mobile_var, width=28, **_field_kwargs()).grid(row=1, column=1, pady=5, ipady=3)

        tk.Label(
            form, text="Address", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
        ).grid(row=2, column=0, sticky="w", pady=5)
        tk.Entry(form, textvariable=address_var, width=28, **_field_kwargs()).grid(row=2, column=1, pady=5, ipady=3)

        def _save():
            name = name_var.get().strip()
            if not name:
                ui_popups.show_error(win, "Error", "Supplier Name Required")
                return
            if repo.supplier_name_exists(name):
                ui_popups.show_error(win, "Error", f'"{name}" already exists in Supplier Master.')
                return
            try:
                repo.insert_quick_supplier(name, mobile_var.get().strip(), address_var.get().strip())
            except Exception as e:
                ui_popups.show_error(win, "Database Error", str(e))
                return

            self.load_suppliers()
            self.supplier.set(name)
            self._on_supplier_selected()
            win.grab_release()
            win.destroy()

        btns = tk.Frame(form, bg=theme.SURFACE_WHITE)
        btns.grid(row=3, column=0, columnspan=2, pady=(15, 0))
        ui_style.flat_button(btns, "Save", theme.STATUS_SUCCESS, _save, width=12).pack(side="left", padx=5)
        ui_style.flat_button(
            btns, "Cancel", theme.ACCENT_NEUTRAL, lambda: (win.grab_release(), win.destroy()), width=12,
        ).pack(side="left", padx=5)

        win.protocol("WM_DELETE_WINDOW", lambda: (win.grab_release(), win.destroy()))

        # No explicit width/height (was a fixed 380x220 guess) - see
        # ui_style.center_window()'s own docstring for why sizing to
        # real packed content is safer.
        ui_style.center_window(win, parent=self.frame.winfo_toplevel())

    def open_edit_supplier_popup(self):
        """Fixes the CURRENTLY SELECTED supplier's own Supplier Master
        row (Name/Mobile/Address/GSTIN/DL No/Credit Period) without
        leaving Purchase Entry - new in the Aug 2026 invoice-export work.
        Deliberately writes to Supplier Master itself (matched by the id
        fetched when the popup opens, not by name - so renaming the
        supplier here doesn't break the WHERE clause), so a corrected
        GSTIN/address is right for every future invoice too, not just a
        one-off override for the invoice currently on screen."""
        name = self.supplier.get().strip()
        if not name:
            ui_popups.show_error(self.frame, "Error", "Select a supplier first")
            return

        row = repo.get_supplier_for_edit(name)
        if not row:
            ui_popups.show_error(self.frame, "Error", f'"{name}" not found in Supplier Master.')
            return

        sup_id, cur_name, cur_mobile, cur_addr, cur_gstin, cur_dlno, cur_credit = row

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring) - already modal
        # (grab_set() below), so only the look changes here.
        win = tk.Toplevel(self.frame)
        win.title(f"Edit Supplier - {cur_name}")
        win.resizable(False, False)
        win.grab_set()
        # Esc key also closes this popup (same as Cancel/the window's X).
        win.bind("<Escape>", lambda event: (win.grab_release(), win.destroy()))
        win.focus_force()

        outer = ui_style.popup_header(win, f"Edit Supplier - {cur_name}", icon="🏭")

        name_var = tk.StringVar(value=cur_name or "")
        mobile_var = tk.StringVar(value=cur_mobile or "")
        address_var = tk.StringVar(value=cur_addr or "")
        gstin_var = tk.StringVar(value=cur_gstin or "")
        dlno_var = tk.StringVar(value=cur_dlno or "")
        credit_var = tk.StringVar(value=str(cur_credit or 0))

        form = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=15, pady=15)
        form.pack(fill="both", expand=True)

        fields = [
            ("Supplier Name *", name_var, 28),
            ("Mobile", mobile_var, 28),
            ("Address", address_var, 28),
            ("GSTIN", gstin_var, 28),
            ("DL No", dlno_var, 28),
            ("Credit Period (days)", credit_var, 10),
        ]
        entries = []
        for i, (label, var, width) in enumerate(fields):
            tk.Label(
                form, text=label, bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
            ).grid(row=i, column=0, sticky="w", pady=5)
            e = tk.Entry(
                form, textvariable=var, width=width, font=("Segoe UI", 10),
                bg=theme.SURFACE_FIELD, relief="flat", highlightthickness=1,
                highlightbackground=theme.BORDER_DEFAULT, highlightcolor=theme.BORDER_FOCUS,
            )
            e.grid(row=i, column=1, pady=5, sticky="w", ipady=3)
            entries.append(e)
        entries[0].focus_set()

        def _save():
            new_name = name_var.get().strip()
            if not new_name:
                ui_popups.show_error(win, "Error", "Supplier Name Required")
                return
            try:
                credit_days = int(float(credit_var.get() or 0))
            except (ValueError, TypeError):
                ui_popups.show_error(win, "Error", "Credit Period must be a number")
                return

            # Renaming to a name another supplier already has would
            # silently merge two distinct suppliers - blocked, same
            # duplicate-name guard the Add popup already uses.
            if repo.supplier_name_exists_excluding(new_name, sup_id):
                ui_popups.show_error(win, "Error", f'"{new_name}" already exists in Supplier Master.')
                return
            try:
                repo.update_supplier_full(
                    sup_id, new_name, mobile_var.get().strip(), address_var.get().strip(),
                    gstin_var.get().strip(), dlno_var.get().strip(), credit_days
                )
            except Exception as e:
                ui_popups.show_error(win, "Database Error", str(e))
                return

            self.load_suppliers()
            self.supplier.set(new_name)
            self._on_supplier_selected()
            win.grab_release()
            win.destroy()

        btns = tk.Frame(form, bg=theme.SURFACE_WHITE)
        btns.grid(row=len(fields), column=0, columnspan=2, pady=(15, 0))
        ui_style.flat_button(btns, "Save", theme.STATUS_SUCCESS, _save, width=12).pack(side="left", padx=5)
        ui_style.flat_button(
            btns, "Cancel", theme.ACCENT_NEUTRAL, lambda: (win.grab_release(), win.destroy()), width=12,
        ).pack(side="left", padx=5)

        win.protocol("WM_DELETE_WINDOW", lambda: (win.grab_release(), win.destroy()))

        # No explicit width/height (was a fixed 400x320 guess) - see
        # ui_style.center_window()'s own docstring for why sizing to
        # real packed content is safer.
        ui_style.center_window(win, parent=self.frame.winfo_toplevel())

# ======================================
# KEYBOARD SHORTCUTS (F5 Add Item, Esc Clear Item Fields)
# ======================================

    def _bind_shortcuts(self):
        """F5/Escape bound on the shared root window (not bind_all), so
        they fire whenever any widget on THIS window has focus but never
        steal keys from a separate Toplevel popup (Add New Supplier,
        Bulk Import, etc each run in their own window). Same pattern as
        billing.py's _bind_shortcuts() - proven safe there already."""
        top = self.frame.winfo_toplevel()
        self._shortcut_map = {
            "<F5>": lambda e: self.add_item(),
            "<Escape>": lambda e: self.clear_item_fields(),
        }
        for seq, handler in self._shortcut_map.items():
            top.bind(seq, handler)

    def _unbind_shortcuts(self, event=None):
        if event is not None and event.widget is not self.frame:
            return
        top = self.frame.winfo_toplevel()
        for seq in getattr(self, "_shortcut_map", {}):
            try:
                top.unbind(seq)
            except Exception:
                pass

# ======================================
# SAVE PURCHASE
# ======================================

    def save_purchase(self):
        if not self.purchaseTable.get_sheet_data():
            ui_popups.show_error(self.frame, "Error", "No Items Added")
            return

        bill_no = self.bill_no.get().strip()
        if not bill_no:
            ui_popups.show_error(self.frame, "Error", "Bill number cannot be empty.")
            return

        # Reads the item grid into plain dicts (UI-shape parsing stays
        # here); the actual duplicate-bill check, INSERT-per-item and
        # medicine_master stock/pack_size UPDATE all happen inside
        # repo.save_purchase() as one transaction - see
        # purchase_repository.py's module docstring for why that stayed
        # a single function instead of several small repo calls.
        items = []
        for values in self.purchaseTable.get_sheet_data():
            medicine = values[0]
            batch = values[1]
            expiry = values[2]
            hsn = values[3]
            try:
                gst = float(values[4] or 0)
            except (ValueError, TypeError):
                gst = 0.0
            purchase = float(values[5])
            sale = float(values[6])
            pack_size = str(values[7]).strip() if values[7] is not None else ""
            # டெசிமல் புள்ளியுடன் ('30.0') வந்தாலும் எரர் வராமல் safely int-ஆக மாற்றுவது
            try:
                qty = int(float(values[8]))
            except (ValueError, TypeError):
                qty = 1
            total = float(values[9])
            items.append({
                "medicine": medicine, "batch": batch, "expiry": expiry, "hsn": hsn,
                "gst": gst, "purchase": purchase, "sale": sale,
                "pack_size": pack_size, "qty": qty, "total": total,
            })

        try:
            repo.save_purchase(
                bill_no,
                self.bill_date.get(),
                self.supplier.get(),
                self.supplier_invoice_no.get().strip(),
                self.supplier_invoice_date.get().strip(),
                items
            )
            ui_popups.show_info(self.frame, "Success", "Purchase Saved Successfully")
            self.clear_purchase()
            self.generate_bill_no()

        except repo.DuplicateBillNumber:
            # --- 💡 டூப்ளிகேட் பில் சோதனை (Duplicate Bill Check) ---
            ui_popups.show_error(self.frame, "Error", "இந்த பில் எண் ஏற்கனவே பதிவு செய்யப்பட்டுள்ளது!")
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", str(e))

# ======================================
# EXPORT (CSV / PDF) - share the current purchase invoice with the
# supplier ("give Srinivasa a CSV/PDF of this invoice"). Both work off
# the items grid + ledger fields exactly as they stand on screen right
# now, not a re-query of the saved `purchase` table - so they're usable
# whether or not "Save Purchase" has been clicked yet. Added Aug 2026,
# see get_shop_details()/get_supplier_export_details()/_get_export_items()
# below for the shared data-gathering they both build on.
# ======================================

    def get_shop_details(self):
        """Same settings columns billing.py's own get_shop_details() reads
        (shop_name/address/city/phone/gstin/dl20/dl21) - kept as a local
        copy rather than importing billing.py just for this, since
        Purchase Entry has no other dependency on that module."""
        row = repo.get_shop_details_row()
        if row and row[0]:
            return {
                "name": row[0], "address": row[1] or "", "city": row[2] or "",
                "phone": row[3] or "", "gstin": row[4] or "",
                "dl20": row[5] or "", "dl21": row[6] or "",
            }
        return {"name": "LIFE CARE PHARMACY", "address": "", "city": "", "phone": "",
                "gstin": "", "dl20": "", "dl21": ""}

    def get_supplier_export_details(self):
        name = self.supplier.get().strip()
        details = {"name": name, "address": "", "phone": "", "gstin": "", "dlno": "", "credit_days": 0}
        if not name:
            return details
        row = repo.get_supplier_export_row(name)
        if row:
            details["address"] = row[0] or ""
            details["phone"] = row[1] or ""
            details["gstin"] = row[2] or ""
            details["dlno"] = row[3] or ""
            details["credit_days"] = int(row[4] or 0)
        return details

    def _get_formatted_description(self, medicine_name):
        """"Description of Goods" convention - brand name + generic
        composition in brackets, e.g. "Paracetamol 500mg Tab (Paracetamol)".
        Matches billing.py's Print Bill exactly (see Billing.
        get_formatted_billing_item() there) - kept as a local copy
        instead of importing billing.py, since Purchase Entry has no
        other dependency on that module.

        Returns (formatted_text, generic_text, missing_generic) - the
        PDF's Description of Goods column uses formatted_text (brand +
        generic combined, for a human reading the invoice); CSV export
        needs the BARE medicine_name and generic_text kept as separate
        columns instead (see export_csv()) - a combined "Name (Generic)"
        string in one cell broke re-importing the CSV back into Bulk
        Import, since the whole combined string got treated as a brand-
        new medicine name instead of matching the existing one. Falls
        back with generic_text="" when Medicine Master has no Generic on
        file (new/needs-review entries, or a brand Brand Master has no
        mapping for - see offer_create_medicine()) - missing_generic
        tells the caller so export_csv()/export_pdf() can warn before
        exporting (see _check_missing_generics())."""
        row = repo.get_medicine_generic(medicine_name)
        if row and row[0]:
            generic_text = row[0].strip()
            return f"{medicine_name} ({generic_text})", generic_text, False
        return medicine_name, "", True

    def _get_export_items(self):
        """Reads the item grid + computes per-item/overall GST from the
        real per-row GST% column (see calculate_grand_total()'s bug-fix
        comment - same logic, duplicated here rather than shared because
        this also needs the per-item breakdown for the table rows, not
        just the totals). SGST/CGST is a simple 50/50 split of the total
        GST, matching the reference layout - correct for an INTRA-STATE
        purchase (supplier also in Tamil Nadu, the normal case here); an
        inter-state purchase should legally be IGST instead of SGST+CGST,
        which this does not distinguish - a simplification worth knowing
        about if a supplier from outside Tamil Nadu is ever billed this
        way."""
        items = []
        subtotal = 0.0
        tax_total = 0.0
        for values in self.purchaseTable.get_sheet_data():
            # values[:10] - not a bare unpack of `values` itself. The
            # grid gained an 11th "Status" column (see add_item()); a
            # fixed 10-name unpack of the full row would raise "too many
            # values to unpack" the moment that column exists. Slicing
            # to the first 10 keeps this working regardless of whatever
            # trailing display-only columns the grid picks up later.
            medicine, batch, expiry, hsn, gst_pct, rate, mrp, pack_size, qty, line_total = values[:10]
            try:
                gst_pct_f = float(gst_pct or 0)
            except (ValueError, TypeError):
                gst_pct_f = 0.0
            try:
                qty_i = int(float(qty))
            except (ValueError, TypeError):
                qty_i = 0
            try:
                rate_f = float(rate)
            except (ValueError, TypeError):
                rate_f = 0.0
            try:
                mrp_f = float(mrp)
            except (ValueError, TypeError):
                mrp_f = 0.0
            try:
                line_f = float(line_total)
            except (ValueError, TypeError):
                line_f = 0.0
            pack_size_s = str(pack_size).strip() if pack_size is not None else ""

            desc, composition, missing_generic = self._get_formatted_description(medicine)
            item_gst = round(line_f * gst_pct_f / 100, 2)
            items.append({
                "medicine": medicine, "description": desc, "composition": composition,
                "missing_generic": missing_generic,
                "batch": batch, "expiry": expiry, "pack_size": pack_size_s or "-",
                "hsn": hsn or "-", "gst_pct": gst_pct_f, "qty": qty_i,
                "rate": rate_f, "mrp": mrp_f, "amount": line_f,
            })
            subtotal += line_f
            tax_total += item_gst

        subtotal = round(subtotal, 2)
        tax_total = round(tax_total, 2)
        sgst = round(tax_total / 2, 2)
        cgst = round(tax_total - sgst, 2)
        grand_total = round(subtotal + tax_total, 2)
        return items, subtotal, sgst, cgst, grand_total

    def _export_default_filename(self, ext):
        supplier_part = (self.supplier.get().strip() or "Supplier").replace(" ", "_")
        bill_part = (self.bill_no.get().strip() or "Draft").replace(" ", "_")
        return f"Purchase_{supplier_part}_{bill_part}.{ext}"

    def get_export_column_config(self):
        """Reads the saved column show/hide + label choices (see
        DEFAULT_EXPORT_COLUMNS's module docstring). Merges by key onto
        the factory default list rather than trusting the saved JSON
        as-is - if the app ever adds a new exportable column in a future
        update, an old saved config (missing that key) still shows it
        with its default visible/label instead of silently dropping it,
        and any unknown/stale key in old saved JSON is just ignored.
        Best-effort: any read/parse failure (no settings row yet, no
        purchase_export_columns column yet, corrupt JSON) falls back to
        the plain default - a broken export-settings save should never
        block using Export CSV/PDF at all."""
        saved_json = repo.get_purchase_export_columns_json()

        saved_by_key = {}
        if saved_json:
            try:
                for entry in json.loads(saved_json):
                    if isinstance(entry, dict) and "key" in entry:
                        saved_by_key[entry["key"]] = entry
            except (ValueError, TypeError):
                pass

        merged = []
        for default_col in DEFAULT_EXPORT_COLUMNS:
            key = default_col["key"]
            saved = saved_by_key.get(key, {})
            merged.append({
                "key": key,
                "label": (saved.get("label") or "").strip() or default_col["label"],
                "visible": bool(saved["visible"]) if "visible" in saved else default_col["visible"],
            })
        return merged

    def save_export_column_config(self, columns):
        """Best-effort persistence, same UPDATE-only pattern as
        dashboard.py's set_dark_mode_pref() - if `settings` has zero rows
        (shop details never saved once via the Settings screen), this
        affects 0 rows and the choice just won't be remembered on next
        launch, which is fine; it must never block using Export Settings
        this session."""
        repo.save_purchase_export_columns_json(json.dumps(columns))

    def open_export_settings(self):
        """BharatERP-style 'Invoice Format Setup' - lets the pharmacist
        show/hide individual export columns and rename their headers,
        applied to BOTH CSV and PDF export identically. "Medicine" and
        "Amount" aren't listed - always shown, an invoice without an
        item name or its price isn't useful to anyone."""
        columns = self.get_export_column_config()

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring) - already modal
        # (grab_set() below), so only the look changes here.
        win = tk.Toplevel(self.frame)
        win.title("Export Settings - CSV / PDF Columns")
        win.resizable(False, False)
        win.grab_set()
        # Esc key also closes this popup (same as Cancel/the window's X).
        win.bind("<Escape>", lambda event: (win.grab_release(), win.destroy()))
        win.focus_force()

        outer = ui_style.popup_header(win, "Export Settings - CSV / PDF Columns", icon="📄")

        tk.Label(
            outer, text="Choose which columns appear in Export CSV / Export PDF,\n"
                      "and rename their headers if you'd like.",
            bg=theme.SURFACE_WHITE, fg=theme.TEXT_PRIMARY, justify="left", padx=15, pady=10
        ).pack(anchor="w")

        body = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=15)
        body.pack(fill="both", expand=True)

        tk.Label(
            body, text="Show", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL, font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, padx=(0, 10))
        tk.Label(
            body, text="Column", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL, font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=1, sticky="w")
        tk.Label(
            body, text="Header Label", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL, font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=2, padx=(15, 0), sticky="w")

        show_vars = {}
        label_vars = {}
        for i, col in enumerate(columns, start=1):
            show_vars[col["key"]] = tk.BooleanVar(value=col["visible"])
            tk.Checkbutton(
                body, variable=show_vars[col["key"]], bg=theme.SURFACE_WHITE,
                activebackground=theme.SURFACE_WHITE,
            ).grid(row=i, column=0, pady=3)
            tk.Label(
                body, text=col["key"], bg=theme.SURFACE_WHITE, fg=theme.TEXT_PRIMARY,
            ).grid(row=i, column=1, sticky="w", pady=3)
            label_vars[col["key"]] = tk.StringVar(value=col["label"])
            tk.Entry(
                body, textvariable=label_vars[col["key"]], width=22, font=("Segoe UI", 10),
                bg=theme.SURFACE_FIELD, relief="flat", highlightthickness=1,
                highlightbackground=theme.BORDER_DEFAULT, highlightcolor=theme.BORDER_FOCUS,
            ).grid(row=i, column=2, padx=(15, 0), pady=3, sticky="w", ipady=2)

        def _save():
            new_columns = []
            for col in DEFAULT_EXPORT_COLUMNS:
                key = col["key"]
                new_columns.append({
                    "key": key,
                    "label": label_vars[key].get().strip() or col["label"],
                    "visible": bool(show_vars[key].get()),
                })
            self.save_export_column_config(new_columns)
            win.grab_release()
            win.destroy()
            ui_popups.show_info(self.frame, "Saved", "Export Settings saved. This applies to your next Export CSV / Export PDF.")

        def _reset_default():
            for col in DEFAULT_EXPORT_COLUMNS:
                show_vars[col["key"]].set(col["visible"])
                label_vars[col["key"]].set(col["label"])

        btns = tk.Frame(outer, bg=theme.SURFACE_WHITE)
        btns.pack(fill="x", padx=15, pady=15)
        ui_style.flat_button(btns, "Save", theme.STATUS_SUCCESS, _save, width=12).pack(side="left")
        ui_style.flat_button(
            btns, "Reset to Default", theme.ACCENT_NEUTRAL, _reset_default, width=16,
        ).pack(side="left", padx=8)
        ui_style.flat_button(
            btns, "Cancel", theme.ACCENT_NEUTRAL, lambda: (win.grab_release(), win.destroy()), width=12,
        ).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", lambda: (win.grab_release(), win.destroy()))

        # No explicit width/height (was a fixed 460x430 guess) - see
        # ui_style.center_window()'s own docstring for why sizing to
        # real packed content is safer.
        ui_style.center_window(win, parent=self.frame.winfo_toplevel())

    @staticmethod
    def _csv_cell_value(key, item):
        """Raw-ish values for CSV (numbers stay numbers where sensible -
        a CSV opened in Excel is more useful that way than pre-formatted
        text)."""
        if key == "composition":
            return item["composition"]
        if key == "hsn":
            return item["hsn"]
        if key == "batch":
            return item["batch"]
        if key == "expiry":
            return item["expiry"]
        if key == "pack_size":
            return item["pack_size"]
        if key == "gst_pct":
            return item["gst_pct"]
        if key == "qty":
            return item["qty"]
        if key == "rate":
            return f"{item['rate']:.2f}"
        if key == "mrp":
            return f"{item['mrp']:.2f}"
        return ""

    @staticmethod
    def _pdf_cell_value(key, item):
        """Short display strings for the PDF table (every cell is
        centred text there, unlike CSV)."""
        if key == "composition":
            return item["composition"] or "-"
        if key == "hsn":
            return item["hsn"]
        if key == "batch":
            return item["batch"]
        if key == "expiry":
            return item["expiry"]
        if key == "pack_size":
            return item["pack_size"]
        if key == "gst_pct":
            return f"{item['gst_pct']:g}"
        if key == "qty":
            return str(item["qty"])
        if key == "rate":
            return f"{item['rate']:.2f}"
        if key == "mrp":
            return f"{item['mrp']:.2f}"
        return ""

    def _check_missing_generics(self, items):
        """Warns before export if any item has no Generic/Composition on
        file (see _get_formatted_description()) - user's own choice over
        the silent-fallback alternative, since a purchase invoice sent to
        a supplier/accountant with unexplained blank compositions could
        look like a data-entry mistake rather than a genuinely unmapped
        brand. Returns True to proceed, False if the user cancels."""
        missing = [it["medicine"] for it in items if it["missing_generic"]]
        if not missing:
            return True
        shown = "\n".join(f"  • {n}" for n in missing[:10])
        more = f"\n  ...and {len(missing) - 10} more" if len(missing) > 10 else ""
        return ui_popups.show_confirmation(self.frame, 
            "Missing Generic/Composition",
            f"{len(missing)} item(s) have no Generic/Composition on file in "
            f"Medicine Master:\n\n{shown}{more}\n\n"
            "They will export WITHOUT composition shown (just the brand "
            "name). Fill in Generic later in Medicine Master to fix this "
            "on future exports.\n\nContinue export anyway?"
        )

    def _validate_before_export(self):
        if not self.purchaseTable.get_sheet_data():
            ui_popups.show_error(self.frame, "Error", "No Items Added")
            return False
        if not self.supplier.get().strip():
            ui_popups.show_error(self.frame, "Error", "Select Supplier")
            return False
        return True

    def export_csv(self):
        if not self._validate_before_export():
            return

        shop = self.get_shop_details()
        supplier = self.get_supplier_export_details()
        items, subtotal, sgst, cgst, grand_total = self._get_export_items()

        if not self._check_missing_generics(items):
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=self._export_default_filename("csv"),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not file_path:
            return

        # "Medicine" is always shown, deliberately the BARE name (not the
        # "Name (Generic)" combined text the PDF's Description of Goods
        # column shows) - this is what makes the file re-importable:
        # importing this back via Bulk Import's "Import from File" button
        # (header-aware, see spreadsheet_import.py) matches "Medicine"
        # against the existing medicine_master row correctly, instead of
        # a combined string being read as an unrecognised brand-new
        # medicine. Everything else is controlled by Export Settings (see
        # open_export_settings()) - hiding a column here means that data
        # simply isn't in the exported file, same trade-off as any
        # configurable export.
        visible_cols = [c for c in self.get_export_column_config() if c["visible"]]
        headers = ["SI", "Medicine"] + [c["label"] for c in visible_cols] + ["Amount"]
        pad = [""] * (len(headers) - 2)  # blank cells under SI..last-visible-column for the totals rows

        try:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow([shop["name"]])
                addr_line = ", ".join(filter(None, [shop["address"], shop["city"]]))
                if addr_line:
                    w.writerow([addr_line])
                if shop["phone"]:
                    w.writerow([f"Phone: {shop['phone']}"])
                if shop["gstin"]:
                    w.writerow([f"GSTIN: {shop['gstin']}"])
                w.writerow([])
                w.writerow(["PURCHASE INVOICE"])
                w.writerow([])
                w.writerow(["Invoice No", self.bill_no.get(), "", "Supplier's Invoice No", self.supplier_invoice_no.get() or "-"])
                w.writerow(["Invoice Date", self.bill_date.get(), "", "Supplier's Invoice Date", self.supplier_invoice_date.get() or "-"])
                w.writerow(["Supplier", supplier["name"], "", "GSTIN", supplier["gstin"] or "-"])
                if supplier["address"]:
                    w.writerow(["Address", supplier["address"]])
                w.writerow([])
                w.writerow(headers)
                for i, it in enumerate(items, 1):
                    row = [i, it["medicine"]] + [self._csv_cell_value(c["key"], it) for c in visible_cols] + [f"{it['amount']:.2f}"]
                    w.writerow(row)
                w.writerow([])
                w.writerow(pad + ["Subtotal", f"{subtotal:.2f}"])
                w.writerow(pad + ["SGST", f"{sgst:.2f}"])
                w.writerow(pad + ["CGST", f"{cgst:.2f}"])
                w.writerow(pad + ["Grand Total", f"{grand_total:.2f}"])
        except Exception as e:
            ui_popups.show_error(self.frame, "Export Error", str(e))
            return

        audit_log.log_action("Purchase", "Export CSV", f"Exported invoice for {supplier['name']} ({self.bill_no.get()}) to CSV")
        ui_popups.show_info(self.frame, "Exported", f"Saved:\n{file_path}")

    def export_pdf(self):
        if not self._validate_before_export():
            return

        from reportlab.pdfgen import canvas as pdf_canvas
        from reportlab.lib.pagesizes import A4

        shop = self.get_shop_details()
        supplier = self.get_supplier_export_details()
        items, subtotal, sgst, cgst, grand_total = self._get_export_items()

        if not self._check_missing_generics(items):
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=self._export_default_filename("pdf"),
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not file_path:
            return

        visible_cols = [c for c in self.get_export_column_config() if c["visible"]]

        try:
            self._draw_purchase_invoice_pdf(file_path, shop, supplier, items, subtotal, sgst, cgst, grand_total, visible_cols)
        except Exception as e:
            ui_popups.show_error(self.frame, "Export Error", str(e))
            return

        audit_log.log_action("Purchase", "Export PDF", f"Exported invoice for {supplier['name']} ({self.bill_no.get()}) to PDF")
        ui_popups.show_info(self.frame, "Exported", f"Saved:\n{file_path}")

    def _draw_purchase_invoice_pdf(self, file_path, shop, supplier, items, subtotal, sgst, cgst, grand_total, visible_cols):
        """Bordered A4 layout matching the approved mockup (shop header /
        Invoice No + Supplier's Invoice No / Supplier + Terms box / item
        table with real grid lines / GST totals box). Uses reportlab's
        low-level canvas (rect/line/drawString), same approach billing.py's
        generate_invoice() already uses for the sales-bill PDF, extended
        here with actual grid borders and pagination for long item lists
        (a bulk-imported purchase invoice can easily have 30+ lines).

        visible_cols: the Export Settings-configured column list (see
        open_export_settings()) - SI/Description of Goods/Amount are
        always drawn regardless (not part of this list at all); every
        other column here becomes an extra table column, in order.
        "composition" is handled specially - it doesn't get its own PDF
        column, it controls whether Description of Goods shows the
        "(Generic)" suffix at all (see desc_source below)."""
        from reportlab.pdfgen import canvas as pdf_canvas
        from reportlab.lib.pagesizes import A4

        W, H = A4
        M = 30
        c = pdf_canvas.Canvas(file_path, pagesize=A4)

        composition_visible = any(col["key"] == "composition" for col in visible_cols)
        pdf_data_cols = [col for col in visible_cols if col["key"] != "composition"]

        cols = [("SI", 18), ("DESCRIPTION OF GOODS", 190)]
        cols += [(col["label"].upper(), _EXPORT_COL_PDF_WIDTH.get(col["key"], 45)) for col in pdf_data_cols]
        cols += [("AMOUNT", 54)]
        table_left = M + 10
        table_right = W - M - 10
        total_w = sum(w for _, w in cols)
        scale = (table_right - table_left) / total_w
        cols = [(name, w * scale) for name, w in cols]
        x_pos = [table_left]
        for _, w in cols:
            x_pos.append(x_pos[-1] + w)

        header_h = 16
        row_h = 15

        def fit_text(text, max_width, font, size):
            """Truncates with an ellipsis based on ACTUAL rendered pixel
            width (reportlab's stringWidth), not a guessed character
            count - a fixed [:N] slice was cutting long brand+generic
            combination-drug descriptions (e.g. "Amoxicillin + Clavulanic
            Acid") mid-word regardless of how much room the column
            genuinely had. Returns the text unchanged if it already fits."""
            if c.stringWidth(text, font, size) <= max_width:
                return text
            while text and c.stringWidth(text + "...", font, size) > max_width:
                text = text[:-1]
            return text + "..." if text else "..."

        def draw_page_frame_and_header(continued=False):
            c.setLineWidth(1.2)
            c.rect(M, M, W - 2 * M, H - 2 * M)
            y = H - M - 22
            c.setFont("Helvetica-Bold", 16)
            c.drawCentredString(W / 2, y, shop["name"])
            y -= 15
            c.setFont("Helvetica", 8.5)
            addr_line = ", ".join(filter(None, [shop["address"], shop["city"]]))
            if addr_line:
                c.drawCentredString(W / 2, y, addr_line)
                y -= 11
            phone_gst = "   |   ".join(filter(None, [
                f"Phone: {shop['phone']}" if shop["phone"] else "",
                f"GSTIN: {shop['gstin']}" if shop["gstin"] else "",
            ]))
            if phone_gst:
                c.drawCentredString(W / 2, y, phone_gst)
                y -= 11
            c.line(M + 10, y, W - M - 10, y)
            y -= 14
            c.setFont("Helvetica-Bold", 12)
            c.drawCentredString(W / 2, y, "PURCHASE INVOICE" + ("  (contd.)" if continued else ""))
            y -= 16
            c.line(M + 10, y, W - M - 10, y)
            y -= 14
            return y

        y = draw_page_frame_and_header()

        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(table_left, y, "Invoice No.")
        c.drawString(table_left + 65, y, f": {self.bill_no.get() or '-'}")
        c.drawString(W / 2 + 5, y, "Supplier's Invoice No.")
        c.drawString(W / 2 + 100, y, f": {self.supplier_invoice_no.get() or '-'}")
        y -= 13
        c.drawString(table_left, y, "Invoice Date")
        c.drawString(table_left + 65, y, f": {self.bill_date.get() or '-'}")
        c.drawString(W / 2 + 5, y, "Supplier's Invoice Date")
        c.drawString(W / 2 + 100, y, f": {self.supplier_invoice_date.get() or '-'}")
        y -= 18

        box_top = y
        box_h = 58
        c.setLineWidth(0.7)
        c.rect(table_left, box_top - box_h, table_right - table_left, box_h)
        c.line(W / 2, box_top - box_h, W / 2, box_top)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(table_left + 5, box_top - 11, "SUPPLIER DETAILS")
        c.drawString(W / 2 + 5, box_top - 11, "TERMS")
        c.setFont("Helvetica", 8)
        c.drawString(table_left + 5, box_top - 24, supplier["name"] or "-")
        c.drawString(table_left + 5, box_top - 36, (supplier["address"] or "-")[:60])
        c.drawString(table_left + 5, box_top - 48, f"GSTIN: {supplier['gstin'] or '-'}")
        c.drawString(W / 2 + 5, box_top - 24, f"Credit Period: {supplier['credit_days']} Days")
        due_date = "-"
        try:
            bill_dt = datetime.strptime(self.bill_date.get().strip(), "%d-%m-%Y")
            due_date = (bill_dt + timedelta(days=supplier["credit_days"])).strftime("%d-%m-%Y")
        except ValueError:
            pass
        c.drawString(W / 2 + 5, box_top - 36, f"Due Date: {due_date}")
        y = box_top - box_h - 16

        def draw_table_header(y):
            c.setFillColorRGB(0.08, 0.40, 0.75)
            c.rect(table_left, y - header_h, table_right - table_left, header_h, fill=1, stroke=0)
            c.setFillColorRGB(1, 1, 1)
            c.setFont("Helvetica-Bold", 7.5)
            for i, (name, w) in enumerate(cols):
                cx = (x_pos[i] + x_pos[i + 1]) / 2
                c.drawCentredString(cx, y - header_h + 5, name)
            c.setFillColorRGB(0, 0, 0)
            return y - header_h

        y = draw_table_header(y)
        table_top = y
        c.setFont("Helvetica", 7.5)

        for idx, it in enumerate(items, 1):
            # Page-break: leave room for the totals box + footer (~140pt)
            if y - row_h < M + 140:
                c.line(table_left, y, table_right, y)
                for xp in x_pos:
                    c.line(xp, table_top, xp, y)
                c.rect(table_left, y, table_right - table_left, table_top - y)
                c.showPage()
                y = draw_page_frame_and_header(continued=True)
                y = draw_table_header(y)
                table_top = y
                c.setFont("Helvetica", 7.5)

            desc_font, desc_size = "Helvetica", 6.8
            desc_max_w = (x_pos[2] - x_pos[1]) - 8  # column 1's own width, minus padding
            # composition_visible (from Export Settings) decides whether
            # the "(Generic)" suffix is included here at all, rather than
            # Composition being its own PDF column - see this method's
            # docstring.
            desc_source = it["description"] if composition_visible else it["medicine"]
            desc_text = fit_text(desc_source, desc_max_w, desc_font, desc_size)

            row_vals = (
                [str(idx), desc_text]
                + [self._pdf_cell_value(col["key"], it) for col in pdf_data_cols]
                + [f"{it['amount']:.2f}"]
            )
            for i, val in enumerate(row_vals):
                if i == 1:
                    c.setFont(desc_font, desc_size)
                    c.drawString(x_pos[i] + 4, y - row_h + 5, val)
                    c.setFont("Helvetica", 7.5)
                else:
                    cx = (x_pos[i] + x_pos[i + 1]) / 2
                    c.drawCentredString(cx, y - row_h + 5, val)
            y -= row_h

        # Close out the table grid for whichever page it ended on
        c.line(table_left, y, table_right, y)
        for xp in x_pos:
            c.line(xp, table_top, xp, y)
        c.rect(table_left, y, table_right - table_left, table_top - y)

        y -= 18
        tot_x = table_right - 170
        c.setFont("Helvetica-Bold", 9)
        for label, val in (("Subtotal", subtotal), ("SGST", sgst), ("CGST", cgst)):
            c.drawString(tot_x, y, label)
            c.drawRightString(table_right, y, f"{val:,.2f}")
            y -= 14
        y -= 4
        c.setFillColorRGB(0.95, 0.96, 0.98)
        c.rect(tot_x - 8, y - 4, table_right - tot_x + 8, 20, fill=1, stroke=0)
        c.setFillColorRGB(0.08, 0.40, 0.75)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(tot_x, y + 2, "GRAND TOTAL")
        c.drawRightString(table_right, y + 2, f"Rs. {grand_total:,.2f}")
        c.setFillColorRGB(0, 0, 0)

        y -= 40
        c.setFont("Helvetica", 8)
        c.line(table_left, y, table_left + 150, y)
        c.drawString(table_left, y - 10, "Receiver's Signature")
        c.line(table_right - 150, y, table_right, y)
        c.drawString(table_right - 150, y - 10, "Authorized Signatory")

        c.setFont("Helvetica", 7)
        c.drawCentredString(W / 2, M + 12, "This is a computer generated purchase invoice - Life Care Pharmacy ERP")

        c.save()

# ======================================
# CLEAR
# ======================================

    def clear_fields(self):
        self.clear_purchase()

# ======================================
# LOAD SUPPLIERS & MEDICINES
# ======================================

    def load_suppliers(self):
        try:
            self.cmbSupplier["values"] = repo.list_supplier_names()
        except:
            self.cmbSupplier["values"] = []

    def load_medicines(self):
        self._medicine_names = repo.list_medicine_names()
        self.cmbMedicine["values"] = self._medicine_names

    def on_medicine_keyrelease(self, event=None):
        text = self.medicine.get().lower()
        if text == "":
            self.cmbMedicine["values"] = self._medicine_names
            return
        matches = [n for n in self._medicine_names if text in n.lower()]
        self.cmbMedicine["values"] = matches

    def _confirm_purchase_medicine(self, event=None):
        """bind_search_combo()'s on_confirm for self.cmbMedicine - always
        advances to Batch once real text is present (unlike Billing's
        equivalent, an unrecognized name is a legitimate "new medicine"
        purchase here, not an error - see fetch_medicine() below and
        offer_create_medicine() in add_item()); only a totally blank box
        is not advanced past."""
        if not self.medicine.get().strip():
            return False
        self.fetch_medicine(event)
        return True

    def fetch_medicine(self, event=None):
        row = repo.get_medicine_defaults(self.medicine.get())

        if row:
            self.batch.set(row[0] or "")
            self.expiry.set(row[1] or "")
            self.purchase.set(row[2] or 0)
            self.sale.set(row[3] or 0)
            self.gst.set(row[4] or 0)
            self.hsn.set(row[5] or "")
            # "1" fallback here is fine - it's what's SHOWN/editable, not
            # what silently gets written back (see save_purchase()'s
            # COALESCE-protected UPDATE below, matching the Aug 2026
            # bulk_import.py fix for the same corruption risk).
            self.pack_size.set(row[6] or "1")
            self.qty.set("1")
            total = float(row[2] or 0) * 1
            self.total.set(f"{total:.2f}")

    def calculate_total(self, *args):
        try:
            rate = self._get_exclusive_purchase_rate()
            total = rate * int(self.qty.get())
            self.total.set(f"{total:.2f}")
        except Exception:
            self.total.set("0")

    def calculate_grand_total(self):
        total_items = 0
        total_qty = 0
        subtotal = 0.0
        tax_amount = 0.0

        # BUG FIX (Aug 2026 invoice-export work): this used to apply a
        # flat, hardcoded 5% GST to the whole subtotal regardless of what
        # was actually typed per item - wrong the moment any line had a
        # 12%/18%/28% medicine (common - most tablets/syrups are 12%,
        # many devices/cosmetics are 18%). GST% is now a real per-row
        # column in the grid (see add_item()), so each line's own tax is
        # computed and summed here instead of guessed.
        for values in self.purchaseTable.get_sheet_data():
            try:
                gst_pct = float(values[4] or 0)
            except (ValueError, TypeError):
                gst_pct = 0.0

            try:
                qty = int(float(values[8]))
            except (ValueError, TypeError):
                qty = 0

            try:
                line_total = float(values[9])
            except (ValueError, TypeError):
                line_total = 0.0

            total_items += 1
            total_qty += qty
            subtotal += line_total
            tax_amount += round(line_total * gst_pct / 100, 2)

        tax_amount = round(tax_amount, 2)
        net_amount = round(subtotal + tax_amount, 2)

        # Big Net Amount top-right in the Ledger panel (lblGrand) + the
        # full Items/Qty/Subtotal/GST breakdown in the totals footer
        # (grand_breakdown) - split from the single combined label the
        # old layout used, so the most important number (what you're
        # about to pay) is prominent, with the detail still available
        # just below the item grid.
        if hasattr(self, 'lblGrand') and self.lblGrand:
            self.lblGrand.config(text=f"₹ {net_amount:,.2f}")
        self.grand_breakdown.set(
            f"Total Items: {total_items}  |  Total Qty: {total_qty}  |  "
            f"Subtotal: ₹ {subtotal:,.2f}  |  GST: ₹ {tax_amount:,.2f}  |  "
            f"Net Amount: ₹ {net_amount:,.2f}"
        )

    def _purchase_item_status(self, expiry_str):
        """Batch-wise expiry status for the item grid's Status column -
        Expired / Expiring Soon / OK. Reuses the exact same rules already
        used elsewhere in the app instead of inventing a third threshold:
        the "Expired" cut (expiry month/year before the current month)
        matches medicine_master.py's _render_medicine_rows(), and the 90-
        day "Expiring Soon" window matches dashboard.py/reports.py's own
        `cutoff = (datetime.now() + timedelta(days=90)).replace(day=1)`
        (see also stock_alerts_gui.py's EXPIRY_WARNING_DAYS = 90).
        Purchase Entry has no per-row stock count of its own (this table
        lists items about to be bought, not existing stock on the
        shelf), so unlike Medicine Master's Status column there is no
        "Low Stock" case here - only the two expiry-based ones apply.
        Returns "OK" (not blank) for an empty/unparseable expiry so a
        half-typed row never shows as falsely Expired."""
        if not expiry_str:
            return "OK"
        try:
            expiry_dt = datetime.strptime(expiry_str.strip(), "%m/%y")
        except (ValueError, AttributeError):
            return "OK"
        today = datetime.today().replace(day=1)
        cutoff = (datetime.now() + timedelta(days=90)).replace(day=1)
        if expiry_dt < today:
            return "Expired"
        if expiry_dt <= cutoff:
            return "Expiring Soon"
        return "OK"

    def _highlight_purchase_status_rows(self, data):
        """Re-derives Expired/Expiring Soon row highlights from the
        Status column (index 10) of `data` and applies them fresh -
        called after every edit to the item table (add/remove/clear).
        tksheet highlights are tied to row POSITIONS, not to the data
        itself, so once a row above is added or removed every later
        row's highlight would otherwise point at the wrong position;
        recomputing from scratch each time (same approach medicine_
        master.py's _render_medicine_rows() already uses, for the exact
        same reason) keeps this correct with no extra bookkeeping."""
        expired_rows = [i for i, row in enumerate(data) if row[10] == "Expired"]
        soon_rows = [i for i, row in enumerate(data) if row[10] == "Expiring Soon"]
        if expired_rows:
            self.purchaseTable.highlight_rows(rows=expired_rows, bg=theme.STATUS_DANGER, fg="white")
        if soon_rows:
            self.purchaseTable.highlight_rows(rows=soon_rows, bg=theme.STATUS_WARNING, fg="black")

    def add_item(self):
        if self.medicine.get().strip() == "":
            ui_popups.show_error(self.frame, "Error", "Select Medicine")
            self.cmbMedicine.focus_set()
            return

        # Computed ONCE here and reused for both the new-medicine INSERT
        # and the grid row below, so a medicine created fresh from this
        # exact Add click and the row added for it always agree on the
        # same (already GST-exclusive) rate - see
        # _get_exclusive_purchase_rate()'s docstring.
        excl_purchase = self._get_exclusive_purchase_rate()

        if self.medicine.get() not in self._medicine_names:
            if not self.offer_create_medicine(excl_purchase):
                self.cmbMedicine.focus_set()
                self.cmbMedicine.select_range(0, tk.END)
                return

        try:
            qty_val = int(float(self.qty.get() or 0))
        except (ValueError, tk.TclError):
            qty_val = 0
        line_total = round(excl_purchase * qty_val, 2)

        current_data = self.purchaseTable.get_sheet_data()
        current_data.append([
            self.medicine.get(),
            self.batch.get(),
            self.expiry.get(),
            self.hsn.get(),
            self.gst.get(),
            excl_purchase,
            self.sale.get(),
            self.pack_size.get(),
            self.qty.get(),
            line_total,
            self._purchase_item_status(self.expiry.get())
        ])
        # reset_col_positions=False keeps our custom column widths.
        # reset_row_positions must stay True - otherwise tksheet's row
        # grid never grows past 0 rows and nothing gets drawn even
        # though the data is there (see stock.py's note on this).
        # reset_highlights=True (was False) - a new row's position can
        # collide with an OLDER row's already-highlighted position isn't
        # actually possible here since rows only ever get appended, but
        # this keeps add_item() consistent with remove_item()/
        # clear_purchase() below, all three always rebuilding highlights
        # fresh from _highlight_purchase_status_rows() rather than half
        # trusting tksheet to carry old ones forward correctly.
        self.purchaseTable.set_sheet_data(current_data, reset_col_positions=False, reset_row_positions=True, reset_highlights=True)
        self._highlight_purchase_status_rows(current_data)

        self.calculate_grand_total()
        self.clear_item_fields()
        self.cmbMedicine["values"] = self._medicine_names
        self.cmbMedicine.focus_set()

    def clear_item_fields(self):
        self.medicine.set("")
        self.batch.set("")
        self.expiry.set("")
        self.hsn.set("")
        self.purchase.set(0)
        self.sale.set(0)
        self.pack_size.set("1")
        self.gst.set(0)
        self.qty.set(1)
        self.total.set(0)

    def offer_create_medicine(self, purchase_override=None):
        """purchase_override: the already-GST-exclusive rate computed by
        add_item() (see _get_exclusive_purchase_rate()) - used instead of
        self.purchase.get() directly so a brand-new medicine created from
        an "Inclusive of Taxes" entry stores the same converted rate the
        grid row for it will show, not the raw inclusive figure. Falls
        back to self.purchase.get() if called without it (keeps this
        method safe to call on its own, e.g. from a test or future
        caller, without silently requiring the override)."""
        name = self.medicine.get().strip()
        purchase_value = purchase_override if purchase_override is not None else self.purchase.get()

        # Brand Master lookup - if this brand name was already provided
        # (see brand_seed_data.py / the Brand Master screen), pre-fill
        # Generic/Company/Category/Dosage Form instead of leaving them
        # blank the way every brand-new entry used to. Shown to the
        # pharmacist in the confirm dialog itself, so they can see and
        # verify exactly what will be filled in before accepting.
        brand_info = brand_mapping.lookup_brand(name)

        if brand_info:
            confirm_msg = (
                f'"{name}" is not in Medicine Master yet.\n\n'
                f"Found in Brand Master:\n"
                f"  Generic: {brand_info['generic_text'] or '-'}\n"
                f"  Company: {brand_info['manufacturer'] or '-'}\n"
                f"  Category: {brand_info['category'] or '-'}\n"
                f"  Dosage Form: {brand_info['dosage_form'] or '-'}\n\n"
                "Add it as a new medicine using the current Batch/Expiry/"
                "Purchase/MRP/GST plus the above details?"
            )
        else:
            confirm_msg = (
                f'"{name}" is not in Medicine Master yet.\n\n'
                "Add it as a new medicine using the current Batch/Expiry/Purchase/MRP/GST?"
            )

        if not ui_popups.show_confirmation(self.frame, "New Medicine", confirm_msg):
            return False

        generic_text = brand_info["generic_text"] if brand_info else None
        company = brand_info["manufacturer"] if brand_info else None
        category = brand_info["category"] if brand_info else None
        dosage_form = brand_info["dosage_form"] if brand_info else None
        composition_id = brand_mapping.resolve_composition_id(generic_text) if generic_text else None

        try:
            repo.insert_new_medicine_from_purchase(
                name, generic_text, company, category, dosage_form, composition_id,
                self.batch.get(), self.expiry.get(), purchase_value,
                self.sale.get(), self.gst.get(),
                # Uses whatever the pharmacist actually typed in the new
                # Pack Size field (defaults to "1" if left untouched -
                # see create_variables()) instead of a hardcoded 1 - a
                # brand-new medicine created mid-purchase now gets its
                # real pack size from day one instead of always needing a
                # later fix in Medicine Master.
                self.pack_size.get().strip() or "1",
            )
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))
            return False

        audit_log.log_action(
            "Purchase", "Create Medicine",
            f"Created '{name}' from Purchase Entry" + (" (Brand Master match)" if brand_info else " (no Brand Master match)")
        )
        self.load_medicines()
        return True

    def remove_item(self):
        current = self.purchaseTable.get_currently_selected()
        if not current or current.row is None:
            ui_popups.show_error(self.frame, "Error", "Select Item")
            return
        self.purchaseTable.del_rows(rows=current.row)
        # Rebuild highlights from whatever rows are left AFTER the
        # delete - every remaining row below the deleted one just moved
        # up a position, so its old highlight (if any) would otherwise
        # be pointing at the wrong row now. See
        # _highlight_purchase_status_rows()'s docstring.
        self._highlight_purchase_status_rows(self.purchaseTable.get_sheet_data())
        self.calculate_grand_total()

    def clear_purchase(self):
        self.purchaseTable.set_sheet_data([], reset_col_positions=False, reset_row_positions=True)
        self.calculate_grand_total()
        self.clear_item_fields()
        self.supplier.set("")
        self.supplier_address.set("")
        self.supplier_phone.set("")
        self.supplier_invoice_no.set("")
        self.supplier_invoice_date.set("")
        self.bill_no.set("")
        # 2026-09-01 fix: without this, a Purchase Date auto-synced (or
        # manually backdated) for ONE invoice silently carried over into
        # the NEXT invoice entered in the same session - e.g. a pharmacist
        # who just entered a late 18-08-2026 purchase, then starts a
        # normal purchase for TODAY right after, would have had it
        # wrongly dated 18-08-2026 too unless they noticed and fixed it
        # themselves. Resetting it here matches every other per-invoice
        # field above.
        self.bill_date.set(self._bill_date_default)
        self.generate_bill_no()

    def _sync_purchase_date_from_supplier_invoice(self, event=None):
        """See the _bill_date_default comment in create_variables() for
        the full bug report this fixes. Only fills Purchase Date in when
        it's still exactly the untouched today-default - a pharmacist who
        already typed their own Purchase Date is never overridden."""
        typed = self.supplier_invoice_date.get().strip()
        if typed and self.bill_date.get().strip() == self._bill_date_default:
            self.bill_date.set(typed)

    def generate_bill_no(self):
        next_id = repo.get_next_purchase_id()

        today = datetime.now().strftime("%Y%m%d")
        bill = f"PUR-{today}-{next_id:04d}"
        self.bill_no.set(bill)