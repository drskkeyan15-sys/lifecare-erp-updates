"""
price_list.py
LifeCare Pharmacy ERP - Inventory > Price List | All Items.

BharatERP-style master price list (2026-08-22): every medicine_master
row (one per batch - see database.py's own comment on why `name` isn't
UNIQUE) in one searchable table, with a quick price-only edit (ENTER)
and a fuller batch+price edit popup (CTRL+ENTER), matching the two-tier
edit BharatERP's own "PRICE LIST | ALL ITEMS" screenshot showed
("ENTER = CHANGE PRICE" vs "CTRL+ENTER = EDIT" -> "UPDATE BATCH AND
PRICE" popup).

Deliberately scoped down from BharatERP's own screenshot, per the
user's own AskUserQuestion choices (2026-08-22):
- No "Show Prices in Alternet Unit" checkbox - this app has no real
  Alt-Unit field at all (`pack_size` is free text, not a stored
  Unit-of-Measure column - see pricing_utils.guess_display_unit()'s own
  docstring on why "Unit" here is a cosmetic guess, not authoritative).
- No FILTER / PRICE UPDATE / ITEM DISCOUNTS / SCHEMES buttons - each is
  its own separate subsystem BharatERP has, not part of this screen's
  own ask, and none of it exists in this app's schema today.
- "ADD ITEM" is a real button here, but deliberately does NOT duplicate
  Medicine Master's own "add a new medicine" form (which already does
  this job) - it navigates to the existing Medicine Master screen
  instead, via the optional on_open_medicine_master callback
  dashboard.py wires in (same on_close-style optional-callback
  convention open_module() already uses elsewhere).
"""

import tkinter as tk
from tkinter import messagebox
import sqlite3
from datetime import datetime

from app_paths import DB_NAME as DB
import ui_style
import theme
import audit_log
from pricing_utils import guess_display_unit
import ui_popups

EXP_FMT = "%m/%y"  # same MM/YY convention as every other expiry field in this app


class PriceList:

    def __init__(self, frame, on_open_medicine_master=None):
        self.frame = frame
        self.on_open_medicine_master = on_open_medicine_master

        self.search = tk.StringVar()
        self.remove_zero_stock = tk.BooleanVar(value=False)
        self.total_items_var = tk.StringVar(value="0")

        self._all_rows = []      # every medicine_master row, unfiltered, as dicts
        self._display_rows = []  # after search/zero-stock filter - what's on screen
        self._row_ids = []       # display row index -> medicine_master.id

        self.create_ui()
        self.create_footer()
        self.load_data()
        self._bind_shortcuts()

    # ==========================================
    # USER INTERFACE (UI)
    # ==========================================

    def create_ui(self):
        tk.Label(
            self.frame, text="PRICE LIST | ALL ITEMS",
            bg="#1565C0", fg="white", font=("Segoe UI", 18, "bold"), pady=10
        ).pack(fill="x")

        top = tk.Frame(self.frame)
        top.pack(fill="x", padx=10, pady=10)

        search_box = tk.LabelFrame(top, text="Search Item", font=("Segoe UI", 10, "bold"))
        search_box.pack(side="left", fill="y")
        tk.Label(search_box, text="Search Item [F3]").grid(row=0, column=0, padx=5, pady=8)
        search_entry = tk.Entry(search_box, textvariable=self.search, width=28)
        search_entry.grid(row=0, column=1, padx=(0, 10))
        self._search_entry = search_entry
        self.search.trace_add("write", lambda *a: self._render())

        tk.Checkbutton(
            top, text="Remove Zero Stock Items", variable=self.remove_zero_stock, command=self._render
        ).pack(side="left", padx=15)

        tk.Button(top, text="View All [F5]", width=12, command=self.view_all).pack(side="left", padx=(0, 8))
        tk.Button(
            top, text="Add Item", width=12, bg="#2E7D32", fg="white", command=self._add_item
        ).pack(side="left")

        total_frame = tk.Frame(top, bg="white", highlightbackground="#CCCCCC", highlightthickness=1)
        total_frame.pack(side="right", fill="y")
        tk.Label(
            total_frame, textvariable=self.total_items_var, bg="white",
            fg="#1565C0", font=("Segoe UI", 15, "bold")
        ).pack(padx=16, pady=(8, 0))
        tk.Label(
            total_frame, text="TOTAL ITEMS", bg="white", fg="#555555", font=("Segoe UI", 9, "bold")
        ).pack(padx=16, pady=(0, 8))

        table = tk.Frame(self.frame)
        table.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._cols = (
            "Item Code", "Item Name", "Batch No", "Unit",
            "MRP", "Tax %", "Purchase Price", "Sale Price", "Stock",
        )
        col_widths = {
            "Item Code": 100, "Item Name": 260, "Batch No": 110, "Unit": 60,
            "MRP": 90, "Tax %": 70, "Purchase Price": 132, "Sale Price": 100, "Stock": 80,
        }
        self._col_widths = col_widths

        # 2026-08-30: switched from make_excel_sheet() (tksheet) to
        # make_plain_sheet() (plain ttk.Treeview) - see medicine_master.py's
        # ui_style.PlainSheet docstring for the full rationale. Every
        # other call below (set_sheet_data/get_currently_selected/
        # column_width/etc) is unchanged, PlainSheet answers to the
        # same method names.
        self.table = ui_style.make_plain_sheet(
            table, self._cols, col_widths,
            text_columns=("Item Code", "Item Name", "Batch No"),
            center_columns=("Unit", "Stock"),
        )
        self.table.pack(fill="both", expand=True)
        self.table.enable_bindings(*ui_style.READONLY_BINDINGS)
        ui_style.enable_row_highlight_on_select(self.table)

        # "Item Name" stretch fix - same retarget-to-the-text-column
        # pattern as medicine_master.py/stock.py/purchase_item_summary.py.
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

    # ---------------- Footer / keyboard shortcuts / Export / Print ----------------

    def create_footer(self):
        footer = ui_style.make_shortcut_footer(
            self.frame,
            shortcuts=[
                ("ENTER", "Change Price"),
                ("CTRL+ENTER", "Edit"),
                ("F5", "Load All"),
                ("F3", "Search"),
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

        root.bind("<F3>", _guarded(lambda: self._search_entry.focus_set()), add=True)
        root.bind("<F5>", _guarded(self.view_all), add=True)
        root.bind("<Control-p>", _guarded(self.print_action), add=True)
        root.bind("<Control-e>", _guarded(self.export_action), add=True)

        # ENTER/CTRL+ENTER are grid-scoped only - same DEL/ENTER-on-tree-
        # only safety reasoning as every other Master screen this
        # session (a toplevel-wide binding would fire while typing in
        # the Search box too).
        self.table.bind("<Return>", self._quick_price_edit, add=True)
        self.table.bind("<Control-Return>", self._full_batch_price_edit, add=True)

    def _current_export_rows(self):
        return list(self._cols), [list(r) for r in self._display_rows]

    def export_action(self):
        headers, rows = self._current_export_rows()
        ui_style.export_rows_to_excel(self.frame, headers, rows, default_filename="price_list")

    def print_action(self):
        headers, rows = self._current_export_rows()
        ui_style.print_rows_as_report(headers, rows, title="Price List - All Items", parent=self.frame)

    # ==========================================
    # LOAD / FILTER
    # ==========================================

    def load_data(self):
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, barcode, batch, pack_size, expiry, mrp, gst, purchase, sale, stock "
            "FROM medicine_master ORDER BY name"
        )
        self._all_rows = cur.fetchall()
        conn.close()
        self._render()

    def view_all(self):
        self.search.set("")
        self.remove_zero_stock.set(False)
        self.load_data()

    def _render(self):
        search_text = self.search.get().strip().lower()
        only_stocked = self.remove_zero_stock.get()

        self._display_rows = []
        self._row_ids = []
        for med_id, name, barcode, batch, pack_size, expiry, mrp, gst, purchase, sale, stock in self._all_rows:
            stock = stock or 0
            if only_stocked and stock <= 0:
                continue
            if search_text and search_text not in (name or "").lower():
                continue
            self._row_ids.append(med_id)
            self._display_rows.append(list(ui_style.clean_row((
                barcode or "", name or "", batch or "", guess_display_unit(pack_size),
                mrp or 0.0, gst or 0.0, purchase or 0.0, sale or 0.0, stock,
            ))))

        self.total_items_var.set(str(len(self._display_rows)))

        data = ui_style.pad_for_full_grid(list(self._display_rows), len(self._cols))
        self.table.set_sheet_data(data, reset_col_positions=False, reset_row_positions=True, reset_highlights=True)

    # ==========================================
    # ADD ITEM -> Medicine Master
    # ==========================================

    def _add_item(self):
        if self.on_open_medicine_master is not None:
            self.on_open_medicine_master()
        else:
            ui_popups.show_info(self.frame, 
                "Add Item",
                "New medicines are added from the Medicine Master screen.",
            )

    # ==========================================
    # ENTER = quick "Change Price" popup
    # ==========================================

    def _selected_medicine_id(self):
        current = self.table.get_currently_selected()
        if not current or current.row is None or current.row >= len(self._row_ids):
            return None
        return self._row_ids[current.row]

    def _quick_price_edit(self, event=None):
        med_id = self._selected_medicine_id()
        if med_id is None:
            return

        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute("SELECT name, mrp, purchase, sale FROM medicine_master WHERE id=?", (med_id,))
        row = cur.fetchone()
        conn.close()
        if row is None:
            return
        name, mrp, purchase, sale = row

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as Medicine Master's own Quick Edit popup
        # (see ui_style.popup_header()'s docstring) - this popup was
        # already modal (grab_set() below), so only the look changes.
        popup = tk.Toplevel(self.frame)
        popup.title(f"Change Price - {name}")
        popup.resizable(False, False)
        popup.transient(self.frame.winfo_toplevel())
        popup.grab_set()
        # Esc key also closes this popup (same as Cancel/the window's X).
        popup.bind("<Escape>", lambda event: popup.destroy())
        popup.focus_force()

        outer = ui_style.popup_header(popup, "Change Price", icon="₹")
        body = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=20, pady=16)
        body.pack(fill="both", expand=True)

        tk.Label(
            body, text=name, bg=theme.SURFACE_WHITE, fg=theme.TEXT_PRIMARY,
            font=("Segoe UI", 12, "bold"), wraplength=280, justify="left",
        ).pack(fill="x", pady=(0, 12))

        fields = {}
        form = tk.Frame(body, bg=theme.SURFACE_WHITE)
        form.pack(fill="x")
        for i, (label, value) in enumerate([("MRP", mrp), ("Purchase Price", purchase), ("Sale Price", sale)]):
            tk.Label(
                form, text=label, bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
                font=("Segoe UI", 10), anchor="w", width=13,
            ).grid(row=i, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=str(value if value is not None else 0))
            tk.Entry(
                form, textvariable=var, width=14, font=("Segoe UI", 10),
                bg=theme.SURFACE_FIELD, relief="flat", highlightthickness=1,
                highlightbackground=theme.BORDER_DEFAULT, highlightcolor=theme.BORDER_FOCUS,
            ).grid(row=i, column=1, pady=4, ipady=3)
            fields[label] = var

        def save_quick_price():
            try:
                new_mrp = float(fields["MRP"].get())
                new_purchase = float(fields["Purchase Price"].get())
                new_sale = float(fields["Sale Price"].get())
            except ValueError:
                ui_popups.show_error(popup, "Change Price", "MRP/Purchase Price/Sale Price must be numbers.")
                return

            conn2 = sqlite3.connect(DB)
            cur2 = conn2.cursor()
            cur2.execute(
                "UPDATE medicine_master SET mrp=?, purchase=?, sale=? WHERE id=?",
                (new_mrp, new_purchase, new_sale, med_id),
            )
            conn2.commit()
            conn2.close()

            audit_log.log_action(
                "Price List", "Change Price", f"Updated MRP/Purchase/Sale for '{name}' (id={med_id})"
            )

            popup.destroy()
            self.load_data()

        btn_row = tk.Frame(body, bg=theme.SURFACE_WHITE)
        btn_row.pack(fill="x", pady=(18, 0))
        ui_style.flat_button(btn_row, "Cancel", theme.ACCENT_NEUTRAL, popup.destroy).pack(side="right")
        ui_style.flat_button(
            btn_row, "Save", theme.STATUS_SUCCESS, save_quick_price,
        ).pack(side="right", padx=(0, 8))

        # No explicit width/height (was a fixed 300x230 guess) - see
        # ui_style.center_window()'s own docstring for why sizing to
        # real packed content is safer.
        ui_style.center_window(popup, parent=self.frame.winfo_toplevel())

    # ==========================================
    # CTRL+ENTER = full "Update Batch and Price" popup
    # ==========================================

    def _full_batch_price_edit(self, event=None):
        med_id = self._selected_medicine_id()
        if med_id is None:
            return

        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute(
            "SELECT name, batch, expiry, gst, mrp, purchase, sale FROM medicine_master WHERE id=?", (med_id,)
        )
        row = cur.fetchone()
        conn.close()
        if row is None:
            return
        name, batch, expiry, gst, mrp, purchase, sale = row

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring) - already modal
        # (grab_set() below), so only the look changes here.
        popup = tk.Toplevel(self.frame)
        popup.title(f"Update Batch and Price - {name}")
        popup.resizable(False, False)
        popup.transient(self.frame.winfo_toplevel())
        popup.grab_set()
        # Esc key also closes this popup (same as Cancel/the window's X).
        popup.bind("<Escape>", lambda event: popup.destroy())
        popup.focus_force()

        outer = ui_style.popup_header(popup, "Update Batch and Price", icon="₹")
        body = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=20, pady=16)
        body.pack(fill="both", expand=True)

        tk.Label(
            body, text=name, bg=theme.SURFACE_WHITE, fg=theme.TEXT_PRIMARY,
            font=("Segoe UI", 12, "bold"), wraplength=350, justify="left",
        ).pack(fill="x", pady=(0, 12))

        form = tk.Frame(body, bg=theme.SURFACE_WHITE)
        form.pack(fill="x")

        fields = {}

        def add_row(r, label, value, width=16, editable=True):
            tk.Label(
                form, text=label, bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
                font=("Segoe UI", 10), anchor="w", width=14,
            ).grid(row=r, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=str(value if value is not None else ""))
            entry = tk.Entry(
                form, textvariable=var, width=width, state=("normal" if editable else "readonly"),
                font=("Segoe UI", 10), bg=(theme.SURFACE_FIELD if editable else theme.SURFACE_PAGE),
                relief="flat", highlightthickness=1, highlightbackground=theme.BORDER_DEFAULT,
                highlightcolor=theme.BORDER_FOCUS,
            )
            entry.grid(row=r, column=1, pady=4, sticky="w", ipady=3)
            fields[label] = var
            return var

        add_row(0, "Batch No", batch or "", editable=False)
        add_row(1, "Exp Date (MM/YY)", expiry or "")
        add_row(2, "Tax % (GST)", gst if gst is not None else 0.0)
        add_row(3, "MRP", mrp if mrp is not None else 0.0)
        add_row(4, "Purchase Price", purchase if purchase is not None else 0.0)
        add_row(5, "Sale Price", sale if sale is not None else 0.0)

        margin_var = tk.StringVar(value="")
        tk.Label(
            form, text="Profit Margin %", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
            font=("Segoe UI", 10), anchor="w", width=14,
        ).grid(row=6, column=0, sticky="w", pady=4)
        tk.Label(
            form, textvariable=margin_var, bg=theme.SURFACE_WHITE, fg=theme.TEXT_MUTED, anchor="w",
        ).grid(row=6, column=1, sticky="w", pady=4)

        def _refresh_margin(*_a):
            try:
                p = float(fields["Purchase Price"].get())
                s = float(fields["Sale Price"].get())
                margin_var.set(f"{((s - p) / p * 100):.2f} %" if p else "-")
            except ValueError:
                margin_var.set("-")

        fields["Purchase Price"].trace_add("write", _refresh_margin)
        fields["Sale Price"].trace_add("write", _refresh_margin)
        _refresh_margin()

        def save_full_edit():
            try:
                new_expiry = fields["Exp Date (MM/YY)"].get().strip()
                if new_expiry:
                    datetime.strptime(new_expiry, EXP_FMT)  # validates format only
                new_gst = float(fields["Tax % (GST)"].get())
                new_mrp = float(fields["MRP"].get())
                new_purchase = float(fields["Purchase Price"].get())
                new_sale = float(fields["Sale Price"].get())
            except ValueError:
                ui_popups.show_error(popup, 
                    "Update Batch and Price",
                    "Check Exp Date (MM/YY) and that Tax %/MRP/Purchase Price/Sale Price are numbers.",
                )
                return

            conn2 = sqlite3.connect(DB)
            cur2 = conn2.cursor()
            cur2.execute(
                "UPDATE medicine_master SET expiry=?, gst=?, mrp=?, purchase=?, sale=? WHERE id=?",
                (new_expiry, new_gst, new_mrp, new_purchase, new_sale, med_id),
            )
            conn2.commit()
            conn2.close()

            audit_log.log_action(
                "Price List", "Update Batch and Price",
                f"Updated batch/price fields for '{name}' batch '{batch}' (id={med_id})"
            )

            popup.destroy()
            self.load_data()

        btn_row = tk.Frame(body, bg=theme.SURFACE_WHITE)
        btn_row.pack(fill="x", pady=(18, 0))
        ui_style.flat_button(btn_row, "Cancel", theme.ACCENT_NEUTRAL, popup.destroy, width=14).pack(side="right")
        ui_style.flat_button(
            btn_row, "Save", theme.STATUS_SUCCESS, save_full_edit, width=14,
        ).pack(side="right", padx=(0, 8))

        # No explicit width/height (was a fixed 380x420 guess) - see
        # ui_style.center_window()'s own docstring for why sizing to
        # real packed content is safer.
        ui_style.center_window(popup, parent=self.frame.winfo_toplevel())
