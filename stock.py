import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
from datetime import datetime

from pricing_utils import get_pack_multiplier
import ui_style
import generic_mapping
import stock_repository as repo
# Category/Dosage Form option lists (Aug 2026 Stock filter round) - kept
# as the single source of truth in medicine_master.py (where the Add/
# Edit form's own dropdowns already use them) so the Stock filter and
# the Medicine Master entry form can never drift apart on spelling.
from medicine_master import CATEGORY_OPTIONS, DOSAGE_FORM_OPTIONS

import re
import ui_popups
import theme


class Stock:

    def __init__(self, frame):
        self.frame = frame
        self.search = tk.StringVar()
        # Category / Dosage Form filter dropdowns (Aug 2026 Stock filter
        # round) - "All" means "don't filter on this", same convention
        # stock_repository.list_medicines_filtered() itself uses.
        self.category_filter = tk.StringVar(value="All")
        self.dosage_filter = tk.StringVar(value="All")
        self._row_names = []
        # Unpadded rows currently on screen (post-search-filter) - see
        # medicine_master.py's identical _current_display_rows comment.
        # Used by export/print (2026-08-22).
        self._current_display_rows = []

        self.create_ui()
        self.create_footer()
        self.load_stock()
        self._bind_shortcuts()

    # ==========================================
    # USER INTERFACE (UI)
    # ==========================================

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="STOCK MANAGEMENT",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        top = tk.LabelFrame(
            self.frame,
            text="Search Medicine",
            font=("Segoe UI", 10, "bold")
        )
        top.pack(fill="x", padx=10, pady=10)

        tk.Label(top, text="Medicine").grid(row=0, column=0, padx=5, pady=5)
        
        search_entry = tk.Entry(top, textvariable=self.search, width=35)
        search_entry.grid(row=0, column=1)
        # Stored so F3 (see _bind_shortcuts()) can jump focus here.
        self._search_entry = search_entry
        self.search.trace_add("write", lambda *args: self.search_stock())

        tk.Button(
            top, text="Search", bg="#27AE60", fg="white", width=12, command=self.search_stock
        ).grid(row=0, column=2, padx=5)

        tk.Button(
            top, text="Refresh", width=12, command=self.load_stock
        ).grid(row=0, column=3, padx=5)

        tk.Button(
            top, text="View Substitutes", bg="#EF6C00", fg="white", width=16,
            command=self.view_substitutes
        ).grid(row=0, column=4, padx=5)

        # Category / Dosage Form filter row (Aug 2026 Stock filter round)
        # - own row under the search row, same LabelFrame. Both are
        # readonly (state="readonly") - these filter a query, so only
        # the exact stored values make sense here, not free typing.
        # Values are the standard option lists from medicine_master.py
        # unioned with whatever's actually already in the database (see
        # _filter_dropdown_values()'s own docstring for why), so this
        # dropdown never hides a category/dosage form some other screen
        # already wrote into medicine_master.
        tk.Label(top, text="Category").grid(row=1, column=0, padx=5, pady=5)
        self.category_combo = ttk.Combobox(
            top, textvariable=self.category_filter, width=32,
            state="readonly",
            values=self._filter_dropdown_values(CATEGORY_OPTIONS, repo.list_distinct_categories()),
        )
        self.category_combo.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.category_combo.bind("<<ComboboxSelected>>", lambda event: self.apply_filters())

        tk.Label(top, text="Dosage Form").grid(row=1, column=2, padx=5, pady=5)
        self.dosage_combo = ttk.Combobox(
            top, textvariable=self.dosage_filter, width=20,
            state="readonly",
            values=self._filter_dropdown_values(DOSAGE_FORM_OPTIONS, repo.list_distinct_dosage_forms()),
        )
        self.dosage_combo.grid(row=1, column=3, padx=5, pady=5, sticky="w")
        self.dosage_combo.bind("<<ComboboxSelected>>", lambda event: self.apply_filters())

        # Full-width table - no side-by-side info panel anymore (see the
        # "Selected Medicine Info" popup further down): the panel used to
        # sit here permanently, showing "(select a row)" for most of a
        # session and eating ~300px of width the table could otherwise
        # use - which is exactly the "screen doesn't fully occupy /
        # looks letterboxed" feedback from Aug 2026. table now packs
        # straight into self.frame, same as Medicine Master's table
        # already does.
        table = tk.Frame(self.frame)
        table.pack(fill="both", expand=True, padx=10, pady=10)

        # Rack dropped - every row showed it blank (never populated
        # anywhere in the app) and Medicine Master's table never had
        # this column either, so it was just empty space, not a
        # missing-data problem.
        self._stock_cols = ("S.No", "Medicine", "Company", "Batch", "Expiry", "Purchase", "Sale", "MRP", "Stock")

        col_widths = {
            "S.No": 55,
            "Medicine": 270,
            "Company": 120,
            "Batch": 100,
            "Expiry": 65,
            "Purchase": 90,
            "Sale": 90,
            "MRP": 90,
            "Stock": 80
        }
        # Medicine=270 / Expiry=65 match medicine_master.py's 2026-08-22
        # rebalancing - real get_column_text_width() measurements against
        # pharmacy.db's actual longest medicine name (22 chars) needed
        # 195px and "MM/YY" Expiry text needed 58px; these add headroom
        # for future longer names while keeping Expiry small, per the
        # user's "medicine column full length, expiry small" request.

        # 2026-08-30: switched from make_excel_sheet() (tksheet) to
        # make_plain_sheet() (plain ttk.Treeview) - see medicine_master.py's
        # ui_style.PlainSheet docstring for the full rationale. Every
        # other call below (set_sheet_data/highlight_rows/
        # get_currently_selected/column_width/etc) is unchanged,
        # PlainSheet answers to the same method names.
        self.stockTable = ui_style.make_plain_sheet(
            table, self._stock_cols, col_widths,
            text_columns=("Medicine", "Company", "Batch", "Expiry"),
            center_columns=("S.No",),
        )
        self.stockTable.pack(fill="both", expand=True)
        self.stockTable.enable_bindings(*ui_style.READONLY_BINDINGS)
        ui_style.enable_row_highlight_on_select(self.stockTable)

        # ERP-wide keyboard-nav pass (Aug 2026): Down/Enter in the search
        # box jumps into the grid and opens its first result's Medicine
        # Info popup - see ui_style.bind_search_to_grid()'s docstring.
        ui_style.bind_search_to_grid(
            self._search_entry, self.stockTable,
            row_count_fn=lambda: len(self._row_names),
        )

        # "Medicine" column stretch fix (same pattern as
        # medicine_master.py) - make_excel_sheet() sizes columns to fixed
        # pixel widths regardless of the container's actual packed width,
        # which left a blank strip past "Stock" whenever the window is
        # wider than the sum of col_widths. Originally this stretched
        # "Stock" (the last column); retargeted to "Medicine" on
        # 2026-08-22 alongside medicine_master.py's identical change, so
        # Medicine always shows the FULL name with no truncation and also
        # soaks up the leftover width, instead of a numeric last column
        # growing wide for no visual benefit. Bound to the ROOT window's
        # <Configure> (not the Sheet's own - that fires during normal
        # scrolling too and caused a real stutter regression on Medicine
        # Master) with winfo_exists()/TclError guards since the root's
        # <Configure> keeps firing after this screen's widgets are
        # destroyed on navigation.
        self._stock_stretch_col_index = self._stock_cols.index("Medicine")
        self._stock_last_col_width = None

        def _stretch_stock_last_column(event=None):
            try:
                if not self.stockTable.winfo_exists():
                    return
                self.stockTable.update_idletasks()
                widget_width = self.stockTable.winfo_width()
            except tk.TclError:
                return
            if widget_width <= 1:
                return
            fixed = sum(
                col_widths.get(c, 120) + ui_style.CENTER_PAD_WIDTH
                for c in self._stock_cols
                if c != "Medicine"
            )
            # No MAX_STRETCH_COLUMN_WIDTH cap here (2026-08-22 fix) - see
            # medicine_master.py's identical fix comment: the cap left a
            # real blank strip of plain background past the last column on
            # a maximized window, which the user explicitly asked to be
            # removed. Medicine's own configured width (270px, sized off
            # the longest real medicine name in pharmacy.db) is the floor
            # here, so this only ever grows it further, never shrinks
            # below what's needed to show the full name.
            new_width = max(
                col_widths["Medicine"] + ui_style.CENTER_PAD_WIDTH,
                widget_width - fixed - ui_style._SCROLLBAR_ALLOWANCE
            )
            if new_width == self._stock_last_col_width:
                return
            self._stock_last_col_width = new_width
            try:
                self.stockTable.column_width(column=self._stock_stretch_col_index, width=new_width)
            except tk.TclError:
                pass

        self.stockTable.after(200, _stretch_stock_last_column)
        self.frame.winfo_toplevel().bind("<Configure>", _stretch_stock_last_column, add=True)

        # 2026-08-30 (user report): this used to fire on_row_select() off
        # "<<SheetSelect>>" (every single click/row-selection change,
        # same event Medicine Master's grid uses) - fine for Medicine
        # Master, where a single click is meant to load the row into the
        # bottom edit form, but on Stock it popped open the "Selected
        # Medicine Info" window on every single click, which kept
        # stealing keyboard focus (see _show_info_popup()'s
        # focus_force()) and made scrolling/clicking through the list
        # feel stuck. Switched to a real double-click - single click now
        # just selects/highlights the row (enable_row_highlight_on_
        # select() above still handles that), and the info popup only
        # opens on an explicit double-click, matching what the user
        # actually wants from this screen.
        self.stockTable.bind("<Double-1>", self.on_row_select, add=True)

        # "Selected Medicine Info" used to live here as an always-visible
        # side panel (composition, GST%, stock value, expiry countdown
        # for whichever row is selected). It's now a small popup window
        # instead, built on demand by _show_info_popup() and opened from
        # on_row_select() below - same information, but it no longer
        # permanently claims ~300px of width for what's blank
        # "(select a row)" placeholder most of the time.
        self._info_popup = None
        self._info_popup_widgets = None

        bottom = tk.Frame(self.frame)
        bottom.pack(fill="x", padx=10, pady=10)

        self.lblTotal = tk.Label(bottom, text="Total Medicines : 0", fg="blue", font=("Segoe UI", 11, "bold"))
        self.lblTotal.pack(side="left")

        # MRP-ன் மூலம் விற்கப்படும் மொத்த மதிப்பு (Total MRP Value) காட்டுவது
        self.lblMrpTotal = tk.Label(bottom, text="Total MRP Value : ₹0.00", fg="#8E44AD", font=("Segoe UI", 11, "bold"))
        self.lblMrpTotal.pack(side="left", padx=20)

        self.lblValue = tk.Label(bottom, text="Stock Purchase Value : ₹0.00", fg="green", font=("Segoe UI", 11, "bold"))
        self.lblValue.pack(side="right")

    # ---------------- Footer / keyboard shortcuts / Export / Print ----------------
    # Same shared ui_style helper as medicine_master.py/brand_master_gui.py
    # (2026-08-22), but Stock Management has no add/edit/delete form of
    # its own - it's a pure browse/search view (editing happens on
    # Medicine Master or via Purchase/Stock Adjustment) - so this screen
    # only gets F3=Search plus Print/Export, no ENTER/DEL/CTRL+S/Quick
    # Edit shortcuts. Double-clicking a row already opens the "Selected
    # Medicine Info" popup via on_row_select() (bound to <Double-1>
    # further down) - nothing new needed there.

    def create_footer(self):
        footer = ui_style.make_shortcut_footer(
            self.frame,
            shortcuts=[("F3", "Search")],
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

    def _current_export_rows(self):
        return list(self._stock_cols), list(self._current_display_rows)

    def export_action(self):
        headers, rows = self._current_export_rows()
        ui_style.export_rows_to_excel(self.frame, headers, rows, default_filename="stock")

    def print_action(self):
        headers, rows = self._current_export_rows()
        ui_style.print_rows_as_report(headers, rows, title="Stock Management", parent=self.frame)

    # ==========================================
    # LOAD STOCK DATA
    # ==========================================

    @staticmethod
    def _filter_dropdown_values(base_options, db_values):
        """"All" + `base_options` (in their own declared order) + any
        value already present in medicine_master that isn't in
        `base_options` (appended at the end, in whatever order the
        database returned them) - see create_ui()'s comment on why the
        union matters, not just the fixed list."""
        merged = list(base_options)
        seen = set(base_options)
        for value in db_values:
            if value not in seen:
                merged.append(value)
                seen.add(value)
        return ["All"] + merged

    def load_stock(self):
        self.search.set("")
        self.category_filter.set("All")
        self.dosage_filter.set("All")
        rows = repo.list_medicines()
        self._render_stock_rows(rows)

    def search_stock(self):
        self.apply_filters()

    def apply_filters(self):
        """Shared by the Medicine search box (trace + Search button) and
        both Category/Dosage Form comboboxes - whichever of the three
        changed, all three are always applied together."""
        rows = repo.list_medicines_filtered(
            self.search.get(), self.category_filter.get(), self.dosage_filter.get()
        )
        self._render_stock_rows(rows)

    def _render_stock_rows(self, rows):
        """
        Shared by load_stock() and search_stock() - both used to
        duplicate this entire loop with only the SQL WHERE clause
        differing. Builds the sheet data plus which row indices need
        the "low stock" / "expired" highlight, then applies both in one
        shot via set_sheet_data()/highlight_rows() (tksheet's
        alternate_color option handles the plain zebra striping for
        every row that isn't explicitly highlighted, so there's no
        manual even/odd bookkeeping left to do here at all).
        """
        total_purchase_value = 0.0
        total_mrp_value = 0.0
        today_date = datetime.today().replace(day=1)

        data = []
        low_rows = []
        expired_rows = []
        self._row_names = []  # display row index -> medicine name, for view_substitutes()

        for index, row in enumerate(rows, start=1):
            self._row_names.append(row[1])
            stock = int(row[9] or 0)
            expiry = row[4]
            is_expired = False

            try:
                exp = datetime.strptime(expiry, "%m/%y")
                if exp < today_date:
                    is_expired = True
            except Exception:
                pass

            row_idx = index - 1  # tksheet rows are 0-based, unlike our S.No column
            if is_expired:
                expired_rows.append(row_idx)
            elif stock <= 10:
                low_rows.append(row_idx)

            # row[5] (rack) is fetched but no longer displayed - see the
            # Rack-column removal note in create_ui().
            data.append([index, row[1], row[2], row[3], row[4], row[6], row[7], row[8], row[9]])

            try:
                pack_raw = str(row[10] if len(row) > 10 and row[10] else "1")
                gst_percent = float(row[11] if len(row) > 11 and row[11] else 0.0)
                
                pack_mult = get_pack_multiplier(pack_raw)
                base_box_price = float(row[6] or 0.0)
                box_price_with_tax = base_box_price + (base_box_price * (gst_percent / 100))
                
                unit_price = box_price_with_tax / pack_mult
                total_purchase_value += unit_price * stock

                # ─── திருத்தப்பட்ட சரியான மொத்த MRP மதிப்பு கணக்கீடு ───
                mrp_box = float(row[8] or 0.0)
                unit_mrp = mrp_box / pack_mult if pack_mult > 0 else mrp_box
                total_mrp_value += unit_mrp * stock
            except Exception:
                pass

        # Padded with blank rows (see ui_style.pad_for_full_grid's own
        # docstring) so the grid keeps drawing borders/zebra striping all
        # the way down a maximized window even when there are only a
        # handful of real medicines - without this a small inventory
        # left a large plain-white gap below the last real row, which is
        # what read as "the screen doesn't fill/look like a letterboxed
        # video" in the Aug 2026 feedback. Real row indices (used by
        # highlight_rows() and self._row_names below) are unaffected -
        # padding rows are appended after every real row, never between.
        # Unpadded copy kept for Export/Print - see medicine_master.py's
        # identical _current_display_rows comment (2026-08-22).
        self._current_display_rows = list(data)

        data = ui_style.pad_for_full_grid(data, len(self._stock_cols))

        # reset_col_positions=False keeps our custom column widths from
        # resetting to tksheet's 120px default on every refresh.
        # reset_row_positions must stay True (its default) - tksheet only
        # draws as many rows as len(row_positions)-1, NOT len(data), so
        # with reset_row_positions=False on a sheet that started with 0
        # rows, row_positions never grows and every row stays invisible
        # even though the data is actually there (this is what caused
        # the "table looks empty but totals are correct" bug).
        self.stockTable.set_sheet_data(data, reset_col_positions=False, reset_row_positions=True, reset_highlights=True)
        if low_rows:
            self.stockTable.highlight_rows(rows=low_rows, bg="orange", fg="black")
        if expired_rows:
            self.stockTable.highlight_rows(rows=expired_rows, bg="red", fg="white")

        self.lblTotal.config(text=f"Total Medicines : {len(rows)}")
        self.lblMrpTotal.config(text=f"Total MRP Value : ₹{total_mrp_value:,.2f}")
        self.lblValue.config(text=f"Stock Purchase Value : ₹{total_purchase_value:,.2f}")

        # Reloading the sheet drops whatever row was selected - close any
        # open info popup rather than leaving a stale medicine's details
        # showing after Refresh/Search.
        self._close_info_popup()

    # ==========================================
    # SELECTED MEDICINE INFO POPUP
    # ==========================================
    # Used to be an always-visible side panel next to the table (see
    # create_ui()'s comment on why that changed) - now a small reusable
    # Toplevel, built once on first use and just updated/re-shown on
    # every later row click instead of spawning a new window each time.

    def _close_info_popup(self):
        if self._info_popup is not None:
            try:
                self._info_popup.destroy()
            except tk.TclError:
                pass
            self._info_popup = None
            self._info_popup_widgets = None

    def _ensure_info_popup(self):
        if self._info_popup is not None:
            try:
                if self._info_popup.winfo_exists():
                    return
            except tk.TclError:
                pass
            self._info_popup = None
            self._info_popup_widgets = None

        popup = tk.Toplevel(self.frame)
        popup.title("Selected Medicine Info")
        popup.resizable(False, False)
        popup.transient(self.frame.winfo_toplevel())
        popup.protocol("WM_DELETE_WINDOW", self._close_info_popup)
        # Esc key also closes this popup, same as the Close button and
        # the window's own X button.
        popup.bind("<Escape>", lambda event: self._close_info_popup())

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as ui_popups.py's modal dialogs (see
        # ui_style.popup_header()'s own docstring) - applied here too
        # even though this popup deliberately STAYS non-modal, since the
        # pharmacist needs to keep clicking through grid rows without a
        # modal blocking that (see this popup's section header comment
        # above).
        outer = ui_style.popup_header(popup, "Selected Medicine Info", icon="ℹ")
        body = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=16, pady=14)
        body.pack(fill="both", expand=True)

        name_lbl = tk.Label(
            body, text="(select a row)", bg=theme.SURFACE_WHITE, fg=theme.TEXT_PRIMARY,
            font=("Segoe UI", 12, "bold"), wraplength=300, justify="left", anchor="w"
        )
        name_lbl.pack(fill="x", pady=(0, 4))

        gst_lbl = tk.Label(
            body, text="", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
            font=("Segoe UI", 10), anchor="w"
        )
        gst_lbl.pack(fill="x")

        stock_value_lbl = tk.Label(
            body, text="", bg=theme.SURFACE_WHITE, fg=theme.STATUS_SUCCESS,
            font=("Segoe UI", 10, "bold"), anchor="w"
        )
        stock_value_lbl.pack(fill="x", pady=(2, 0))

        expiry_lbl = tk.Label(
            body, text="", bg=theme.SURFACE_WHITE, font=("Segoe UI", 10, "bold"), anchor="w"
        )
        expiry_lbl.pack(fill="x", pady=(2, 8))

        tk.Frame(body, height=1, bg=theme.BORDER_DEFAULT).pack(fill="x", pady=4)

        composition_lbl = tk.Label(
            body, text="", bg=theme.SURFACE_WHITE, fg=theme.PRIMARY,
            wraplength=300, justify="left", anchor="nw"
        )
        composition_lbl.pack(fill="both", expand=True, pady=4)

        ui_style.flat_button(body, "Close", theme.PRIMARY, self._close_info_popup).pack(pady=(10, 0))

        self._info_popup = popup
        self._info_popup_widgets = {
            "name": name_lbl, "gst": gst_lbl, "stock_value": stock_value_lbl,
            "expiry": expiry_lbl, "composition": composition_lbl,
        }

        # Centered over the main window (was anchored to the top-right
        # corner) - falls back to Tk's own default placement (silently,
        # via the TclError guard) if the main window's geometry isn't
        # measurable yet for any reason. No explicit width/height here
        # (the Aug 2026 restyle made this taller than the old fixed
        # 320x320 guess) - see ui_style.center_window()'s own docstring
        # for why omitting them and letting it size to the real packed
        # content is the safe way to do this.
        try:
            root = self.frame.winfo_toplevel()
            ui_style.center_window(popup, parent=root)
        except tk.TclError:
            pass

    def _show_info_popup(self, name, gst_text, stock_value_text, expiry_text, expiry_color, composition_text):
        self._ensure_info_popup()
        w = self._info_popup_widgets
        w["name"].config(text=name)
        w["gst"].config(text=gst_text)
        w["stock_value"].config(text=stock_value_text)
        w["expiry"].config(text=expiry_text, fg=expiry_color)
        w["composition"].config(text=composition_text)
        self._info_popup.deiconify()
        self._info_popup.lift()
        # Give the popup keyboard focus so Esc-to-close actually receives
        # the keypress (it's non-modal, so without this the main grid
        # keeps focus and Esc would go nowhere) - same fix as Brand
        # Master's equivalent popup.
        self._info_popup.focus_force()
        self._info_popup.lift()

    def on_row_select(self, event=None):
        current = self.stockTable.get_currently_selected()
        if not current or current.row is None or current.row >= len(self._row_names):
            # Either nothing selected, or a blank padding row (see
            # ui_style.pad_for_full_grid) was clicked - leave any
            # already-open popup showing its last real medicine rather
            # than blanking it out on every stray click.
            return

        name = self._row_names[current.row]
        row = repo.get_medicine_summary(name)
        if not row:
            return

        generic_text, gst, purchase, stock, expiry, pack_size = row
        generic_text = (generic_text or "").strip()
        stock = stock or 0
        purchase = float(purchase or 0.0)
        gst = float(gst or 0.0)

        try:
            pack_mult = get_pack_multiplier(str(pack_size or "1")) or 1
        except Exception:
            pack_mult = 1
        unit_price = (purchase + purchase * (gst / 100)) / pack_mult
        stock_value = unit_price * stock

        # Same %m/%y convention used everywhere else in this app
        # (billing.py's FIFO batching, this screen's own expired-row
        # highlighting) - color thresholds match Smart Alerts' red
        # (expired) / orange (<=90d) / green (safe) scheme.
        try:
            exp_dt = datetime.strptime(expiry, "%m/%y").replace(day=1)
            days_left = (exp_dt - datetime.now().replace(day=1)).days
            if days_left < 0:
                color, txt = "#e74c3c", f"EXPIRED ({expiry})"
            elif days_left <= 90:
                color, txt = "#e67e22", f"{days_left} days left ({expiry})"
            else:
                color, txt = "#2E7D32", f"{days_left} days left ({expiry})"
        except Exception:
            color, txt = "#777777", (expiry or "-")

        if generic_text:
            uses = generic_mapping.get_composition_uses(generic_text)
            action_class = generic_mapping.get_composition_action_class(generic_text)
            habit_forming = generic_mapping.get_composition_habit_forming(generic_text)

            parts = []
            if action_class:
                parts.append(f"Class: {action_class}")
            if uses:
                parts.append(f"Uses: {uses}")
            comp_text = "\n".join(parts) if parts else "No composition info on file for this generic."
            if habit_forming:
                comp_text = "⚠ HABIT FORMING\n" + comp_text
        else:
            comp_text = "No composition/generic saved for this medicine."

        self._show_info_popup(
            name,
            f"GST: {gst:.1f}%",
            f"Stock Value: ₹{stock_value:,.2f}",
            f"Expiry: {txt}",
            color,
            comp_text,
        )

    # ==========================================
    # SUBSTITUTE MEDICINE LOOKUP
    # ==========================================

    def view_substitutes(self):
        """Shows every brand sharing the selected row's composition -
        unlike Billing's version, this is a browse-anytime tool (no
        in_stock_only filter), since Stock is about knowing what COULD
        cover a composition, not just what's sellable right now."""
        current = self.stockTable.get_currently_selected()
        if not current or current.row is None or current.row >= len(self._row_names):
            ui_popups.show_info(self.frame, "Select a Row", "Select a medicine row first.")
            return

        name = self._row_names[current.row]
        row = repo.get_generic(name)

        generic_text = (row[0] or "").strip() if row else ""
        if not generic_text:
            ui_popups.show_info(self.frame, 
                "No Composition Info",
                f'"{name}" has no generic/composition saved, so substitutes '
                "can't be looked up.\n\nAdd its composition in Medicine Master."
            )
            return

        generic_mapping.show_substitute_selector(
            self.frame, generic_text, exclude_name=name, in_stock_only=False
        )