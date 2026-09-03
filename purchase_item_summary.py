"""
purchase_item_summary.py
LifeCare Pharmacy ERP - Purchase > Item Summary.

BharatERP-style "ITEM SUMMARY | PURCHASE" report (2026-08-22): a
date-range summary of everything bought, either grouped by medicine
(default - matches BharatERP's own default view) or grouped by
invoice/date ("VIEW BY DATE" toggle). Reuses the exact same purchase-
entry data purchase.py/purchase_repository.py already writes to the
`purchase` table - no schema changes, no new data entry anywhere.

Deliberately V1-scoped per the user's own choice: Purchase only (no
Sale-side equivalent yet), no "Filter Userwise" (would need a new
created-by column on `purchase`, which doesn't exist yet and would only
be populated going forward - explicitly deferred to a later round).
"""

import tkinter as tk
from tkinter import messagebox
import sqlite3
from datetime import datetime, timedelta
import calendar

from app_paths import DB_NAME as DB
import ui_style
import theme
from pricing_utils import guess_display_unit
import ui_popups

DATE_FMT = "%d-%m-%Y"  # matches purchase.py's own Bill Date field/format


class PurchaseItemSummary:

    def __init__(self, frame):
        self.frame = frame

        today = datetime.now()
        self.from_date = tk.StringVar(value=today.replace(day=1).strftime(DATE_FMT))
        self.to_date = tk.StringVar(value=today.strftime(DATE_FMT))
        self.search = tk.StringVar()

        self.item_total_var = tk.StringVar(value="0.00")
        self.tax_var = tk.StringVar(value="0.00")
        self.grand_total_var = tk.StringVar(value="0.00")
        self.total_item_var = tk.StringVar(value="0")
        self.total_invoice_var = tk.StringVar(value="0")

        # False = item-wise summary (BharatERP's default), True =
        # date/invoice-wise breakdown ("VIEW BY DATE" toggle below).
        self._view_by_date = False

        # Both views are computed together on every load_summary() call
        # (the underlying dataset is small - a pharmacy's own purchase
        # history, not a multi-tenant table) so toggling VIEW BY DATE is
        # instant and never re-hits the database.
        self._item_rows = []      # aggregated-by-medicine rows, full date range, unfiltered by search
        self._invoice_rows = []   # aggregated-by-bill_no rows, full date range, unfiltered by search
        self._current_display_rows = []  # whatever's on screen right now (post search filter) - for export/print

        # display row index -> the real key (medicine name, or bill_no)
        # for that row, so ENTER=View Invoices and export/print know
        # what's actually selected/on screen. Rebuilt on every render.
        self._row_keys = []

        self.create_ui()
        self.create_footer()
        self.load_summary()
        self._bind_shortcuts()

    # ==========================================
    # USER INTERFACE (UI)
    # ==========================================

    def create_ui(self):
        tk.Label(
            self.frame, text="PURCHASE - ITEM SUMMARY",
            bg="#1565C0", fg="white", font=("Segoe UI", 18, "bold"), pady=10
        ).pack(fill="x")

        top = tk.Frame(self.frame)
        top.pack(fill="x", padx=10, pady=10)

        # ---- Duration ----
        duration = tk.LabelFrame(top, text="Duration [ CTRL+D ]", font=("Segoe UI", 10, "bold"))
        duration.pack(side="left", fill="y")

        tk.Label(duration, text="From").grid(row=0, column=0, padx=5, pady=8)
        from_entry = tk.Entry(duration, textvariable=self.from_date, width=13)
        from_entry.grid(row=0, column=1, padx=(0, 10))
        self._from_entry = from_entry

        tk.Label(duration, text="To").grid(row=0, column=2, padx=5)
        tk.Entry(duration, textvariable=self.to_date, width=13).grid(row=0, column=3, padx=(0, 10))

        tk.Button(
            duration, text="View By Date", width=13, command=self.toggle_view_by_date
        ).grid(row=0, column=4, padx=(0, 5))
        self._view_toggle_btn = duration.grid_slaves(row=0, column=4)[0]

        tk.Button(
            duration, text="This Month", width=11, command=self.filter_this_month
        ).grid(row=0, column=5, padx=(0, 5))

        tk.Button(
            duration, text="Last Month", width=11, command=self.filter_last_month
        ).grid(row=0, column=6, padx=(0, 5))

        tk.Button(
            duration, text="Refresh", width=10, command=self.load_summary
        ).grid(row=0, column=7, padx=(0, 5))

        # ---- Summary cards (Item Total / Tax / Total Amount) ----
        cards = tk.Frame(top)
        cards.pack(side="left", fill="y", padx=15)
        self._make_summary_card(cards, "ITEM TOTAL", self.item_total_var, "#1565C0")
        self._make_summary_card(cards, "TAX", self.tax_var, "#8E44AD")
        self._make_summary_card(cards, "TOTAL AMOUNT", self.grand_total_var, "#2E7D32")

        # ---- Search ----
        search_box = tk.LabelFrame(top, text="Search Item", font=("Segoe UI", 10, "bold"))
        search_box.pack(side="right", fill="y")

        tk.Label(search_box, text="Search Item [F3]").grid(row=0, column=0, padx=5, pady=8)
        search_entry = tk.Entry(search_box, textvariable=self.search, width=22)
        search_entry.grid(row=0, column=1, padx=(0, 10))
        self._search_entry = search_entry
        self.search.trace_add("write", lambda *a: self._render_current_view())

        # ---- Counts row (Total Item / Total Invoice) ----
        counts = tk.Frame(self.frame)
        counts.pack(fill="x", padx=10, pady=(0, 5))

        tk.Label(counts, text="Total Item", font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(
            counts, textvariable=self.total_item_var, fg="#1565C0", font=("Segoe UI", 11, "bold")
        ).pack(side="left", padx=(5, 25))

        tk.Label(counts, text="Total Invoice", font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(
            counts, textvariable=self.total_invoice_var, fg="#1565C0", font=("Segoe UI", 11, "bold")
        ).pack(side="left", padx=(5, 0))

        # ---- Table ----
        table = tk.Frame(self.frame)
        table.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._item_cols = ("Item Code", "Medicine", "Invoice", "Qty", "Item Total", "Discount", "Tax", "Total")
        self._invoice_cols = ("Bill No", "Date", "Supplier", "Items", "Qty", "Item Total", "Tax", "Total")

        col_widths = {
            "Item Code": 90, "Medicine": 260, "Invoice": 65, "Qty": 70,
            "Item Total": 100, "Discount": 85, "Tax": 90, "Total": 100,
            # Bill No sized via tksheet's own get_column_text_width()
            # against a real bill_no value ("PUR-20260818-0015", the
            # bill-number format purchase.py actually generates) - 100px
            # truncated it; 155px is the exact measured requirement.
            "Bill No": 155, "Date": 90, "Supplier": 180, "Items": 60,
        }
        self._col_widths = col_widths

        # 2026-08-30: switched from make_excel_sheet() (tksheet) to
        # make_plain_sheet() (plain ttk.Treeview) - see medicine_master.py's
        # ui_style.PlainSheet docstring for the full rationale. The
        # item/date-view header swap below (headers()/align_columns())
        # is answered identically by PlainSheet - see its own docstring.
        self.table = ui_style.make_plain_sheet(
            table, self._item_cols, col_widths,
            text_columns=("Item Code", "Medicine"),
            center_columns=("Invoice", "Qty", "Items"),
        )
        self.table.pack(fill="both", expand=True)
        self.table.enable_bindings(*ui_style.READONLY_BINDINGS)
        ui_style.enable_row_highlight_on_select(self.table)

        # "Medicine" stretch fix - same retarget-to-the-text-column
        # pattern as medicine_master.py/stock.py (2026-08-22): sizes to
        # fixed pixel widths regardless of container width, so the wide
        # text column (not a numeric one) is what absorbs leftover space.
        self._stretch_col_name = "Medicine"
        self._last_stretch_width = None

        def _stretch_last_column(event=None):
            try:
                if not self.table.winfo_exists():
                    return
                self.table.update_idletasks()
                widget_width = self.table.winfo_width()
            except tk.TclError:
                return
            if widget_width <= 1:
                return
            cols = self._item_cols if not self._view_by_date else self._invoice_cols
            stretch_col = "Medicine" if not self._view_by_date else "Supplier"
            if stretch_col not in cols:
                return
            stretch_index = cols.index(stretch_col)
            fixed = sum(
                col_widths.get(c, 120) + ui_style.CENTER_PAD_WIDTH
                for c in cols if c != stretch_col
            )
            new_width = max(
                col_widths.get(stretch_col, 120) + ui_style.CENTER_PAD_WIDTH,
                widget_width - fixed - ui_style._SCROLLBAR_ALLOWANCE
            )
            if new_width == self._last_stretch_width:
                return
            self._last_stretch_width = new_width
            try:
                self.table.column_width(column=stretch_index, width=new_width)
            except tk.TclError:
                pass

        self._stretch_last_column = _stretch_last_column
        self.table.after(200, _stretch_last_column)
        self.frame.winfo_toplevel().bind("<Configure>", _stretch_last_column, add=True)

    def _make_summary_card(self, parent, label, var, color):
        card = tk.Frame(parent, bg="white", highlightbackground="#CCCCCC", highlightthickness=1)
        card.pack(side="left", padx=6, fill="y")
        tk.Label(
            card, textvariable=var, bg="white", fg=color, font=("Segoe UI", 15, "bold")
        ).pack(padx=16, pady=(8, 0))
        tk.Label(
            card, text=label, bg="white", fg="#555555", font=("Segoe UI", 9, "bold")
        ).pack(padx=16, pady=(0, 8))

    # ---------------- Footer / keyboard shortcuts / Export / Print ----------------

    def create_footer(self):
        footer = ui_style.make_shortcut_footer(
            self.frame,
            shortcuts=[
                ("ENTER", "View Invoices"),
                ("F3", "Search"),
                ("CTRL+D", "Duration"),
            ],
            on_print=self.print_action,
            on_export=self.export_action,
        )
        footer.pack(side="bottom", fill="x")

    def _bind_shortcuts(self):
        root = self.frame.winfo_toplevel()

        def _guarded(fn):
            def handler(event=None):
                try:
                    if not self.frame.winfo_exists():
                        return
                except tk.TclError:
                    return
                fn()
            return handler

        # CTRL+S/ESC-style broad bindings are safe on the whole toplevel
        # (none of these type/delete a character) - same reasoning as
        # medicine_master.py/brand_master_gui.py's _bind_shortcuts().
        root.bind("<F3>", _guarded(lambda: self._search_entry.focus_set()), add=True)
        root.bind("<Control-d>", _guarded(lambda: self._from_entry.focus_set()), add=True)
        root.bind("<Control-p>", _guarded(self.print_action), add=True)
        root.bind("<Control-e>", _guarded(self.export_action), add=True)

        # ENTER=View Invoices is scoped to the grid only, same reasoning
        # as DEL/ENTER on the other screens - this screen has no text
        # entry where Enter would otherwise submit something, but
        # keeping it grid-scoped costs nothing and matches the pattern.
        self.table.bind("<Return>", self._view_invoices_for_selected, add=True)

    def _current_export_rows(self):
        cols = self._invoice_cols if self._view_by_date else self._item_cols
        return list(cols), list(self._current_display_rows)

    def export_action(self):
        headers, rows = self._current_export_rows()
        ui_style.export_rows_to_excel(self.frame, headers, rows, default_filename="purchase_item_summary")

    def print_action(self):
        headers, rows = self._current_export_rows()
        title = "Purchase - Item Summary (By Date)" if self._view_by_date else "Purchase - Item Summary"
        ui_style.print_rows_as_report(headers, rows, title=title, parent=self.frame)

    # ==========================================
    # DURATION QUICK FILTERS
    # ==========================================

    def toggle_view_by_date(self):
        self._view_by_date = not self._view_by_date
        self._view_toggle_btn.config(text="View By Item" if self._view_by_date else "View By Date")
        self._last_stretch_width = None  # force the stretch fix to re-measure for the new column set
        self._render_current_view()
        self._stretch_last_column()

    def filter_this_month(self):
        today = datetime.now()
        self.from_date.set(today.replace(day=1).strftime(DATE_FMT))
        self.to_date.set(today.strftime(DATE_FMT))
        self.load_summary()

    def filter_last_month(self):
        today = datetime.now()
        first_of_this_month = today.replace(day=1)
        last_of_prev_month = first_of_this_month - timedelta(days=1)
        first_of_prev_month = last_of_prev_month.replace(day=1)
        self.from_date.set(first_of_prev_month.strftime(DATE_FMT))
        self.to_date.set(last_of_prev_month.strftime(DATE_FMT))
        self.load_summary()

    # ==========================================
    # LOAD / AGGREGATE
    # ==========================================

    def load_summary(self):
        try:
            from_dt = datetime.strptime(self.from_date.get().strip(), DATE_FMT).date()
            to_dt = datetime.strptime(self.to_date.get().strip(), DATE_FMT).date()
        except ValueError:
            ui_popups.show_error(self.frame, 
                "Invalid Date",
                f"From/To dates must be in {DATE_FMT.replace('%d', 'DD').replace('%m', 'MM').replace('%Y', 'YYYY')} format."
            )
            return

        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        # purchase.bill_date is stored as DD-MM-YYYY text (matches
        # purchase.py's own Bill Date field) - that format does NOT sort
        # or compare correctly as a plain SQL string BETWEEN, so every
        # row is fetched and filtered here in Python instead (this
        # pharmacy's own purchase history is a small table, not a
        # multi-tenant one - a full scan is cheap).
        cur.execute("SELECT bill_no, bill_date, supplier, medicine, gst, qty, total FROM purchase")
        raw_rows = cur.fetchall()

        cur.execute("SELECT name, barcode FROM medicine_master")
        barcode_map = {}
        for name, barcode in cur.fetchall():
            if barcode and name not in barcode_map:
                barcode_map[name] = barcode
        conn.close()

        item_agg = {}     # medicine -> {invoices:set, qty, item_total, tax}
        invoice_agg = {}  # bill_no -> {date, supplier, medicines:set, qty, item_total, tax}

        for bill_no, bill_date, supplier, medicine, gst, qty, total in raw_rows:
            try:
                row_dt = datetime.strptime((bill_date or "").strip(), DATE_FMT).date()
            except ValueError:
                continue
            if not (from_dt <= row_dt <= to_dt):
                continue

            qty = qty or 0
            total = total or 0.0
            gst = gst or 0.0
            tax = round(total * gst / 100, 2)

            item = item_agg.setdefault(medicine, {"invoices": set(), "qty": 0, "item_total": 0.0, "tax": 0.0})
            item["invoices"].add(bill_no)
            item["qty"] += qty
            item["item_total"] += total
            item["tax"] += tax

            inv = invoice_agg.setdefault(
                bill_no, {"date": bill_date, "supplier": supplier, "medicines": set(), "qty": 0, "item_total": 0.0, "tax": 0.0}
            )
            inv["medicines"].add(medicine)
            inv["qty"] += qty
            inv["item_total"] += total
            inv["tax"] += tax

        self._item_rows = []
        for medicine in sorted(item_agg.keys(), key=lambda s: s.lower()):
            v = item_agg[medicine]
            total_amt = round(v["item_total"] + v["tax"], 2)
            self._item_rows.append((
                barcode_map.get(medicine, ""), medicine, len(v["invoices"]), v["qty"],
                round(v["item_total"], 2), 0.00, round(v["tax"], 2), total_amt,
            ))

        self._invoice_rows = []
        for bill_no in sorted(invoice_agg.keys(), key=lambda b: invoice_agg[b]["date"] or "", reverse=True):
            v = invoice_agg[bill_no]
            total_amt = round(v["item_total"] + v["tax"], 2)
            self._invoice_rows.append((
                bill_no, v["date"], v["supplier"], len(v["medicines"]), v["qty"],
                round(v["item_total"], 2), round(v["tax"], 2), total_amt,
            ))

        grand_item_total = sum(r["item_total"] for r in item_agg.values())
        grand_tax = sum(r["tax"] for r in item_agg.values())
        self.item_total_var.set(f"{grand_item_total:,.2f}")
        self.tax_var.set(f"{grand_tax:,.2f}")
        self.grand_total_var.set(f"{grand_item_total + grand_tax:,.2f}")
        self.total_item_var.set(str(len(item_agg)))
        self.total_invoice_var.set(str(len(invoice_agg)))

        self._render_current_view()

    def _render_current_view(self):
        search_text = self.search.get().strip().lower()

        if self._view_by_date:
            cols = self._invoice_cols
            source = self._invoice_rows
            if search_text:
                source = [r for r in source if search_text in str(r[0]).lower() or search_text in str(r[2]).lower()]
            self._row_keys = [r[0] for r in source]  # bill_no
        else:
            cols = self._item_cols
            source = self._item_rows
            if search_text:
                source = [r for r in source if search_text in str(r[1]).lower()]
            self._row_keys = [r[1] for r in source]  # medicine name

        # clean_row() so a genuinely NULL supplier/medicine/bill_no (seen
        # in real pharmacy.db data - some old purchase rows have a blank
        # supplier) renders as blank, not the literal text "None" -
        # same fix already applied everywhere else in this app.
        self._current_display_rows = [list(ui_style.clean_row(r)) for r in source]
        data = ui_style.pad_for_full_grid(list(self._current_display_rows), len(cols))

        # Rebuild the sheet's headers/columns for whichever view is
        # active - column count/meaning genuinely differs between
        # item-wise and date-wise, unlike every other screen in this app
        # which never changes its own column set at runtime.
        self.table.headers(list(cols))
        for i, col in enumerate(cols):
            width = self._col_widths.get(col, 120) + ui_style.CENTER_PAD_WIDTH
            self.table.column_width(column=i, width=width)
            align = "w" if col in ("Item Code", "Medicine", "Bill No", "Date", "Supplier") else (
                "center" if col in ("Invoice", "Qty", "Items") else "e"
            )
            self.table.align_columns(columns=[i], align=align, align_header=True)

        self.table.set_sheet_data(data, reset_col_positions=False, reset_row_positions=True, reset_highlights=True)

    # ==========================================
    # ENTER = VIEW INVOICES
    # ==========================================

    def _view_invoices_for_selected(self, event=None):
        current = self.table.get_currently_selected()
        if not current or current.row is None or current.row >= len(self._row_keys):
            return
        key = self._row_keys[current.row]

        try:
            from_dt = datetime.strptime(self.from_date.get().strip(), DATE_FMT).date()
            to_dt = datetime.strptime(self.to_date.get().strip(), DATE_FMT).date()
        except ValueError:
            return

        if self._view_by_date:
            conn = sqlite3.connect(DB)
            cur = conn.cursor()
            cur.execute(
                "SELECT medicine, batch, qty, purchase, gst, total FROM purchase WHERE bill_no=?",
                (key,),
            )
            rows = cur.fetchall()
            conn.close()
            title = f"Invoice {key} - Items"
            col_headers = ("Medicine", "Batch", "Qty", "Rate", "GST%", "Total")

            popup = tk.Toplevel(self.frame)
            popup.title(title)
            ui_style.center_window(popup, 520, 400, parent=self.frame.winfo_toplevel())
            popup.transient(self.frame.winfo_toplevel())
            # Esc key also closes this popup, same as the Close button.
            popup.bind("<Escape>", lambda event: popup.destroy())
            popup.focus_force()

            # Aug 2026 visual refresh: same colored-header / white-body /
            # flat-button look as every other hand-built popup app-wide
            # (see ui_style.popup_header()'s docstring).
            body = ui_style.popup_header(popup, title, icon="📄")

            list_frame = tk.Frame(body, bg=theme.SURFACE_WHITE)
            list_frame.pack(fill="both", expand=True, padx=10, pady=10)
            # 2026-08-30: make_excel_sheet() (tksheet) -> make_plain_sheet()
            # (plain ttk.Treeview) - see medicine_master.py's ui_style.
            # PlainSheet docstring for the full rationale.
            popup_table = ui_style.make_plain_sheet(list_frame, col_headers, text_columns=col_headers)
            popup_table.pack(fill="both", expand=True)
            popup_table.enable_bindings(*ui_style.READONLY_BINDINGS)
            popup_table.set_sheet_data(
                [list(ui_style.clean_row(r)) for r in rows],
                reset_col_positions=False, reset_row_positions=True, reset_highlights=True,
            )

            ui_style.flat_button(body, "Close", theme.PRIMARY, popup.destroy).pack(pady=(0, 10))
        else:
            # Item-wise view: BharatERP's own "PURCHASE DETAILS | <item>"
            # screen (2026-08-22 user screenshot) is a full report in its
            # own right (Duration/summary cards/This Month/Last Month/
            # Closing Stock), not just a raw list - see
            # _open_purchase_details_screen()'s own docstring.
            self._open_purchase_details_screen(key)

    def _open_purchase_details_screen(self, medicine):
        """
        BharatERP-style "PURCHASE DETAILS | <item>" drill-down
        (2026-08-22): everything this pharmacy has ever bought of ONE
        medicine, with its own Duration filter, Invoice/Quantity/Item
        Total/Discount/Tax/Total Amount summary cards, current Closing
        Stock, This Month/Last Month quick filters, and a Date/Inv No/
        Ledger A/c/Quantity/Rate/Disc/Tax/Total Amount table - replacing
        the earlier plain "list of purchase rows" popup this ENTER
        action used to open.

        A Toplevel (not a full dashboard-body screen) on purpose, same
        reasoning as every other drill-down in this app (Quick Edit,
        Selected Info, the date-wise Invoice-Items popup right above) -
        BharatERP itself is a single-window MDI app where this slides
        in as an overlay with its own [x]; here that translates most
        directly to a sizeable popup window with its own Close button,
        not a change to how dashboard.py navigates between screens.
        """
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute(
            "SELECT bill_no, bill_date, supplier, batch, pack_size, qty, purchase, gst, total FROM purchase WHERE medicine=?",
            (medicine,),
        )
        all_rows = cur.fetchall()
        cur.execute("SELECT COALESCE(SUM(stock), 0), MAX(pack_size) FROM medicine_master WHERE name=?", (medicine,))
        closing_stock, any_pack_size = cur.fetchone()
        conn.close()
        unit_label = guess_display_unit(any_pack_size)

        popup = tk.Toplevel(self.frame)
        popup.title(f"Purchase Details - {medicine}")
        ui_style.center_window(popup, 980, 680, parent=self.frame.winfo_toplevel())
        popup.minsize(820, 560)
        popup.transient(self.frame.winfo_toplevel())

        # Aug 2026 visual refresh: same colored-header / white-body look
        # as every other hand-built popup app-wide (see
        # ui_style.popup_header()'s docstring).
        body = ui_style.popup_header(popup, f"PURCHASE DETAILS | {medicine}", icon="📊")

        pd_from = tk.StringVar(value=self.from_date.get())
        pd_to = tk.StringVar(value=self.to_date.get())
        card_vars = {
            "invoice": tk.StringVar(value="0"), "qty": tk.StringVar(value="0"),
            "item_total": tk.StringVar(value="0.00"), "discount": tk.StringVar(value="0.00"),
            "tax": tk.StringVar(value="0.00"), "grand_total": tk.StringVar(value="0.00"),
        }
        closing_var = tk.StringVar(value=f"CLOSING STOCK : {closing_stock} {unit_label}")

        # Duration and the 6 summary cards each get their OWN full-width
        # row (stacked, not side-by-side in one row) - a single row wide
        # enough for Duration + all 6 cards needed ~1350px, more than
        # this popup is meant to take on a normal laptop screen; Tk's
        # own automatic geometry management also does not reliably
        # honour a fixed popup.geometry() once packed children need
        # more width than requested (confirmed empirically - a narrower
        # single-row layout silently clipped the last 1-2 cards off the
        # visible window instead of wrapping them). Stacking avoids
        # needing that much width at all.
        duration_row = tk.Frame(body, bg=theme.SURFACE_WHITE)
        duration_row.pack(fill="x", padx=10, pady=(10, 4))

        duration = tk.LabelFrame(
            duration_row, text="Duration [ CTRL+D ]", font=("Segoe UI", 10, "bold"),
            bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
        )
        duration.pack(side="left")
        tk.Label(duration, text="From", bg=theme.SURFACE_WHITE).grid(row=0, column=0, padx=5, pady=8)
        pd_from_entry = tk.Entry(duration, textvariable=pd_from, width=12)
        pd_from_entry.grid(row=0, column=1, padx=(0, 10))
        tk.Label(duration, text="To", bg=theme.SURFACE_WHITE).grid(row=0, column=2, padx=5)
        tk.Entry(duration, textvariable=pd_to, width=12).grid(row=0, column=3, padx=(0, 10))

        cards_row = tk.Frame(body, bg=theme.SURFACE_WHITE)
        cards_row.pack(fill="x", padx=10, pady=(0, 6))
        cards = tk.Frame(cards_row, bg=theme.SURFACE_WHITE)
        cards.pack(side="left")
        for key, label, color in (
            ("invoice", "INVOICE", "#1565C0"), ("qty", "QUANTITY", "#00695C"),
            ("item_total", "ITEM TOTAL", "#1565C0"), ("discount", "DISCOUNT", "#8D6E63"),
            ("tax", "TAX", "#8E44AD"), ("grand_total", "TOTAL AMOUNT", "#2E7D32"),
        ):
            self._make_summary_card(cards, label, card_vars[key], color)

        table_frame = tk.Frame(body, bg=theme.SURFACE_WHITE)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        # 2026-09-03: added "Trend" - the pharmacist's actual question
        # ("did this medicine's purchase rate go up or down?") had no
        # answer here beyond eyeballing a column of numbers and doing
        # the subtraction themselves, invoice by invoice. Each row now
        # shows the change vs. the immediately PRECEDING chronological
        # purchase of this same medicine (any supplier/batch - the
        # pharmacist cares about "what am I paying now vs last time",
        # not a per-batch/per-supplier breakdown), with the whole row
        # tinted red (rate up) or green (rate down) - see reload_details()
        # below for the actual comparison and highlight_rows() calls.
        pd_cols = ("Date", "Inv No", "Ledger A/c", "Quantity", "Rate", "Trend", "Disc", "Tax", "Total Amount")
        pd_col_widths = {"Date": 90, "Inv No": 150, "Ledger A/c": 200, "Quantity": 90,
                          "Rate": 90, "Trend": 140, "Disc": 80, "Tax": 90, "Total Amount": 126}
        # 2026-08-30: make_excel_sheet() (tksheet) -> make_plain_sheet()
        # (plain ttk.Treeview) - see medicine_master.py's ui_style.
        # PlainSheet docstring for the full rationale.
        pd_table = ui_style.make_plain_sheet(
            table_frame, pd_cols, pd_col_widths,
            text_columns=("Inv No", "Ledger A/c", "Trend"), center_columns=("Date", "Quantity"),
        )
        pd_table.pack(fill="both", expand=True)
        pd_table.enable_bindings(*ui_style.READONLY_BINDINGS)
        ui_style.enable_row_highlight_on_select(pd_table)

        display_rows = []
        row_bill_nos = []

        def reload_details():
            try:
                from_dt = datetime.strptime(pd_from.get().strip(), DATE_FMT).date()
                to_dt = datetime.strptime(pd_to.get().strip(), DATE_FMT).date()
            except ValueError:
                ui_popups.show_error(popup, "Invalid Date", "From/To dates must be in DD-MM-YYYY format.")
                return

            filtered = []
            for bill_no, bill_date, supplier, batch, pack_size, qty, purchase, gst, total in all_rows:
                try:
                    row_dt = datetime.strptime((bill_date or "").strip(), DATE_FMT).date()
                except ValueError:
                    continue
                if from_dt <= row_dt <= to_dt:
                    filtered.append((row_dt, bill_no, bill_date, supplier, qty or 0, purchase or 0.0,
                                      gst or 0.0, total or 0.0))

            # Oldest first - the Trend column below only means anything
            # when rows are in actual chronological order, not whatever
            # order SQLite happened to return them in (rowid order,
            # which a backdated/late entry can easily break).
            filtered.sort(key=lambda r: r[0])

            display_rows.clear()
            row_bill_nos.clear()
            up_rows, down_rows = [], []
            total_qty = total_item = total_tax = 0.0
            invoices = set()
            prev_rate = None
            for row_dt, bill_no, bill_date, supplier, qty, rate, gst, total in filtered:
                tax_amt = round(total * gst / 100, 2)
                total_amt = round(total + tax_amt, 2)

                if prev_rate is None:
                    trend = "First purchase"
                else:
                    diff = round(rate - prev_rate, 2)
                    if diff > 0:
                        trend = f"▲ +₹{diff:.2f}"
                        up_rows.append(len(display_rows))
                    elif diff < 0:
                        trend = f"▼ -₹{abs(diff):.2f}"
                        down_rows.append(len(display_rows))
                    else:
                        trend = "No change"
                prev_rate = rate

                display_rows.append([
                    bill_date, bill_no, supplier or "", f"{qty:.1f} {unit_label}",
                    rate, trend, 0.00, tax_amt, total_amt,
                ])
                row_bill_nos.append(bill_no)
                invoices.add(bill_no)
                total_qty += qty
                total_item += total
                total_tax += tax_amt

            card_vars["invoice"].set(str(len(invoices)))
            card_vars["qty"].set(f"{total_qty:.0f} {unit_label}")
            card_vars["item_total"].set(f"{total_item:,.2f}")
            card_vars["discount"].set("0.00")
            card_vars["tax"].set(f"{total_tax:,.2f}")
            card_vars["grand_total"].set(f"{total_item + total_tax:,.2f}")

            data = ui_style.pad_for_full_grid([list(ui_style.clean_row(r)) for r in display_rows], len(pd_cols))
            pd_table.set_sheet_data(data, reset_col_positions=False, reset_row_positions=True, reset_highlights=True)
            # Same red/green convention as Supplier Ledger's Overdue/Paid
            # rows (see supplier_ledger.py's render_invoice_status) - a
            # rate INCREASE (costs the pharmacy more) is tinted red, a
            # DECREASE is tinted green.
            if up_rows:
                pd_table.highlight_rows(rows=up_rows, bg="#FFCDD2", fg="#B71C1C")
            if down_rows:
                pd_table.highlight_rows(rows=down_rows, bg="#C8E6C9", fg="#1B5E20")

        def this_month():
            today = datetime.now()
            pd_from.set(today.replace(day=1).strftime(DATE_FMT))
            pd_to.set(today.strftime(DATE_FMT))
            reload_details()

        def last_month():
            today = datetime.now()
            first_this = today.replace(day=1)
            last_prev = first_this - timedelta(days=1)
            pd_from.set(last_prev.replace(day=1).strftime(DATE_FMT))
            pd_to.set(last_prev.strftime(DATE_FMT))
            reload_details()

        tk.Button(duration, text="This Month", width=11, command=this_month).grid(row=0, column=4, padx=(0, 5))
        tk.Button(duration, text="Last Month", width=11, command=last_month).grid(row=0, column=5, padx=(0, 5))
        tk.Button(duration, text="Refresh", width=10, command=reload_details).grid(row=0, column=6, padx=(0, 5))

        tk.Label(
            body, textvariable=closing_var, bg=theme.SURFACE_WHITE, fg="#D84315", font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=15, pady=(0, 6))

        def view_invoice(event=None):
            current = pd_table.get_currently_selected()
            if not current or current.row is None or current.row >= len(row_bill_nos):
                return
            bill_no = row_bill_nos[current.row]
            conn2 = sqlite3.connect(DB)
            cur2 = conn2.cursor()
            cur2.execute(
                "SELECT medicine, batch, qty, purchase, gst, total FROM purchase WHERE bill_no=?", (bill_no,)
            )
            items = cur2.fetchall()
            conn2.close()
            inv_popup = tk.Toplevel(popup)
            inv_popup.title(f"Invoice {bill_no}")
            ui_style.center_window(inv_popup, 520, 380, parent=popup)
            inv_popup.transient(popup)
            # Esc key also closes this popup, same as the Close button.
            inv_popup.bind("<Escape>", lambda event: inv_popup.destroy())
            inv_popup.focus_force()
            # Aug 2026 visual refresh: same colored-header / white-body /
            # flat-button look as every other hand-built popup app-wide
            # (see ui_style.popup_header()'s docstring).
            inv_body = ui_style.popup_header(inv_popup, f"Invoice {bill_no}", icon="📄")
            inv_frame = tk.Frame(inv_body, bg=theme.SURFACE_WHITE)
            inv_frame.pack(fill="both", expand=True, padx=10, pady=10)
            inv_headers = ("Medicine", "Batch", "Qty", "Rate", "GST%", "Total")
            # 2026-08-30: make_excel_sheet() (tksheet) -> make_plain_sheet()
            # (plain ttk.Treeview) - see medicine_master.py's ui_style.
            # PlainSheet docstring for the full rationale.
            inv_table = ui_style.make_plain_sheet(inv_frame, inv_headers, text_columns=inv_headers)
            inv_table.pack(fill="both", expand=True)
            inv_table.enable_bindings(*ui_style.READONLY_BINDINGS)
            inv_table.set_sheet_data(
                [list(ui_style.clean_row(r)) for r in items],
                reset_col_positions=False, reset_row_positions=True, reset_highlights=True,
            )
            ui_style.flat_button(inv_body, "Close", theme.PRIMARY, inv_popup.destroy).pack(pady=(0, 10))

        pd_table.bind("<Return>", view_invoice, add=True)

        def pd_export():
            ui_style.export_rows_to_excel(popup, list(pd_cols), display_rows, default_filename="purchase_details")

        def pd_print():
            ui_style.print_rows_as_report(list(pd_cols), display_rows, title=f"Purchase Details - {medicine}", parent=popup)

        footer = ui_style.make_shortcut_footer(
            body, shortcuts=[("ENTER", "View Invoice")], on_print=pd_print, on_export=pd_export,
        )
        footer.pack(side="bottom", fill="x")

        popup.bind("<Control-p>", lambda e: pd_print(), add=True)
        popup.bind("<Control-e>", lambda e: pd_export(), add=True)
        popup.bind("<Control-d>", lambda e: pd_from_entry.focus_set(), add=True)
        # Esc key also closes this popup, same as its window's own X
        # button (no dedicated Close button on this full-screen popup).
        popup.bind("<Escape>", lambda e: popup.destroy(), add=True)
        popup.focus_force()

        reload_details()
