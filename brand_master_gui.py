"""
brand_master_gui.py
LifeCare Pharmacy ERP - Brand Master screen.

Lets the pharmacist browse/search the Brand Master catalog (see
brand_mapping.py + brand_seed_data.py), add or correct a single brand by
hand, and bulk-add many brands at once by pasting tab-separated rows
copied from Excel/Google Sheets - the same "paste from a spreadsheet"
workflow bulk_import.py already uses for Purchase Entry, so this is meant
to be the pharmacist's own ongoing way to keep growing the catalog with
data they've verified themselves, rather than something built once and
left static.

Browse/Add/Edit tab deliberately matches medicine_master.py's own
look/structure (LabelFrame form -> button row -> tksheet grid ->
Selected-row info panel) rather than the plain ttk.Treeview list this
screen started with - both so the two screens read as the same app, and
because tksheet's row-select ("<<SheetSelect>>") is the already-proven
click-to-load pattern medicine_master.py uses, instead of ttk.Treeview's
click handling.
"""

import tkinter as tk
from tkinter import ttk, messagebox

import brand_mapping
import ui_style
import theme
from app_paths import DB_NAME
import ui_popups

DOSAGE_FORM_OPTIONS = [
    "Tablet", "Capsule", "Syrup", "Injection", "Ointment",
    "Cream", "Lotion", "Drops", "Other",
]


class BrandMaster:

    def __init__(self, frame):
        self.frame = frame
        brand_mapping.ensure_brand_master(DB_NAME)

        self.brand_name = tk.StringVar()
        self.generic_text = tk.StringVar()
        self.dosage_form = tk.StringVar()
        self.manufacturer = tk.StringVar()
        self.category = tk.StringVar()
        self.search = tk.StringVar()

        # Parallel to whatever's currently in the sheet - row i's brand
        # name is self._row_names[i]. Same pattern medicine_master.py
        # uses (self._row_ids) since tksheet rows are plain 0-based
        # positions with no per-row identifier of their own.
        self._row_names = []
        self._selected_brand = None
        # Unpadded rows currently on screen (post-search-filter) - see
        # medicine_master.py's identical _current_display_rows comment.
        # Used by export/print (2026-08-22).
        self._current_display_rows = []

        self.create_ui()
        # DEFERRED DATA LOAD (Aug 2026, perceived-speed pass) - same
        # pattern as medicine_master.py's identical change: create_ui()
        # above touches no DB (it only builds the form/tabs/table
        # widgets), so scheduling load_list() one Tk idle tick later lets
        # the screen's real structure get painted first, with rows
        # filling in a moment after - not a "Loading" placeholder, the
        # real screen just appears sooner and populates itself right away.
        self.frame.after(1, self.load_list)
        self._bind_shortcuts()

    def _dashboard_refresh(self):
        """Called by dashboard.py's screen cache (Aug 2026) when this
        already-built screen is shown again instead of being rebuilt -
        re-reads the DB so brands added/edited elsewhere since this
        screen was last visible show up here too."""
        self.load_list()

    # ---------------- UI ----------------

    def create_ui(self):
        tk.Label(
            self.frame, text="BRAND MASTER - Brand → Generic / Manufacturer / Category / Dosage Form",
            bg="#1565C0", fg="white", font=("Segoe UI", 18, "bold"), pady=10
        ).pack(fill="x")

        notebook = ttk.Notebook(self.frame)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        browse_tab = tk.Frame(notebook, bg="white")
        bulk_tab = tk.Frame(notebook, bg="white")
        notebook.add(browse_tab, text="Browse / Add / Edit")
        notebook.add(bulk_tab, text="Bulk Add (Paste from Excel)")

        self.build_browse_tab(browse_tab)
        self.build_bulk_tab(bulk_tab)
        self.create_footer()

    def build_browse_tab(self, tab):
        # ---- Form (same field-pair-per-row layout as Medicine Master) ----
        form = tk.LabelFrame(tab, text="Add / Edit Brand", font=("Segoe UI", 11, "bold"))
        form.pack(fill="x", padx=10, pady=10)

        fields = [
            ("Brand Name", self.brand_name),
            ("Generic", self.generic_text),
            ("Dosage Form", self.dosage_form),
            ("Manufacturer", self.manufacturer),
            ("Category", self.category),
        ]

        row, col = 0, 0
        for text, var in fields:
            tk.Label(form, text=text).grid(row=row, column=col, padx=5, pady=5, sticky="w")

            if var is self.dosage_form:
                entry = ttk.Combobox(
                    form, textvariable=var, width=23, state="readonly",
                    values=DOSAGE_FORM_OPTIONS
                )
            else:
                width = 35 if var in (self.generic_text, self.manufacturer) else 25
                entry = tk.Entry(form, textvariable=var, width=width)

            entry.grid(row=row, column=col + 1, padx=5, pady=5)

            col += 2
            if col > 6:
                row += 1
                col = 0

        # ---- Button row (mirrors Medicine Master's Save/Update/Delete/
        # Clear/Search row) ----
        btn = tk.Frame(tab)
        btn.pack(fill="x", padx=10, pady=10)

        tk.Button(
            btn, text="Save Brand", bg="green", fg="white", width=12,
            command=self.save_brand
        ).pack(side="left", padx=5)

        tk.Button(
            btn, text="Delete Selected", bg="red", fg="white", width=14,
            command=self.delete_selected
        ).pack(side="left", padx=5)

        tk.Button(
            btn, text="Clear", width=12,
            command=self.clear_form
        ).pack(side="left", padx=5)

        tk.Button(
            btn, text="Load Starter Brands", bg="#EF6C00", fg="white", width=18,
            command=self.load_starter_brands
        ).pack(side="left", padx=5)

        tk.Label(btn, text="Search:").pack(side="left", padx=(20, 5))
        search_entry = tk.Entry(btn, textvariable=self.search, width=25)
        search_entry.pack(side="left")
        # Stored so F3 (see _bind_shortcuts()) can jump focus here.
        self._search_entry = search_entry
        self.search.trace_add("write", lambda *a: self.load_list())

        # ---- Table (tksheet, styled + selected the same way as
        # Medicine Master's grid) - full width, no side panel. The old
        # "Selected Brand Info" side panel that used to sit here is now
        # a small popup (see select_record()/_show_brand_info_popup()
        # below) instead of a fixed ~280px-wide always-visible panel -
        # same Aug 2026 "table should fill the window" pass as Stock.
        table_body = tk.Frame(tab)
        table_body.pack(fill="both", expand=True, padx=10, pady=10)

        grid_frame = tk.Frame(table_body)
        grid_frame.pack(fill="both", expand=True)

        cols = ("S.No", "Brand Name", "Generic", "Dosage Form", "Manufacturer", "Category")
        # Stored on self too (not just this method's local `cols`) because
        # load_list() - a totally separate method, called from __init__
        # right after create_ui() returns - needs the column count for
        # pad_for_full_grid() and only ever had this local `cols` to look
        # at, which is already out of scope by the time it runs. That was
        # a real bug: every single open of Brand Master crashed with
        # "NameError: name 'cols' is not defined" (2026-08-22 hotfix).
        self._brand_cols = cols
        col_widths = {
            "S.No": 55, "Brand Name": 160, "Generic": 260,
            "Dosage Form": 100, "Manufacturer": 170, "Category": 150,
        }
        # 2026-08-30: switched from make_excel_sheet() (tksheet) to
        # make_plain_sheet() (plain ttk.Treeview) - see medicine_master.py's
        # identical comment / ui_style.PlainSheet's docstring for why.
        self.tree = ui_style.make_plain_sheet(
            grid_frame, cols, col_widths,
            text_columns=("Brand Name", "Generic", "Dosage Form", "Manufacturer", "Category"),
            center_columns=("S.No",),
        )
        self.tree.pack(fill="both", expand=True)
        self.tree.enable_bindings(*ui_style.READONLY_BINDINGS)
        ui_style.enable_row_highlight_on_select(self.tree)

        # ERP-wide keyboard-nav pass (Aug 2026): Down/Enter in the search
        # box jumps into the grid and selects/loads its first result -
        # see ui_style.bind_search_to_grid()'s docstring.
        ui_style.bind_search_to_grid(
            self._search_entry, self.tree,
            row_count_fn=lambda: len(self._row_ids),
        )

        # Last-column-stretch fix (same pattern as medicine_master.py /
        # purchase.py / stock.py) - make_excel_sheet() sizes columns to
        # fixed pixel widths regardless of the container's actual packed
        # width, leaving a blank strip past "Category" otherwise. Bound to
        # the ROOT window's <Configure> (not the Sheet's own, which fires
        # during normal scrolling too) with winfo_exists()/TclError guards
        # since the root's <Configure> keeps firing after this screen's
        # widgets are destroyed on navigation.
        self._brand_last_col_width = None

        def _stretch_brand_last_column(event=None):
            try:
                if not self.tree.winfo_exists():
                    return
                self.tree.update_idletasks()
                widget_width = self.tree.winfo_width()
            except tk.TclError:
                return
            if widget_width <= 1:
                return
            fixed = sum(
                col_widths.get(c, 120) + ui_style.CENTER_PAD_WIDTH
                for c in cols[:-1]
            )
            new_width = max(
                120 + ui_style.CENTER_PAD_WIDTH,
                widget_width - fixed - ui_style._SCROLLBAR_ALLOWANCE
            )
            if new_width == self._brand_last_col_width:
                return
            self._brand_last_col_width = new_width
            try:
                self.tree.column_width(column=len(cols) - 1, width=new_width)
            except tk.TclError:
                pass

        self.tree.after(200, _stretch_brand_last_column)
        self.frame.winfo_toplevel().bind("<Configure>", _stretch_brand_last_column, add=True)

        # add=True - see medicine_master.py's identical comment: without
        # it, this call replaces (not adds to) enable_row_highlight_on_
        # select()'s handler registered just above, silently disabling
        # the whole-row highlight.
        self.tree.bind("<<SheetSelect>>", self.select_record, add=True)

        # Popup state for the "Selected Brand Info" window - built once
        # on first use, reused/updated on every later row click. Same
        # pattern as stock.py's Selected Medicine Info popup.
        self._brand_info_popup = None
        self._brand_info_popup_widgets = None
        # Pending "show the info popup" delay (see select_record()) -
        # None when nothing is scheduled.
        self._brand_info_after_id = None

        self.countLabel = tk.Label(tab, text="", fg="#555555")
        self.countLabel.pack(anchor="w", padx=10, pady=(0, 10))

    def build_bulk_tab(self, tab):
        tk.Label(
            tab,
            text=(
                "Paste rows copied from Excel/Google Sheets, one brand per line, columns in this order:\n"
                "Brand Name  |  Generic Composition  |  Dosage Form  |  Manufacturer  |  Category\n"
                "(A header row is fine and will be skipped automatically. Existing brand names are updated, not duplicated.)"
            ),
            justify="left", wraplength=950, fg="gray", bg="white"
        ).pack(anchor="w", padx=10, pady=(10, 5))

        self.bulk_text = tk.Text(tab, height=14)
        self.bulk_text.pack(fill="both", expand=True, padx=10, pady=5)

        bottom = tk.Frame(tab, bg="white")
        bottom.pack(fill="x", padx=10, pady=10)

        self.bulk_result_label = tk.Label(bottom, text="", fg="#0D47A1", bg="white")
        self.bulk_result_label.pack(side="left")

        tk.Button(
            bottom, text="Parse & Add All", bg="#1565C0", fg="white",
            font=("Segoe UI", 10, "bold"), width=18,
            command=self.parse_and_add_bulk, cursor="hand2"
        ).pack(side="right")

    # ---------------- Footer / keyboard shortcuts / Export / Print ----------------
    # Same feature, same shared ui_style helper, as medicine_master.py's
    # identical section - see that file's comment for the full reasoning
    # on why DEL/ENTER/CTRL+ENTER are scoped to self.tree only while
    # CTRL+S/ESC/F3 are bound on the whole toplevel (2026-08-22).

    def create_footer(self):
        footer = ui_style.make_shortcut_footer(
            self.frame,
            shortcuts=[
                ("ENTER", "Edit Row"),
                ("DOUBLE-CLICK", "Quick Edit"),
                ("DEL", "Delete"),
                ("CTRL+S", "Save"),
                ("F3", "Search"),
                ("ESC", "Clear"),
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

        def _escape(event=None):
            # 2026-08-31: since _show_brand_info_popup() no longer force-
            # focuses the info popup (see that method's comment), this
            # root-level Esc needs to close it explicitly first - the
            # popup's own <Escape> binding only fires when IT has focus,
            # which now rarely happens. Falls back to clear_form() when
            # no popup is open, same as before this fix.
            if self._brand_info_popup is not None:
                try:
                    if self._brand_info_popup.winfo_exists():
                        self._close_brand_info_popup()
                        return
                except tk.TclError:
                    pass
            self.clear_form()

        root.bind("<Control-s>", _guarded(self.save_brand), add=True)
        root.bind("<Escape>", _guarded(_escape), add=True)
        root.bind("<F3>", _guarded(lambda: self._search_entry.focus_set()), add=True)

        self.tree.bind("<Delete>", lambda e: self.delete_selected(), add=True)
        self.tree.bind("<Return>", self.select_record, add=True)
        self.tree.bind("<Control-Return>", self._open_quick_edit_popup, add=True)

        # Same reasoning as Medicine Master: a double-click on a row is
        # the gesture mouse users actually reach for, not CTRL+ENTER -
        # bound on self.tree only, never the toplevel.
        self.tree.bind("<Double-Button-1>", self._open_quick_edit_popup, add=True)

    def _current_export_rows(self):
        return list(self._brand_cols), list(self._current_display_rows)

    def export_action(self):
        headers, rows = self._current_export_rows()
        ui_style.export_rows_to_excel(self.frame, headers, rows, default_filename="brand_master")

    def print_action(self):
        headers, rows = self._current_export_rows()
        ui_style.print_rows_as_report(headers, rows, title="Brand Master", parent=self.frame)

    def _open_quick_edit_popup(self, event=None):
        # Same spirit as Medicine Master's Quick Edit popup (Ctrl+Enter)
        # - a small window for the fields someone tweaks most often
        # (Generic/Dosage Form/Manufacturer/Category) without touching
        # the Brand Name itself, additional to (not replacing) the
        # existing full-form edit-via-row-click flow.
        current = self.tree.get_currently_selected()
        if not current or current.row is None or current.row >= len(self._row_names):
            return
        brand_name = self._row_names[current.row]
        info = brand_mapping.lookup_brand(brand_name, DB_NAME)
        if not info:
            return

        # A double-click is preceded by a single click, which already
        # fired select_record() -> _show_brand_info_popup(), so the
        # "Selected Brand Info" popup is on-screen by the time this
        # runs. Close it immediately instead of leaving it stacked
        # behind/beside the new Quick Edit popup - reported by the user
        # as two popups landing on top of each other after a double-click.
        self._close_brand_info_popup()

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as ui_popups.py's modal dialogs and Medicine
        # Master's own Quick Edit popup (see ui_style.popup_header()'s
        # docstring) - this popup was already modal (grab_set() below),
        # so only the look changes here, not the behavior.
        popup = tk.Toplevel(self.frame)
        popup.title(f"Quick Edit - {brand_name}")
        popup.resizable(False, False)
        popup.transient(self.frame.winfo_toplevel())
        popup.grab_set()
        # Esc key also closes this popup (same as Cancel/the window's X).
        popup.bind("<Escape>", lambda event: popup.destroy())
        popup.focus_force()

        outer = ui_style.popup_header(popup, "Quick Edit", icon="✱")
        body = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=20, pady=16)
        body.pack(fill="both", expand=True)

        tk.Label(
            body, text=brand_name, bg=theme.SURFACE_WHITE, fg=theme.TEXT_PRIMARY,
            font=("Segoe UI", 12, "bold"), wraplength=320, justify="left",
        ).pack(fill="x", pady=(0, 12))

        fields = {}
        form = tk.Frame(body, bg=theme.SURFACE_WHITE)
        form.pack(fill="x")
        for i, key in enumerate(["generic_text", "dosage_form", "manufacturer", "category"]):
            label = {"generic_text": "Generic", "dosage_form": "Dosage Form",
                      "manufacturer": "Manufacturer", "category": "Category"}[key]
            tk.Label(
                form, text=label, bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
                font=("Segoe UI", 10), anchor="w", width=13,
            ).grid(row=i, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=info.get(key) or "")
            tk.Entry(
                form, textvariable=var, width=20, font=("Segoe UI", 10),
                bg=theme.SURFACE_FIELD, relief="flat", highlightthickness=1,
                highlightbackground=theme.BORDER_DEFAULT, highlightcolor=theme.BORDER_FOCUS,
            ).grid(row=i, column=1, pady=4, ipady=3)
            fields[key] = var

        def save_quick_edit():
            try:
                brand_mapping.add_brand(
                    brand_name, fields["generic_text"].get(), fields["dosage_form"].get(),
                    fields["manufacturer"].get(), fields["category"].get(), DB_NAME
                )
            except Exception as e:
                ui_popups.show_error(popup, "Quick Edit Failed", str(e))
                return
            popup.destroy()
            self.load_list()

        btn_row = tk.Frame(body, bg=theme.SURFACE_WHITE)
        btn_row.pack(fill="x", pady=(20, 0))
        ui_style.flat_button(
            btn_row, "Cancel", theme.ACCENT_NEUTRAL, popup.destroy,
        ).pack(side="right")
        ui_style.flat_button(
            btn_row, "Save", theme.STATUS_SUCCESS, save_quick_edit,
        ).pack(side="right", padx=(0, 8))

        # Centered AFTER all content is built (no explicit width/height -
        # see ui_style.center_window()'s own docstring for why a fixed
        # guessed size, set before packing anything, is the wrong way to
        # do this - doubly so now that this popup has a header strip).
        ui_style.center_window(popup, parent=self.frame.winfo_toplevel())

    # ---------------- DATA ----------------

    def load_list(self):
        rows = brand_mapping.search_brands(self.search.get(), DB_NAME)

        data = []
        self._row_names = []
        for index, (brand_name, generic_text, dosage_form, manufacturer, category) in enumerate(rows, start=1):
            data.append([index, brand_name, generic_text or "", dosage_form or "", manufacturer or "", category or ""])
            self._row_names.append(brand_name)

        # Unpadded copy kept for Export/Print - see medicine_master.py's
        # identical _current_display_rows comment (2026-08-22).
        self._current_display_rows = list(data)

        # Padded so the grid keeps its border/zebra look all the way
        # down a maximized window even with only a handful of brands -
        # see ui_style.pad_for_full_grid()'s own docstring (same Aug
        # 2026 "screens don't fill the window" fix as Stock/Medicine
        # Master). self._row_names is NOT padded, so on_row_select's
        # len() guard already treats a click on a padding row as "no
        # selection", same as an empty sheet.
        data = ui_style.pad_for_full_grid(data, len(self._brand_cols))

        self.tree.set_sheet_data(data, reset_col_positions=False, reset_row_positions=True, reset_highlights=True)
        self.countLabel.config(text=f"{len(rows)} brand(s)")
        self._close_brand_info_popup()

    def select_record(self, event=None):
        # Same tksheet "row position -> real record" lookup medicine_
        # master.py's select_record() uses - tksheet rows have no iid of
        # their own, so self._row_names (built alongside the sheet data
        # in load_list()) maps the clicked row position back to a brand.
        current = self.tree.get_currently_selected()
        if not current or current.row is None or current.row >= len(self._row_names):
            return

        brand_name = self._row_names[current.row]
        info = brand_mapping.lookup_brand(brand_name, DB_NAME)
        if not info:
            return

        self._selected_brand = info["brand_name"]
        self.brand_name.set(info["brand_name"])
        self.generic_text.set(info["generic_text"] or "")
        self.dosage_form.set(info["dosage_form"] or "")
        self.manufacturer.set(info["manufacturer"] or "")
        self.category.set(info["category"] or "")

        # 2026-08-31: no longer shown immediately. A double-click is two
        # clicks - the first already runs select_record() (right here)
        # before the second click's <Double-Button-1> fires
        # _open_quick_edit_popup(). Showing the popup immediately meant
        # it flashed on screen for a split second on every double-click,
        # only to be closed again a moment later by
        # _open_quick_edit_popup()'s own self._close_brand_info_popup()
        # call - reported by the user as "double click show info" (seeing
        # that flash). Delaying it slightly and cancelling the delay if
        # a double-click follows within the window means a genuine
        # single click still shows it (imperceptible delay), but a
        # double-click never shows it at all.
        self._close_brand_info_popup()
        brand_name_for_popup = info["brand_name"]
        generic_for_popup = info["generic_text"] or "(no generic on file)"
        self._brand_info_after_id = self.frame.after(
            250, lambda: self._show_brand_info_popup(brand_name_for_popup, generic_for_popup)
        )

    def clear_form(self):
        self._selected_brand = None
        self.brand_name.set("")
        self.generic_text.set("")
        self.dosage_form.set("")
        self.manufacturer.set("")
        self.category.set("")

    # ---------------- Selected Brand Info popup ----------------
    # Used to be an always-visible ~280px side panel (see build_browse_
    # tab()'s comment on why that changed) - now a small reusable
    # Toplevel, same pattern as stock.py's Selected Medicine Info popup.

    def _close_brand_info_popup(self):
        # Also cancels a still-pending "show it in 250ms" delay (see
        # select_record()) so callers that want "no info popup, now or
        # shortly" - _open_quick_edit_popup()'s double-click handler
        # being the reason this method takes a delay into account at
        # all - only have to make this one call.
        if self._brand_info_after_id is not None:
            try:
                self.frame.after_cancel(self._brand_info_after_id)
            except (tk.TclError, ValueError):
                pass
            self._brand_info_after_id = None
        if self._brand_info_popup is not None:
            try:
                self._brand_info_popup.destroy()
            except tk.TclError:
                pass
            self._brand_info_popup = None
            self._brand_info_popup_widgets = None

    def _ensure_brand_info_popup(self):
        if self._brand_info_popup is not None:
            try:
                if self._brand_info_popup.winfo_exists():
                    return
            except tk.TclError:
                pass
            self._brand_info_popup = None
            self._brand_info_popup_widgets = None

        popup = tk.Toplevel(self.frame)
        popup.title("Selected Brand Info")
        popup.resizable(False, False)
        popup.transient(self.frame.winfo_toplevel())
        popup.protocol("WM_DELETE_WINDOW", self._close_brand_info_popup)
        # Esc key also closes this popup, same as the Close button and
        # the window's own X button - requested by the user so they
        # don't have to reach for the mouse just to dismiss it.
        popup.bind("<Escape>", lambda event: self._close_brand_info_popup())

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as stock.py's Selected Medicine Info popup
        # (see ui_style.popup_header()'s docstring) - stays non-modal
        # for the same reason that one does (keep clicking through
        # grid rows without a modal blocking that).
        outer = ui_style.popup_header(popup, "Selected Brand Info", icon="ℹ")
        body = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=16, pady=14)
        body.pack(fill="both", expand=True)

        name_lbl = tk.Label(
            body, text="(select a row)", bg=theme.SURFACE_WHITE, fg=theme.TEXT_PRIMARY,
            font=("Segoe UI", 12, "bold"), wraplength=280, justify="left", anchor="w"
        )
        name_lbl.pack(fill="x", pady=(0, 4))

        generic_lbl = tk.Label(
            body, text="", bg=theme.SURFACE_WHITE, fg=theme.PRIMARY, font=("Segoe UI", 10),
            wraplength=280, justify="left", anchor="nw"
        )
        generic_lbl.pack(fill="both", expand=True, pady=(2, 8))

        ui_style.flat_button(body, "Close", theme.PRIMARY, self._close_brand_info_popup).pack(pady=(10, 0))

        self._brand_info_popup = popup
        self._brand_info_popup_widgets = {"name": name_lbl, "generic": generic_lbl}

        try:
            # Centered over the main window (was a fixed top-right offset -
            # kept the same defensive TclError guard, now via the shared
            # ui_style.center_window() utility for consistency app-wide).
            # No explicit width/height (the restyle made this taller
            # than the old fixed 300x160 guess).
            root = self.frame.winfo_toplevel()
            ui_style.center_window(popup, parent=root)
        except tk.TclError:
            pass

    def _show_brand_info_popup(self, brand_name, generic_text):
        self._ensure_brand_info_popup()
        w = self._brand_info_popup_widgets
        w["name"].config(text=brand_name)
        w["generic"].config(text=generic_text)
        self._brand_info_popup.deiconify()
        self._brand_info_popup.lift()
        # 2026-08-31 fix: used to call self._brand_info_popup.focus_force()
        # here on every single call - not just when the popup first
        # opens, but on every row selection change, including the ones
        # <<SheetSelect>> fires for plain Up/Down arrow-key navigation
        # (not just a mouse click). That yanked keyboard focus off
        # self.tree onto this popup after the very first row selection,
        # so every next Up/Down arrow press (the pharmacist trying to
        # scroll through the list) went to the popup instead of the
        # grid and did nothing - the grid looked "stuck" and the popup
        # kept reappearing/updating in front of it. Root cause reported
        # by the user as "click show info, esc key, scroll down show
        # info" - Escape closed the popup, but the very next arrow-key
        # move re-selected a row and reopened it, focus never actually
        # returning to the grid. Dropped entirely: the popup still
        # updates live as the selection changes, but keyboard focus now
        # stays on self.tree, so arrow-key/scrollbar browsing works
        # uninterrupted. Esc-to-close is preserved a different way - see
        # _bind_shortcuts()'s handler below, which closes this popup
        # first (if open) before falling back to clear_form().

    def save_brand(self):
        name = self.brand_name.get().strip()
        if not name:
            ui_popups.show_error(self.frame, "Error", "Brand Name is required.")
            return
        try:
            brand_mapping.add_brand(
                name, self.generic_text.get(), self.dosage_form.get(),
                self.manufacturer.get(), self.category.get(), DB_NAME
            )
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))
            return
        ui_popups.show_info(self.frame, "Saved", f'"{name}" saved to Brand Master.')
        self.clear_form()
        self.load_list()

    def delete_selected(self):
        if not self._selected_brand:
            ui_popups.show_info(self.frame, "Select a Row", "Select a brand from the list first.")
            return
        if not ui_popups.show_confirmation(self.frame, "Delete Brand", f'Remove "{self._selected_brand}" from the Brand Master?'):
            return
        brand_mapping.delete_brand(self._selected_brand, DB_NAME)
        self.clear_form()
        self.load_list()

    def load_starter_brands(self):
        if not ui_popups.show_confirmation(self.frame, 
            "Load Starter Brands",
            "Reload the built-in starter brand list (from brand_seed_data.py)?\n\n"
            "Safe to run anytime - anything already in your list is left untouched, "
            "only missing starter brands get added back."
        ):
            return
        added = brand_mapping.seed_brand_master(DB_NAME, force=True)
        ui_popups.show_info(self.frame, "Done", f"{added} new brand(s) added.")
        self.load_list()

    def parse_and_add_bulk(self):
        raw_text = self.bulk_text.get("1.0", tk.END)
        lines = [l for l in raw_text.splitlines() if l.strip()]

        if lines and ("brand name" in lines[0].lower() or "generic" in lines[0].lower()):
            lines = lines[1:]

        added = 0
        skipped = 0
        for line in lines:
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) == 1:
                # Tolerate comma-separated paste too, in case the source
                # wasn't a real spreadsheet copy (tabs are what Excel/
                # Sheets actually put on the clipboard, commas are the
                # common fallback when someone types/pastes from plain text).
                parts = [p.strip() for p in line.split(",")]

            brand_name = parts[0] if len(parts) > 0 else ""
            generic_text = parts[1] if len(parts) > 1 else ""
            dosage_form = parts[2] if len(parts) > 2 else ""
            manufacturer = parts[3] if len(parts) > 3 else ""
            category = parts[4] if len(parts) > 4 else ""

            if not brand_name:
                skipped += 1
                continue

            brand_mapping.add_brand(brand_name, generic_text, dosage_form, manufacturer, category, DB_NAME)
            added += 1

        self.bulk_result_label.config(
            text=f"{added} brand(s) added/updated" + (f", {skipped} blank line(s) skipped" if skipped else "")
        )
        if added:
            self.bulk_text.delete("1.0", tk.END)
            self.load_list()
