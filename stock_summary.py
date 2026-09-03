"""
stock_summary.py
LifeCare Pharmacy ERP - Inventory > Stock Summary (Opening/Inward/
Outward/Closing).

BharatERP-style stock ledger report (2026-08-22): for a chosen date
range, shows every medicine's Opening stock (as of just before "From"),
Inward (bought/returned-in during the range), Outward (sold/returned-
out during the range), and Closing stock (as of "To"), plus a money
Value estimate for each. Complements the existing read-only Stock
screen (a live snapshot of stock right now, no date range/movement
history) rather than replacing it.

There is no dedicated "opening stock" or stock-ledger table anywhere
in this schema (see database.py) - Opening/Inward/Outward/Closing are
reconstructed here by walking every transaction that has ever changed
a medicine's stock count (Purchase, Billing/Sales, Purchase Return,
Sales Return, Stock Adjustment - see stock_repository.get_all_stock_
movements()'s own docstring for exactly which UPDATE statement each
one corresponds to) and working backwards from TODAY's real stock:

    closing (as of To)   = stock_today - sum(movements dated AFTER To)
    opening (as of From) = closing - sum(movements dated in [From, To])

Value columns use the same `(purchase + purchase*gst/100) / pack_
multiplier` landed-cost estimate every other "Stock Value" figure in
this app already uses (stock.py, medicine_master.py, reports.py's
slow_moving_report()) - see get_all_stock_movements()'s own docstring
for why this is an estimate (current cost on file, not a stored
historical cost) rather than a real accounting valuation.

Deliberately scoped down from BharatERP's own screenshot, per the same
2026-08-22 AskUserQuestion round: no RECONCILE / VALUATION METHOD
buttons (separate subsystems this app has no data model for) and no
"O = Change Opening" shortcut (would mean letting someone override a
computed number with a manual one - a real feature, just a different,
larger one than "show me Opening/Inward/Outward/Closing", left for a
future round if actually needed).
"""

import tkinter as tk
from tkinter import messagebox
import sqlite3
from datetime import datetime, timedelta

from app_paths import DB_NAME as DB
import ui_style
import theme
import stock_repository
from pricing_utils import guess_display_unit
import ui_popups

DATE_FMT = "%d-%m-%Y"  # same convention as Purchase Item Summary's Duration filter


class StockSummary:

    def __init__(self, frame):
        self.frame = frame

        today = datetime.now()
        self.from_date = tk.StringVar(value=today.replace(day=1).strftime(DATE_FMT))
        self.to_date = tk.StringVar(value=today.strftime(DATE_FMT))
        self.search = tk.StringVar()
        self.show_zero_stock = tk.BooleanVar(value=False)

        self.opening_var = tk.StringVar(value="0.00")
        self.inward_var = tk.StringVar(value="0.00")
        self.outward_var = tk.StringVar(value="0.00")
        self.closing_var = tk.StringVar(value="0.00")

        self._all_item_rows = []      # every medicine's ledger row for the current range, unfiltered
        self._display_rows = []       # after search/zero-stock filter - what's on screen
        self._row_names = []          # display row index -> medicine name (for View Detail)

        self.create_ui()
        self.create_footer()
        self.load_summary()
        self._bind_shortcuts()

    # ==========================================
    # USER INTERFACE (UI)
    # ==========================================

    def create_ui(self):
        tk.Label(
            self.frame, text="STOCK SUMMARY",
            bg="#1565C0", fg="white", font=("Segoe UI", 18, "bold"), pady=10
        ).pack(fill="x")

        top = tk.Frame(self.frame)
        top.pack(fill="x", padx=10, pady=10)

        duration = tk.LabelFrame(top, text="Duration [ CTRL+D ]", font=("Segoe UI", 10, "bold"))
        duration.pack(side="left", fill="y")
        tk.Label(duration, text="From").grid(row=0, column=0, padx=5, pady=8)
        from_entry = tk.Entry(duration, textvariable=self.from_date, width=13)
        from_entry.grid(row=0, column=1, padx=(0, 10))
        self._from_entry = from_entry
        tk.Label(duration, text="To").grid(row=0, column=2, padx=5)
        tk.Entry(duration, textvariable=self.to_date, width=13).grid(row=0, column=3, padx=(0, 10))
        tk.Button(duration, text="This Month", width=11, command=self.filter_this_month).grid(row=0, column=4, padx=(0, 5))
        tk.Button(duration, text="Last Month", width=11, command=self.filter_last_month).grid(row=0, column=5, padx=(0, 5))
        tk.Button(duration, text="Refresh", width=10, command=self.load_summary).grid(row=0, column=6, padx=(0, 5))

        cards = tk.Frame(top)
        cards.pack(side="left", fill="y", padx=15)
        self._make_summary_card(cards, "OPENING", self.opening_var, "#1565C0")
        self._make_summary_card(cards, "INWARD", self.inward_var, "#2E7D32")
        self._make_summary_card(cards, "OUTWARD", self.outward_var, "#C62828")
        self._make_summary_card(cards, "CLOSING", self.closing_var, "#00695C")

        search_box = tk.LabelFrame(top, text="Search Item", font=("Segoe UI", 10, "bold"))
        search_box.pack(side="right", fill="y")
        tk.Label(search_box, text="Item Name [F3]").grid(row=0, column=0, padx=5, pady=8)
        search_entry = tk.Entry(search_box, textvariable=self.search, width=20)
        search_entry.grid(row=0, column=1, padx=(0, 10))
        self._search_entry = search_entry
        self.search.trace_add("write", lambda *a: self._render())

        opts = tk.Frame(self.frame)
        opts.pack(fill="x", padx=10, pady=(0, 5))
        tk.Checkbutton(
            opts, text="Show Zero Stock Items", variable=self.show_zero_stock, command=self._render
        ).pack(side="left")
        tk.Button(opts, text="View All Items [F5]", width=16, command=self.view_all).pack(side="left", padx=(15, 0))

        table = tk.Frame(self.frame)
        table.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._cols = (
            "Sl No", "Item Name", "Unit", "Opening", "Opening Value",
            "Inward", "In Value", "Outward", "Out Value", "Closing", "Cl. Value",
        )
        # Widths measured via tksheet's own get_column_text_width() against
        # these exact header strings (2026-08-22, same discipline as the
        # Round 3/7 column-width fixes) - "Opening Value"/"Out Value" are
        # long enough that a guessed width truncated the header itself.
        col_widths = {
            "Sl No": 55, "Item Name": 240, "Unit": 55, "Opening": 75, "Opening Value": 135,
            "Inward": 70, "In Value": 85, "Outward": 85, "Out Value": 100, "Closing": 75, "Cl. Value": 90,
        }
        self._col_widths = col_widths

        # 2026-08-30: switched from make_excel_sheet() (tksheet) to
        # make_plain_sheet() (plain ttk.Treeview) - see medicine_master.py's
        # ui_style.PlainSheet docstring for the full rationale.
        self.table = ui_style.make_plain_sheet(
            table, self._cols, col_widths,
            text_columns=("Item Name",), center_columns=("Sl No", "Unit"),
        )
        self.table.pack(fill="both", expand=True)
        self.table.enable_bindings(*ui_style.READONLY_BINDINGS)
        ui_style.enable_row_highlight_on_select(self.table)

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
            stretch_col = "Item Name"
            stretch_index = self._cols.index(stretch_col)
            fixed = sum(
                col_widths.get(c, 120) + ui_style.CENTER_PAD_WIDTH
                for c in self._cols if c != stretch_col
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
        tk.Label(card, textvariable=var, bg="white", fg=color, font=("Segoe UI", 14, "bold")).pack(padx=14, pady=(8, 0))
        tk.Label(card, text=label, bg="white", fg="#555555", font=("Segoe UI", 9, "bold")).pack(padx=14, pady=(0, 8))

    # ---------------- Footer / keyboard shortcuts / Export / Print ----------------

    def create_footer(self):
        footer = ui_style.make_shortcut_footer(
            self.frame,
            shortcuts=[("ENTER", "View Detail"), ("F3", "Search"), ("CTRL+D", "Duration")],
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

        root.bind("<F3>", _guarded(lambda: self._search_entry.focus_set()), add=True)
        root.bind("<F5>", _guarded(self.view_all), add=True)
        root.bind("<Control-d>", _guarded(lambda: self._from_entry.focus_set()), add=True)
        root.bind("<Control-p>", _guarded(self.print_action), add=True)
        root.bind("<Control-e>", _guarded(self.export_action), add=True)

        self.table.bind("<Return>", self._view_detail_for_selected, add=True)

    def _current_export_rows(self):
        return list(self._cols), [list(r) for r in self._display_rows]

    def export_action(self):
        headers, rows = self._current_export_rows()
        ui_style.export_rows_to_excel(self.frame, headers, rows, default_filename="stock_summary")

    def print_action(self):
        headers, rows = self._current_export_rows()
        ui_style.print_rows_as_report(headers, rows, title="Stock Summary", parent=self.frame)

    # ==========================================
    # DURATION QUICK FILTERS
    # ==========================================

    def view_all(self):
        self.search.set("")
        self.show_zero_stock.set(True)
        self._render()

    def filter_this_month(self):
        today = datetime.now()
        self.from_date.set(today.replace(day=1).strftime(DATE_FMT))
        self.to_date.set(today.strftime(DATE_FMT))
        self.load_summary()

    def filter_last_month(self):
        today = datetime.now()
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        self.from_date.set(last_prev.replace(day=1).strftime(DATE_FMT))
        self.to_date.set(last_prev.strftime(DATE_FMT))
        self.load_summary()

    # ==========================================
    # LOAD / RECONSTRUCT LEDGER
    # ==========================================

    def load_summary(self):
        try:
            from_dt = datetime.strptime(self.from_date.get().strip(), DATE_FMT).date()
            to_dt = datetime.strptime(self.to_date.get().strip(), DATE_FMT).date()
        except ValueError:
            ui_popups.show_error(self.frame, "Invalid Date", "From/To dates must be in DD-MM-YYYY format.")
            return

        current_stock = stock_repository.get_current_stock_by_name()
        movements = stock_repository.get_all_stock_movements()

        # Group every movement by medicine name first (a name's ledger
        # needs ALL its movements, not just the ones inside the chosen
        # range, to walk backwards from today's stock - see this
        # module's own docstring for the opening/closing formula).
        by_name = {}
        for m in movements:
            by_name.setdefault(m["medicine"], []).append(m)

        self._movements_by_name = by_name  # kept for _view_detail_for_selected()
        self._range = (from_dt, to_dt)

        self._all_item_rows = []
        total_opening = total_inward = total_outward = total_closing = 0.0
        pack_size_by_name = self._pack_size_lookup()

        for name in sorted(set(list(by_name.keys()) + list(current_stock.keys())), key=lambda s: s.lower()):
            rows = by_name.get(name, [])
            stock_now = current_stock.get(name, 0)

            after_to_qty = sum(m["qty"] for m in rows if m["date"] and m["date"] > to_dt)
            closing_qty = stock_now - after_to_qty

            in_range = [m for m in rows if m["date"] and from_dt <= m["date"] <= to_dt]
            inward_qty = sum(m["qty"] for m in in_range if m["qty"] > 0)
            outward_qty = -sum(m["qty"] for m in in_range if m["qty"] < 0)
            in_value = sum(m["value"] for m in in_range if m["qty"] > 0)
            out_value = sum(m["value"] for m in in_range if m["qty"] < 0)

            opening_qty = closing_qty - (inward_qty - outward_qty)

            unit_price = self._unit_price_now(name, pack_size_by_name)
            opening_value = round(opening_qty * unit_price, 2)
            closing_value = round(closing_qty * unit_price, 2)

            self._all_item_rows.append((
                name, guess_display_unit(pack_size_by_name.get(name, "1")),
                opening_qty, opening_value, inward_qty, round(in_value, 2),
                outward_qty, round(out_value, 2), closing_qty, closing_value,
            ))
            total_opening += opening_value
            total_inward += in_value
            total_outward += out_value
            total_closing += closing_value

        self.opening_var.set(f"{total_opening:,.2f}")
        self.inward_var.set(f"{total_inward:,.2f}")
        self.outward_var.set(f"{total_outward:,.2f}")
        self.closing_var.set(f"{total_closing:,.2f}")

        self._render()

    def _pack_size_lookup(self):
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute("SELECT name, pack_size FROM medicine_master WHERE pack_size IS NOT NULL AND pack_size != ''")
        result = {}
        for name, pack_size in cur.fetchall():
            if name not in result:
                result[name] = pack_size
        conn.close()
        return result

    def _unit_price_now(self, name, pack_size_by_name):
        """Current per-unit landed cost for `name` (first batch found) -
        used only to turn Opening/Closing QUANTITY into an estimated
        Opening/Closing VALUE, same estimate-not-ledger caveat as
        get_all_stock_movements()'s own docstring."""
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute("SELECT purchase, gst, pack_size FROM medicine_master WHERE name=? LIMIT 1", (name,))
        row = cur.fetchone()
        conn.close()
        if not row or not row[0]:
            return 0.0
        purchase, gst, pack_size = row
        from pricing_utils import get_pack_multiplier
        try:
            pack_mult = get_pack_multiplier(pack_size) or 1
        except Exception:
            pack_mult = 1
        return (purchase + purchase * ((gst or 0.0) / 100.0)) / pack_mult

    def _render(self):
        search_text = self.search.get().strip().lower()
        show_zero = self.show_zero_stock.get()

        source = self._all_item_rows
        if search_text:
            source = [r for r in source if search_text in r[0].lower()]
        if not show_zero:
            source = [r for r in source if r[8] != 0 or r[2] != 0]  # closing_qty or opening_qty non-zero

        self._row_names = [r[0] for r in source]
        self._display_rows = []
        for i, r in enumerate(source, start=1):
            name, unit, opening_qty, opening_value, inward_qty, in_value, outward_qty, out_value, closing_qty, closing_value = r
            self._display_rows.append([
                i, name, unit, opening_qty, opening_value, inward_qty, in_value, outward_qty, out_value, closing_qty, closing_value,
            ])

        data = ui_style.pad_for_full_grid(list(self._display_rows), len(self._cols))
        self.table.set_sheet_data(data, reset_col_positions=False, reset_row_positions=True, reset_highlights=True)

    # ==========================================
    # ENTER = VIEW DETAIL
    # ==========================================

    def _view_detail_for_selected(self, event=None):
        current = self.table.get_currently_selected()
        if not current or current.row is None or current.row >= len(self._row_names):
            return
        name = self._row_names[current.row]
        from_dt, to_dt = self._range
        rows = [
            m for m in self._movements_by_name.get(name, [])
            if m["date"] and from_dt <= m["date"] <= to_dt
        ]
        rows.sort(key=lambda m: m["date"])

        popup = tk.Toplevel(self.frame)
        popup.title(f"Stock Movements - {name}")
        ui_style.center_window(popup, 560, 440, parent=self.frame.winfo_toplevel())
        popup.transient(self.frame.winfo_toplevel())
        # Esc key also closes this popup, same as the Close button.
        popup.bind("<Escape>", lambda event: popup.destroy())
        popup.focus_force()

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring).
        body = ui_style.popup_header(popup, f"{name} - Movements", icon="📦")

        list_frame = tk.Frame(body, bg=theme.SURFACE_WHITE)
        list_frame.pack(fill="both", expand=True, padx=10, pady=10)
        headers = ("Date", "Batch", "Qty (+in / -out)", "Value")
        # 2026-08-30: make_excel_sheet() (tksheet) -> make_plain_sheet()
        # (plain ttk.Treeview) - see medicine_master.py's ui_style.
        # PlainSheet docstring for the full rationale.
        popup_table = ui_style.make_plain_sheet(list_frame, headers, text_columns=("Batch",))
        popup_table.pack(fill="both", expand=True)
        popup_table.enable_bindings(*ui_style.READONLY_BINDINGS)
        display = [
            [m["date"].strftime(DATE_FMT) if m["date"] else "", m["batch"] or "", m["qty"], f"{m['value']:.2f}"]
            for m in rows
        ]
        popup_table.set_sheet_data(
            [list(ui_style.clean_row(r)) for r in display],
            reset_col_positions=False, reset_row_positions=True, reset_highlights=True,
        )

        ui_style.flat_button(body, "Close", theme.PRIMARY, popup.destroy).pack(pady=(0, 10))
