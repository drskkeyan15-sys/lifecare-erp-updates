import code
import random
import time
import os
from tkinter import messagebox
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter import font as tkfont
import sqlite3
from datetime import datetime

from app_paths import DB_NAME as DB
from pricing_utils import get_pack_multiplier
import generic_mapping
import medicine_matcher
import ui_style
import theme
import audit_log
from tkinter import ttk
import ui_popups

# Dosage Form options - Tablet/Capsule kept distinct from liquid/topical/
# injectable forms, per the "separate Tablet & Capsule from Ointment/
# Injection/Syrup" requirement for Purchase Entry filtering/browsing.
# "IV Fluid" and "Consumable" (Aug 2026) were added so the Clinic Ledger's
# New Visit screen can filter its medicine search by category - see
# clinic_repository.CLINIC_CATEGORY_TO_DOSAGE_FORMS for the mapping from
# a clinic item category to the Dosage Form value(s) it matches.
#
# "Ampoule"/"Vial"/"Solution"/"Sachet" (Aug 2026 Stock filter round)
# were added on top of the existing list so Stock Management's new
# Dosage Form filter can tell an ampoule apart from a multi-dose vial
# (both used to just be lumped under "Injection") - see
# clinic_repository.CLINIC_CATEGORY_TO_DOSAGE_FORMS, which was updated
# in the same round so its existing "Injection" clinic-category mapping
# still catches medicines now classified as "Ampoule"/"Vial" instead of
# the older plain "Injection" value. Nothing already stored as
# "Injection" needs to change - these are additions, not a rename.
DOSAGE_FORM_OPTIONS = [
    "Tablet", "Capsule", "Syrup", "Injection", "Ampoule", "Vial",
    "IV Fluid", "Solution", "Sachet", "Consumable",
    "Ointment", "Cream", "Lotion", "Drops", "Other",
]

# Category options (Aug 2026 Stock filter round) - a broad "what kind of
# stock item is this" tag, separate from Dosage Form (which is about the
# physical form/pack, not the shelf/section). Lets Stock Management's
# Category filter show "only IV Fluids" / "only Surgicals" etc. without
# guessing from the medicine name. "Tablets & Capsules" covers the
# ordinary oral medicines that make up most of a pharmacy's stock and
# would otherwise have no category at all under this scheme; every other
# value matches the pharmacist's own IV Fluids/Injections/Surgicals
# grouping from the 2026-08-28 stock-filter request (Option B - detailed
# categories, including "Injections (Multi-dose)" kept separate from
# plain "Injections" for the 30ml multi-dose vials).
CATEGORY_OPTIONS = [
    "Tablets & Capsules", "Syrups", "Drops", "IV Fluids",
    "Injections", "Injections (Multi-dose)", "Ointments & Lotions",
    "Consumables", "Powders", "Surgicals", "Other",
]

# Forward-looking pagination (2026-08-28) - NOT a fix for a current
# problem (the real pharmacy.db has a handful of medicines today, and
# real measurements the same day showed this screen opens in well
# under half a second) - added because a pharmacy's real catalog can
# grow into the hundreds or thousands over time, and loading every row
# into tksheet at once would eventually get slow as that happens. 200
# is a comfortable page size: big enough that a normal day's Browse
# (no search text) rarely needs a second page at all, small enough
# that even a very large catalog's FIRST page still renders quickly.
# Search (search_data() below) is NOT paginated - it queries the whole
# table directly by name/company/batch/etc, so it always finds a match
# anywhere in the catalog regardless of how many pages have been
# "Load More"-d in Browse mode.
MEDICINE_MASTER_PAGE_SIZE = 200


class MedicineMaster:

    def __init__(self, parent):
        # TEMPORARY diagnostic (2026-08-27, remove once the real cause of
        # the reported "blank 2-3 seconds, then everything appears at
        # once" delay is found and fixed) - every DB query and import
        # this screen touches measured under 100ms combined against the
        # real pharmacy.db (11 medicines, 1122-row Composition Master),
        # so guessing further isn't useful; this times each __init__
        # step and appends ONE line per screen-open to
        # medicine_master_timing.log (next to pharmacy.db) so the real
        # bottleneck shows up as real numbers instead of more guessing.
        # Wrapped so a logging failure can NEVER block the screen itself.
        _t_start = time.perf_counter()
        _checkpoints = []

        def _mark(step_name):
            _checkpoints.append((step_name, time.perf_counter()))

        self.parent = parent
        # Concept A (Sep 2026): was bg="white" - dashboard.py's self.body
        # and open_module()'s throwaway container frame (the two parents
        # this frame sits inside during the destroy/rebuild screen switch)
        # are both theme.SURFACE_PAGE ("#ecf0f1", a very light grey, not
        # pure white). That mismatch meant every switch into this screen
        # had one extra, real color-flip (grey -> white) baked into the
        # transition on top of the already-investigated destroy/rebuild
        # gap (see dashboard.py's "FLASH FIX ATTEMPT" comment) - matching
        # it removes that one flip. Does not touch open_module()/
        # clear_body() itself, so no risk of the worse bugs those three
        # earlier attempts hit.
        self.frame = tk.Frame(parent, bg=theme.SURFACE_PAGE)
        self.frame.pack(fill="both", expand=True)
        _mark("frame_setup")

        # Parallel to whatever's currently in the sheet - row i's medicine
        # id is self._row_ids[i]. tksheet rows are plain 0-based positions
        # with no per-row identifier of their own (unlike Treeview's
        # iid), so this is what select_record() uses to turn "which row
        # did they click" back into a real database id.
        self._row_ids = []
        # Cached raw DB rows from the last load_data() - search_data()
        # filters this in memory instead of re-querying, matching the
        # original Treeview version's behaviour of filtering rows already
        # on screen rather than hitting the database on every keystroke.
        self._all_rows = []
        # Unpadded, status-computed rows currently on screen - see
        # _render_medicine_rows()'s comment. Used by export/print.
        self._current_display_rows = []

        # 2026-08-31 real bug fix ("stuck typing" in Generic): same
        # "cache once, filter in memory" pattern as self._all_rows above
        # - _load_composition_list() used to be called FRESH (a real
        # sqlite3 connect + full-table SELECT) on every single KeyRelease
        # in the Generic field, on top of refresh_composition_info()'s
        # OWN three separate DB round trips also firing on every
        # keystroke (see that method and _filter_composition_dropdown()
        # below). None of that touched focus or the cursor - the
        # widget's own "type interrupted" feel was four-plus blocking
        # sqlite connects per letter typed, worse once 5,000 catalog
        # medicines' generics were seeded into composition_master. None
        # of it needs to re-hit the disk mid-keystroke: the list only
        # actually changes when a brand new composition gets created via
        # _get_or_create_composition_id() (Save/Update), which now keeps
        # this cache updated itself instead of leaving it stale.
        self._composition_names_cache = None
        # Pending refresh_composition_info() delay - see that method.
        self._composition_info_after_id = None
        # 2026-09-02 real bug, caught live via screen-share verification
        # (twice) of two earlier attempted fixes: caching/debounce alone
        # did NOT solve "stuck typing" - characters still got silently
        # dropped with zero DB calls involved and even with a full
        # 1-second gap between keystrokes (never a speed/lag issue).
        # Removing the auto-Post() call entirely did NOT fix it either -
        # this ttk theme apparently opens the Combobox's native popdown
        # just from CLICKING into the box (not only from an explicit
        # Post() call), and that popdown grabs keyboard focus for its own
        # Up/Down/Home/End navigation the moment it's open, so further
        # typed characters never reach the Entry at all.
        #
        # Real fix: stop relying on the native ttk::Combobox popdown for
        # "show matches while typing" entirely. self._generic_entry/
        # _generic_suggest_listbox below are a separate, plain tk.Listbox
        # overlay - the same "search Entry + suggestion Listbox" pattern
        # already proven elsewhere in this app (clinic_visit.py's
        # patient/medicine pickers) - which never takes keyboard focus,
        # so typing is guaranteed to never be interrupted by it.
        # _filter_composition_dropdown() now also defensively force-
        # closes the native popdown on every keystroke as a safety net,
        # in case something else (a mouse click) had opened it.
        self._generic_entry = None
        self._generic_suggest_listbox = None

        self.create_variables()
        _mark("create_variables")
        self.create_form()
        _mark("create_form")
        self.create_table()
        _mark("create_table")
        self.create_footer()
        _mark("create_footer")
        # DEFERRED DATA LOAD (Aug 2026, perceived-speed pass) - load_data()
        # and calculate_profit() are the only real DB-driven work this
        # screen does; everything above (create_variables/form/table/
        # footer) touches no DB at all. Previously load_data() ran
        # synchronously right here, so nothing was visible on screen until
        # BOTH the widget tree AND the first DB query had finished.
        # Scheduling it one Tk idle tick later via after(1, ...) lets Tk
        # paint the already-built (empty) screen structure first - the
        # pharmacist sees "Medicine Master" and the table headers appear
        # immediately, with rows filling in right after - not a "Loading"
        # placeholder, just the real screen appearing sooner and
        # populating itself a moment later. Same pattern already used for
        # Dashboard's own KPI cards (see dashboard.py's
        # Dashboard.__init__, self.root.after(50, self.refresh_dashboard)).
        self.frame.after(1, self._load_initial_data)
        _mark("data_load_scheduled")
        self._bind_shortcuts()
        _mark("_bind_shortcuts")

        self._log_open_timing(_t_start, _checkpoints)

    def _load_initial_data(self):
        self.load_data()
        self.calculate_profit()

    def _dashboard_refresh(self):
        """Called by dashboard.py's screen cache (Aug 2026) when this
        already-built screen is being shown again after the pharmacist
        navigated away and back, instead of being torn down and rebuilt.
        Re-reads the DB so anything changed elsewhere since this screen
        was last visible (Bulk Import, Purchase, Stock Adjustment, etc.)
        shows up here too, rather than a frozen snapshot from first open."""
        self.load_data()
        self.calculate_profit()

    def _log_open_timing(self, t_start, checkpoints):
        """See the TEMPORARY diagnostic note in __init__ above."""
        try:
            log_path = os.path.join(os.path.dirname(DB) or ".", "medicine_master_timing.log")
            prev = t_start
            parts = []
            for name, ts in checkpoints:
                parts.append(f"{name}={ts - prev:.3f}s")
                prev = ts
            total = checkpoints[-1][1] - t_start if checkpoints else 0.0
            line = (
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  "
                f"TOTAL={total:.3f}s  " + "  ".join(parts) + "\n"
            )
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


    def create_variables(self):

        self.name = tk.StringVar()
        self.generic = tk.StringVar()
        self.company = tk.StringVar()
        self.category = tk.StringVar()
        self.hsn = tk.StringVar()
        self.gst = tk.DoubleVar()
        self.batch = tk.StringVar()
        self.expiry = tk.StringVar()
        self.purchase = tk.DoubleVar()
        self.mrp = tk.DoubleVar()
        self.sale = tk.DoubleVar()
        self.profit = tk.DoubleVar(value=0.0)
        self.stock = tk.IntVar()
        self.barcode = tk.StringVar()
        self.rack = tk.StringVar()
        self.search = tk.StringVar()
        self.pack_size = tk.StringVar(value="1")
        self.free_qty = tk.IntVar(value=0)
        self.unit_price = tk.DoubleVar()
        self.reorder_level = tk.IntVar(value=0)
        # Dosage Form (Tablet/Capsule/Syrup/Injection/Ointment/...) -
        # lets Purchase/Stock separate solid oral forms (Tablet, Capsule)
        # from liquids/topicals/injectables at a glance, instead of only
        # the free-text Category field. Auto-filled from Brand Master
        # when purchase.py's offer_create_medicine() recognises the brand
        # name; editable here like any other field otherwise.
        self.dosage_form = tk.StringVar()
        # Cold-Chain / Refrigerator flag - insulin, vaccines, some
        # biologics. Per medicine+batch (not composition-level), since
        # it's about physical storage the pharmacist controls. Feeds
        # Reports' Cold Chain Stock list.
        self.needs_refrigeration = tk.BooleanVar(value=False)
        self.selected_id = None

        # Composition Master detail line (Uses / Action Class / Habit
        # Forming) for whatever's currently in the Generic field - purely
        # informational, refreshed by refresh_composition_info() whenever
        # Generic changes (typing, dropdown pick, or a row select).
        self.composition_info = tk.StringVar(value="")


    def create_form(self):

        # Blue title bar - every other sidebar screen has one
        # (Stock/Purchase/Brand Master/Supplier Ledger etc. all use
        # #1565C0), Medicine Master was the one screen missing it
        # (confirmed via the Aug 2026 UI-consistency audit) and looked
        # inconsistent as a result. Font size 18pt matches the majority
        # of other screens' headers.
        tk.Label(
            self.frame,
            text="MEDICINE MASTER",
            bg=theme.PRIMARY,
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        ).pack(fill="x")

        form = tk.LabelFrame(
            self.frame,
            text="Medicine Master",
            font=("Segoe UI", 11, "bold")
        )

        form.pack(fill="x", padx=10, pady=10)

        self.pending_banner = tk.Label(
            form,
            text="",
            fg="#8A6D00",
            bg="#FFF3CD",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
            padx=8,
            pady=4
        )

        labels = [
            ("Medicine", self.name),
            ("Generic", self.generic),
            ("Company", self.company),
            ("Category", self.category),
            ("Dosage Form", self.dosage_form),
            ("GST", self.gst),
            ("Batch", self.batch),
            ("Expiry", self.expiry),
            ("Purchase", self.purchase),
            ("MRP", self.mrp),
            ("Sale", self.sale),
            ("Profit %", self.profit),
            ("Stock", self.stock),
            ("Pack Size", self.pack_size),
            ("Free Qty", self.free_qty),
            ("Barcode", self.barcode),
            ("Rack", self.rack),
            ("Unit Price", self.unit_price),
            ("Reorder Level", self.reorder_level),
        ]

        row = 1
        col = 0

        purchase_entry = None
        sale_entry = None
        pack_entry = None
        company_entry = None
        gst_entry = None

        for text, var in labels:

            tk.Label(form, text=text).grid(
                row=row,
                column=col,
                padx=5,
                pady=5,
                sticky="w"
            )

            if var == self.generic:
                # ─── Composition Master Dropdown ───
                # Still a ttk.Combobox (dropdown arrow still works for a
                # mouse-only "browse the whole list" click) - but live
                # filtered suggestions while TYPING come from a separate
                # overlay Listbox below, never this widget's own native
                # popdown. See __init__'s comment on _generic_entry/
                # _generic_suggest_listbox for why.
                entry = ttk.Combobox(
                    form,
                    textvariable=var,
                    width=23,
                    values=self._get_composition_names()
                )
                entry.bind("<KeyRelease>", self._filter_composition_dropdown)
                entry.bind("<KeyRelease>", self._schedule_composition_info_refresh, add="+")
                entry.bind("<<ComboboxSelected>>", self.refresh_composition_info)
                entry.bind("<FocusOut>", self.refresh_composition_info)
                entry.bind("<FocusOut>", self._schedule_hide_generic_suggestions, add="+")
                entry.bind("<Return>", self._pick_generic_suggestion, add="+")
                entry.bind("<Escape>", lambda e: self._hide_generic_suggestions(), add="+")

                self._generic_entry = entry
                self._generic_suggest_listbox = tk.Listbox(
                    form, height=6, exportselection=False,
                    font=("Segoe UI", 10), activestyle="dotbox",
                )
                self._generic_suggest_listbox.bind(
                    "<<ListboxSelect>>", self._pick_generic_suggestion
                )
                # Down with the box empty/list not showing yet still
                # needs to conjure up the (unfiltered) list first, before
                # bind_listbox_navigation()'s own <Down> (added right
                # after, so it runs second) has anything to move a
                # highlight through - lets a mouse-averse pharmacist
                # still browse everything via keyboard alone.
                entry.bind("<Down>", self._ensure_generic_suggestions_shown, add="+")
                ui_style.bind_listbox_navigation(entry, self._generic_suggest_listbox)
                # 2026-09-02: the native popdown (arrow click, or a mouse
                # click landing before _filter_composition_dropdown()'s
                # Unpost runs) sizes its own internal listbox to this
                # widget's width=23 characters, clipping longer
                # composition names - see _widen_generic_popdown()'s own
                # comment. postcommand fires right before Tk shows the
                # popdown, so it's always resized just in time.
                entry.configure(postcommand=self._widen_generic_popdown)
            elif var == self.dosage_form:
                entry = ttk.Combobox(
                    form,
                    textvariable=var,
                    width=23,
                    state="readonly",
                    values=DOSAGE_FORM_OPTIONS,
                )
            elif var == self.category:
                # Aug 2026 Stock filter round: Category used to be a
                # plain free-text Entry (almost never actually filled
                # in, hence every existing medicine has category=NULL) -
                # a readonly dropdown, same pattern as Dosage Form right
                # above, so what the pharmacist picks here always
                # matches exactly what Stock Management's Category
                # filter searches for (no typos like "IV Fluid" vs
                # "IV Fluids" silently breaking the filter).
                entry = ttk.Combobox(
                    form,
                    textvariable=var,
                    width=23,
                    state="readonly",
                    values=CATEGORY_OPTIONS,
                )
            else:
                entry = tk.Entry(
                    form,
                    textvariable=var,
                    width=25
                )

            if var == self.profit or var == self.unit_price:
                entry.config(state="readonly")

            entry.grid(
                row=row,
                column=col+1,
                padx=5,
                pady=5
            )

            if var == self.purchase:
                purchase_entry = entry
            elif var == self.sale:
                sale_entry = entry
            elif var == self.gst:
                gst_entry = entry
            elif var == self.pack_size:
                pack_entry = entry
            elif var == self.company:
                company_entry = entry

            col += 2

            if col > 6:
                row += 1
                col = 0

        # Composition info line - full-width row below every field, shows
        # Uses / Action Class / Habit Forming for whatever's in Generic
        # right now (from Composition Master, see refresh_composition_info).
        self.compositionInfoLabel = tk.Label(
            form,
            textvariable=self.composition_info,
            fg="#0D47A1",
            bg="#E3F2FD",
            font=("Segoe UI", 9, "italic"),
            anchor="w",
            padx=8,
            pady=4
        )
        self.compositionInfoLabel.grid(
            row=row + 1, column=0, columnspan=8, sticky="we", padx=5, pady=(0, 5)
        )

        tk.Checkbutton(
            form, text="❄ Needs Refrigeration (Cold Chain - insulin/vaccines)",
            variable=self.needs_refrigeration, font=("Segoe UI", 9, "bold")
        ).grid(row=row + 2, column=0, columnspan=4, sticky="w", padx=5, pady=(0, 5))

        # Concept A (Sep 2026): explicit bg matching self.frame's new
        # theme.SURFACE_PAGE (was unset, i.e. Tk's default grey, which
        # used to sit unnoticed against the old bg="white" frame above
        # it but would show as a visible mismatched rectangle now).
        btn = tk.Frame(self.frame, bg=theme.SURFACE_PAGE)

        if purchase_entry:
            purchase_entry.bind("<KeyRelease>", lambda e: self.calculate_profit())

        if gst_entry:
            gst_entry.bind("<KeyRelease>", lambda e: self.calculate_profit())

        if sale_entry:
            sale_entry.bind(
                "<KeyRelease>",
                lambda e: (
                    self.calculate_profit(),
                    self.calculate_unit_price()
                )
            )

        if company_entry:
            company_entry.bind("<FocusOut>", lambda e: self.auto_rack())

        if pack_entry:
            pack_entry.bind("<KeyRelease>", lambda e: self.calculate_unit_price())

        btn.pack(fill="x", padx=10, pady=10)

        # Concept A (Sep 2026): raw literal colors ("green"/"blue"/"red")
        # replaced with theme.py tokens carrying the same MEANING
        # (STATUS_SUCCESS/PRIMARY/STATUS_DANGER are already used this way
        # elsewhere - Stock/Smart Alerts, tksheet row highlights, etc.),
        # not necessarily the identical hex - flat relief + hover state
        # added to match the rest of the ERP's already-flat button style
        # (sidebar, Load More button below). Pure widget-option changes,
        # no new logic, no change to save()/update()/delete() themselves.
        def _btn(parent, text, bg, hover, command, width):
            b = tk.Button(
                parent, text=text, bg=bg, fg="white", width=width,
                relief="flat", bd=0, cursor="hand2",
                activebackground=hover, activeforeground="white",
                command=command
            )
            b.bind("<Enter>", lambda e, btn=b, h=hover: btn.config(bg=h))
            b.bind("<Leave>", lambda e, btn=b, c=bg: btn.config(bg=c))
            return b

        _btn(btn, "Save", theme.STATUS_SUCCESS, theme.PRIMARY_HOVER, self.save, 12).pack(side="left", padx=5)
        _btn(btn, "Update", theme.PRIMARY, theme.PRIMARY_HOVER, self.update, 12).pack(side="left", padx=5)
        _btn(btn, "Delete", theme.STATUS_DANGER, "#B71C1C", self.delete, 12).pack(side="left", padx=5)
        _btn(btn, "Clear", theme.ACCENT_NEUTRAL, "#78909C", self.clear, 12).pack(side="left", padx=5)

        _btn(btn, "View Info", theme.PRIMARY, theme.PRIMARY_HOVER, self.show_medicine_info_popup, 12).pack(side="left", padx=5)
        _btn(btn, "Check Brands", theme.PRIMARY, theme.PRIMARY_HOVER, self.check_brands_action, 15).pack(side="left", padx=5)
        _btn(btn, "Composition Master", theme.STATUS_SUCCESS, theme.PRIMARY_HOVER, self.open_composition_master, 18).pack(side="left", padx=5)

        tk.Label(
            btn,
            text="Search:",
            bg=theme.SURFACE_PAGE,
            fg=theme.TEXT_LABEL
        ).pack(side="left", padx=(20, 5))

        search = tk.Entry(
            btn,
            textvariable=self.search,
            width=25
        )

        search.pack(side="left")
        # Stored so F3 (see _bind_shortcuts()) can jump focus straight
        # into this box, matching BharatERP's "F3 = Search" shortcut.
        self._search_entry = search

        self.search.trace_add("write", self.search_data)


    def open_composition_master(self):
        try:
            generic_mapping.show_composition_master(self.frame)
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", f"Could not open Composition Master: {e}")

    def check_brands_action(self):
        selected_med = self.name.get().strip()
        if not selected_med:
            ui_popups.show_warning(self.frame, "Warning", "Please select or enter a medicine name first.")
            return
        try:
            generic_mapping.show_brand_checker(self.frame, selected_med)
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", f"Could not check brands: {e}")


    def create_table(self):
        # tksheet, not ttk.Treeview - see the Aug 2026 UI redesign
        # conversation for why: Treeview cannot draw real vertical grid
        # lines between columns (a hard Tk limitation, not a styling
        # gap), which is what a genuine "looks like Excel" table needs.
        # See ui_style.make_excel_sheet() - every API call it makes was
        # checked against the actual installed tksheet 7.6.0 source, not
        # guessed, since this sandbox has no tkinter to test against
        # live before handing it over.
        self._med_cols = (
            "S.No",
            "Medicine",
            "Company",
            "Batch",
            "Expiry",
            "Purchase",
            "MRP",
            "Stock",
            "Status",
        )

        col_widths = {
            "S.No": 55,
            "Medicine": 270,
            "Company": 130,
            "Batch": 110,
            "Expiry": 65,
            "Purchase": 95,
            "MRP": 95,
            "Stock": 85,
            "Status": 125
        }
        # Medicine=270 and Expiry=65 were sized from real measurements, not
        # guessed: get_column_text_width() against the actual longest names
        # in pharmacy.db's medicine_master table (22 chars, e.g. "GENTALAB
        # E/D 10ML 0.3%") needed 195px, and "MM/YY" Expiry text needed 58px;
        # 270/65 add headroom for future, slightly longer names while still
        # being "small" for Expiry/Status per user request (2026-08-22:
        # "medicine column full length, status and expiry maximum 4-5
        # letters so they're small").
        #
        # Status=125 looks bigger than it should for "4-5 letters", but the
        # real Status VALUES this column shows aren't that short - the
        # status logic below produces "Low Stock", "Expired", and "Details
        # Pending" (up to 15 chars), not just "OK". 125 was measured as the
        # smallest width that shows "Details Pending" (the longest one)
        # without cutting it off, tested directly with tksheet's own
        # get_column_text_width() plus a safety margin for the Segoe UI
        # font (CELL_FONT) rendering slightly differently on the real
        # Windows machine than in this sandbox's fallback font. The real
        # win here isn't the raw number - it's that Status is no longer the
        # auto-stretched column (see _stretch_last_column below, retargeted
        # to "Medicine"), so it no longer balloons out to fill the leftover
        # window width like it did before this fix.

        # Grid now takes the FULL width of the screen (matches the Stock
        # Management screen's look, which the user asked this to match) -
        # the side "Selected Medicine Info" panel that used to eat into
        # this width was replaced with a small popup window instead (see
        # show_medicine_info_popup(), opened via the "View Info" button).
        table_body = tk.Frame(self.frame)
        table_body.pack(fill="both", expand=True, padx=10, pady=10)

        # "Load More" bar (2026-08-28, forward-looking pagination - see
        # MEDICINE_MASTER_PAGE_SIZE's own comment for why). Built here
        # but NOT packed yet - _update_load_more_bar() (called from
        # load_data()/search_data()/_load_more_medicines()) packs it
        # only when there's actually another page to load, and
        # pack_forget()s it otherwise, so a normal small catalog (like
        # today's real one) never shows an empty/pointless bar.
        self._load_more_bar = tk.Frame(table_body, bg="#ECEFF1")
        self._load_more_label = tk.Label(
            self._load_more_bar, text="", bg="#ECEFF1", fg="#37474F", font=("Segoe UI", 9)
        )
        self._load_more_label.pack(side="left", padx=10, pady=6)
        self._load_more_btn = tk.Button(
            self._load_more_bar, text="Load More", bg="#1565C0", fg="white",
            activebackground="#0D47A1", activeforeground="white",
            command=self._load_more_medicines
        )
        self._load_more_btn.pack(side="right", padx=10, pady=6)

        grid_frame = tk.Frame(table_body)
        grid_frame.pack(side="left", fill="both", expand=True)

        # 2026-08-30: switched from make_excel_sheet() (tksheet, a
        # Canvas-drawn widget) to make_plain_sheet() (a real
        # ttk.Treeview) at the user's explicit request, after live
        # testing traced the ~0.4s "flash" opening this screen to
        # tksheet's own construction cost - Billing's table (already
        # plain ttk.Treeview) opens instantly on the same machine/data.
        # See ui_style.PlainSheet's docstring for the full trade-off
        # (loses real vertical grid lines + the MIN_VISIBLE_ROWS "pad so
        # it looks full" cosmetic; every other call below - set_sheet_
        # data/highlight_rows/get_currently_selected/column_width/etc -
        # is unchanged, PlainSheet answers to the same method names).
        self.tree = ui_style.make_plain_sheet(
            grid_frame, self._med_cols, col_widths,
            text_columns=("Medicine", "Company", "Batch", "Expiry", "Status"),
            center_columns=("S.No",),
        )
        self.tree.pack(fill="both", expand=True)
        self.tree.enable_bindings(*ui_style.READONLY_BINDINGS)

        # ERP-wide keyboard-nav pass (Aug 2026): pressing Down or Enter
        # while typing a search term now jumps straight into the grid and
        # selects/loads its first result (same as clicking it) - closes
        # this screen's one keyboard gap (see ui_style.bind_search_to_grid()'s
        # docstring). row_count_fn guards against jumping into a blank
        # padding row (pad_for_full_grid()) when a search matches nothing.
        ui_style.bind_search_to_grid(
            self._search_entry, self.tree,
            row_count_fn=lambda: len(self._row_ids),
        )

        # Stretch the "Medicine" column (NOT the last column anymore) to
        # soak up the leftover width now that the grid fills the whole
        # screen (side info panel removed above). Originally this
        # stretched "Status" (the last column) - see brand_master_gui.py's
        # own last-column stretch and the 2026-08-22 gap-fix commit for
        # that history. Retargeted to "Medicine" the same day, because the
        # user asked for Medicine to always show its FULL name with no
        # truncation while Status/Expiry stay small - if the last column
        # (Status) kept absorbing leftover width, it would keep growing
        # wide to fill a maximized window, directly fighting the "Status
        # should be small" request. Medicine is the right stretch target
        # anyway: it's left-aligned text, so extra trailing whitespace
        # from stretching never causes truncation or looks wrong, and it's
        # the one column pharmacists actually want more room for.
        self._med_stretch_col_index = self._med_cols.index("Medicine")
        self._med_last_col_width = None

        def _stretch_last_column(event=None):
            # This is bound to the ROOT window (see below), which stays
            # alive for the whole app session - but this screen's own
            # self.tree gets destroyed the moment the pharmacist
            # navigates to any OTHER menu item (clear_body() in
            # dashboard.py). The root's <Configure> keeps firing after
            # that (any other screen resizing, e.g. opening the Medicine
            # Info popup itself), calling straight into a dead Tcl widget
            # and crashing with "bad window path name" - winfo_exists()
            # is the guard; wrapped in try/except too since a widget can
            # be mid-teardown (exists() still True but an individual Tcl
            # call already failing) in the exact instant it's destroyed.
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
                for c in self._med_cols
                if c != "Medicine"
            )
            # No MAX_STRETCH_COLUMN_WIDTH cap here (2026-08-22 fix) - the
            # cap was meant to stop a numeric column looking oddly wide,
            # but on this screen's actual maximized-window widths it left
            # a real blank strip of plain background to the right of the
            # table, which the user explicitly asked to be removed - the
            # table should fill the window edge-to-edge. Medicine's own
            # configured width (270px, sized off the longest real medicine
            # name in pharmacy.db) is already the floor here, so this only
            # ever grows it further to fill leftover space, never shrinks
            # below what's needed to show the full name.
            new_width = max(
                col_widths["Medicine"] + ui_style.CENTER_PAD_WIDTH,
                widget_width - fixed - ui_style._SCROLLBAR_ALLOWANCE
            )
            if new_width == self._med_last_col_width:
                return
            self._med_last_col_width = new_width
            try:
                self.tree.column_width(column=self._med_stretch_col_index, width=new_width)
            except tk.TclError:
                pass

        # Bound to the ROOT WINDOW's <Configure> (fires only on an actual
        # app-window resize/sidebar toggle), NOT the Sheet widget's own
        # <Configure> - binding directly to self.tree was the cause of
        # the "scroll time slow and struck" report: tksheet's Sheet fires
        # <Configure> on itself during normal scrolling/internal canvas
        # redraws too, not just real resizes, so every scroll tick was
        # re-running column_width() and visibly stuttering the grid. The
        # root window resizes far less often, so this gets the same
        # stretch behaviour without hooking into the scroll path at all.
        # A one-off delayed call right after construction (root Configure
        # doesn't fire on its own just from being packed) does the
        # initial stretch before any resize ever happens.
        self.tree.after(200, _stretch_last_column)
        self.frame.winfo_toplevel().bind("<Configure>", _stretch_last_column, add=True)
        # Whole-row blue highlight on click (matches the old Treeview
        # look - clicking any cell used to highlight the entire row, not
        # just that one cell) - layered on top of, not instead of,
        # select_record() below.
        ui_style.enable_row_highlight_on_select(self.tree)

        # "<<SheetSelect>>" is tksheet's equivalent of Treeview's
        # "<<TreeviewSelect>>" - fires on any cell/row selection change,
        # same callback signature (receives a plain tkinter event), so
        # select_record() below only needed its body changed, not its
        # signature or how it's wired up here.
        # add=True - REQUIRED, not optional: tksheet's own bind() logic
        # (checked in the installed source) replaces the entire handler
        # list for "<<SheetSelect>>" when add isn't passed, which was
        # silently wiping out enable_row_highlight_on_select()'s handler
        # registered just above (it also uses add=True, but that doesn't
        # protect it from being overwritten by a LATER non-additive bind
        # call like this one).
        self.tree.bind("<<SheetSelect>>", self.select_record, add=True)

        conn = sqlite3.connect(DB)
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS medicine_master(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            generic TEXT,
            company TEXT,
            category TEXT,
            hsn TEXT,
            gst REAL,
            batch TEXT,
            expiry TEXT,
            purchase REAL,
            mrp REAL,
            sale REAL,
            stock INTEGER,
            pack_size TEXT DEFAULT '1',
            free_qty INTEGER DEFAULT 0,
            barcode TEXT,
            rack TEXT,
            needs_review INTEGER DEFAULT 0
        )""")

        try:
            cur.execute(
                "ALTER TABLE medicine_master ADD COLUMN needs_review INTEGER DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass

        conn.commit()
        conn.close()

    # ---------------- Footer / keyboard shortcuts / Export / Print ----------------
    # Added 2026-08-22 after the user compared this screen to BharatERP's
    # own "List of Items" screen and asked for its shortcut footer bar,
    # a real Export-to-Excel flow, and quicker row editing. The footer
    # itself is shared (see ui_style.make_shortcut_footer) so every
    # Master screen looks the same; only the callbacks below are
    # specific to Medicine Master's own Save/Update/Delete/Clear/search.

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
        # side="bottom" - docks below the table regardless of the fact
        # that create_table() (packed with fill="both", expand=True)
        # hasn't run yet at the point this is called from __init__; Tk
        # resolves side="bottom" independently of pack() call order.
        footer.pack(side="bottom", fill="x")

    def _bind_shortcuts(self):
        # CTRL+S / ESC / F3 are safe to bind on the whole TOPLEVEL (not
        # just this screen's own frame) because none of them type or
        # delete a character - Tk's bindtags propagate a KeyPress on any
        # descendant widget (a text Entry included) up to a toplevel-
        # level binding, so these fire no matter which field currently
        # has focus, matching how a person actually expects "Ctrl+S
        # saves the form I'm typing in" to work.
        #
        # DEL and ENTER/CTRL+ENTER are the opposite case - they're bound
        # ONLY on self.tree (the grid), never on the toplevel, because
        # Delete/Enter are real text-editing keys elsewhere on this same
        # screen (deleting a character while fixing a typo in the
        # Medicine Name field, or an Entry's own default Enter handling).
        # A toplevel-wide Delete binding would risk deleting the whole
        # selected medicine record while someone is just editing text -
        # scoping it to the grid means it only fires when the grid
        # itself has keyboard focus (which select_record() already sets
        # via self.tree.focus_set() after every row click).
        #
        # add=True + a self.frame.winfo_exists() guard - same pattern as
        # _stretch_last_column() above - because the ROOT window these
        # are bound to outlives this screen (it's destroyed and rebuilt
        # only on navigation, not on every screen switch), so old
        # bindings from a previous visit to this screen must no-op
        # instead of erroring on now-dead widgets.
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

        root.bind("<Control-s>", _guarded(self.update_or_save), add=True)
        root.bind("<Escape>", _guarded(self.clear), add=True)
        root.bind("<F3>", _guarded(lambda: self._search_entry.focus_set()), add=True)

        self.tree.bind("<Delete>", lambda e: self.delete(), add=True)
        self.tree.bind("<Return>", self.select_record, add=True)
        self.tree.bind("<Control-Return>", self._open_quick_edit_popup, add=True)

        # Mouse users don't reach for CTRL+ENTER - a double-click on a
        # row is the gesture people actually try first (and is what was
        # reported missing: clicking a row only loaded it into the
        # bottom form via <<SheetSelect>>/select_record(), which doesn't
        # visibly "open" anything since that form is already on-screen).
        # Bound on self.tree only, same scoping reasoning as Delete/Enter
        # above - never on the toplevel.
        self.tree.bind("<Double-Button-1>", self._open_quick_edit_popup, add=True)

    def update_or_save(self):
        # CTRL+S needs to do the right thing whether a row is currently
        # selected (Update) or the form is blank/being used to add a
        # brand-new medicine (Save) - the Save/Update buttons stay two
        # separate buttons (unchanged) since that distinction is still
        # useful to see, but one keyboard shortcut covering both is what
        # a person actually expects "Ctrl+S = save my work" to do.
        if self.selected_id is not None:
            self.update()
        else:
            self.save()

    def _current_export_rows(self):
        # self._current_display_rows already has the exact Status text
        # shown on screen (Low Stock/Expired/Details Pending/OK) and
        # respects whatever search filter is currently active - export/
        # print should match what the pharmacist is actually looking at,
        # not silently re-dump the full unfiltered catalog.
        return list(self._med_cols), list(self._current_display_rows)

    def export_action(self):
        headers, rows = self._current_export_rows()
        ui_style.export_rows_to_excel(
            self.frame, headers, rows, default_filename="medicine_master"
        )

    def print_action(self):
        headers, rows = self._current_export_rows()
        ui_style.print_rows_as_report(headers, rows, title="Medicine Master", parent=self.frame)

    def _open_quick_edit_popup(self, event=None):
        # BharatERP-style small "quick edit" popup (Ctrl+Enter) for just
        # the numbers a pharmacist adjusts most often day-to-day -
        # Purchase/MRP/Sale/Stock/Reorder Level - without opening the
        # full top form (which also has Generic/Company/Category/HSN/
        # GST/Batch/Expiry/Pack Size/Barcode/Rack, most of which don't
        # change after a medicine's first entry). This is ADDITIONAL to
        # the existing full-form edit flow (select a row -> it loads
        # into the form -> Update), not a replacement for it - so
        # nothing about the existing Save/Update/Delete flow changes for
        # anyone who doesn't use this shortcut.
        current = self.tree.get_currently_selected()
        if not current or current.row is None or current.row >= len(self._row_ids):
            return
        medicine_id = self._row_ids[current.row]

        # Note: Medicine Info is now opened on demand via the "View Info"
        # button (Aug 2026 popup standardization) rather than
        # auto-appearing on every row click, so there's no longer a
        # stray Medicine Info popup left on-screen for a double-click to
        # close here the way the old non-modal version required.

        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute(
            "SELECT name, purchase, mrp, sale, stock, reorder_level FROM medicine_master WHERE id=?",
            (medicine_id,),
        )
        row = cur.fetchone()
        conn.close()
        if row is None:
            return

        name, purchase, mrp, sale, stock, reorder_level = row

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as ui_popups.py's modal dialogs (see that
        # module's own docstring) - this popup was already modal
        # (grab_set() below), so only the look changes here, not the
        # behavior.
        popup = tk.Toplevel(self.frame)
        popup.title(f"Quick Edit - {name}")
        popup.resizable(False, False)
        popup.transient(self.frame.winfo_toplevel())
        popup.grab_set()
        # Esc key also closes this popup (same as Cancel/the window's X).
        popup.bind("<Escape>", lambda event: popup.destroy())
        # Explicit focus so the Esc binding above reliably receives the
        # keypress - grab_set() alone doesn't guarantee this window has
        # real keyboard focus in every window-manager setup.
        popup.focus_force()

        outer = ui_style.popup_header(popup, "Quick Edit", icon="✱")
        body = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=20, pady=16)
        body.pack(fill="both", expand=True)

        tk.Label(
            body, text=name, bg=theme.SURFACE_WHITE, fg=theme.TEXT_PRIMARY,
            font=("Segoe UI", 12, "bold"), wraplength=300, justify="left",
        ).pack(fill="x", pady=(0, 12))

        fields = {}
        form = tk.Frame(body, bg=theme.SURFACE_WHITE)
        form.pack(fill="x")
        for i, (label, value) in enumerate([
            ("Purchase", purchase), ("MRP", mrp), ("Sale", sale),
            ("Stock", stock), ("Reorder Level", reorder_level),
        ]):
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

        def save_quick_edit():
            try:
                new_purchase = float(fields["Purchase"].get())
                new_mrp = float(fields["MRP"].get())
                new_sale = float(fields["Sale"].get())
                new_stock = int(float(fields["Stock"].get()))
                new_reorder = int(float(fields["Reorder Level"].get()))
            except ValueError:
                ui_popups.show_error(popup, "Quick Edit", "Purchase/MRP/Sale/Stock/Reorder Level must be numbers.")
                return

            conn2 = sqlite3.connect(DB)
            cur2 = conn2.cursor()
            cur2.execute(
                "UPDATE medicine_master SET purchase=?, mrp=?, sale=?, stock=?, reorder_level=? WHERE id=?",
                (new_purchase, new_mrp, new_sale, new_stock, new_reorder, medicine_id),
            )
            conn2.commit()
            conn2.close()

            audit_log.log_action(
                "Medicine Master", "Quick Edit",
                f"Updated price/stock for '{name}' (id={medicine_id})"
            )

            popup.destroy()
            self.load_data()

        btn_row = tk.Frame(body, bg=theme.SURFACE_WHITE)
        btn_row.pack(fill="x", pady=(20, 0))
        ui_style.flat_button(
            btn_row, "Cancel", theme.ACCENT_NEUTRAL, popup.destroy,
        ).pack(side="right")
        ui_style.flat_button(
            btn_row, "Save", theme.STATUS_SUCCESS, save_quick_edit,
        ).pack(side="right", padx=(0, 8))

        # Centered AFTER all content is built (no explicit width/height -
        # the old fixed 320x260 guess, set BEFORE packing anything, is
        # exactly the anti-pattern ui_style.center_window()'s own
        # docstring warns about: real content taller than the guess can
        # get pushed off-screen, and it's also the wrong size now that
        # this popup has a header strip). See that docstring for why
        # this must be the LAST thing done here.
        ui_style.center_window(popup, parent=self.frame.winfo_toplevel())

    def _load_composition_list(self):
        import sqlite3
        # FIX (Aug 2026): was a bare relative "pharmacy.db" (resolves
        # against CWD, not the app's real db folder) - same class of bug
        # fixed in purchase.py; see that file's DB_NAME comment. Uses the
        # shared DB (imported as DB_NAME from app_paths above) instead.
        conn = sqlite3.connect(DB)
        names = [r[0] for r in conn.execute(
            "SELECT composition_name FROM composition_master ORDER BY composition_name"
        ).fetchall()]
        conn.close()
        return names

    def _get_composition_names(self, force_refresh=False):
        """Cached wrapper around _load_composition_list() - see this
        screen's __init__ comment on self._composition_names_cache for
        why the Generic field's dropdown/filter must never hit the disk
        per-keystroke. force_refresh=True only when the underlying table
        might genuinely have changed (a new composition just created via
        _get_or_create_composition_id() already appends to the cache
        directly instead of paying for a reload - this is for the rarer
        case of, say, Composition Master being edited in a different
        screen during the same session)."""
        if force_refresh or self._composition_names_cache is None:
            self._composition_names_cache = self._load_composition_list()
        return self._composition_names_cache

    def _filter_composition_dropdown(self, event):
        # Navigation/selection keys are handled by their own bindings
        # (bind_listbox_navigation for Up/Down, _pick_generic_suggestion
        # for Return, _hide_generic_suggestions for Escape) - re-running
        # the filter for them too would be redundant at best.
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return

        widget = event.widget
        typed = self.generic.get().lower()
        all_names = self._get_composition_names()

        # 2026-09-02 REAL BUG, confirmed live via screen-share testing on
        # the pharmacist's own machine (twice, across two earlier fix
        # attempts - see __init__'s comment on _generic_entry for the
        # full story). This ttk theme opens the Combobox's native
        # popdown from a plain click into the box, not only from an
        # explicit Post() call - and that popdown grabs keyboard focus
        # for its own Up/Down/Home/End navigation the instant it's open,
        # silently swallowing every further typed character. Force-
        # closing it on every keystroke, defensively, is what actually
        # guarantees typing is never interrupted - the live filtered
        # suggestions the pharmacist sees instead come from the separate
        # Listbox overlay below, which never takes keyboard focus.
        try:
            widget.tk.call("ttk::combobox::Unpost", widget)
        except tk.TclError:
            pass

        if typed == "":
            widget["values"] = all_names
            self._hide_generic_suggestions()
            return

        matches = [n for n in all_names if typed in n.lower()]
        widget["values"] = matches

        if matches:
            self._show_generic_suggestions(matches)
        else:
            self._hide_generic_suggestions()

    def _ensure_generic_suggestions_shown(self, event=None):
        """<Down> on the Generic entry, bound BEFORE bind_listbox_
        navigation()'s own <Down> (add="+" preserves call order) so this
        runs first: if the overlay isn't showing anything yet (empty box,
        first Down press), populate it with the full/typed-filtered list
        before the navigation binding tries to move a highlight through
        it - lets a pharmacist who prefers the keyboard browse the whole
        Composition Master list without typing anything first, same as
        clicking the dropdown arrow would."""
        lb = self._generic_suggest_listbox
        if lb is not None and not lb.winfo_ismapped():
            typed = self.generic.get().lower()
            all_names = self._get_composition_names()
            matches = [n for n in all_names if typed in n.lower()] if typed else all_names
            if matches:
                self._show_generic_suggestions(matches)

    def _show_generic_suggestions(self, matches):
        lb = self._generic_suggest_listbox
        entry = self._generic_entry
        if lb is None or entry is None:
            return
        lb.delete(0, "end")
        for name in matches:
            lb.insert("end", name)
        # Coordinates are relative to `form` (both widgets' shared
        # parent) - winfo_x()/winfo_y() already report position in that
        # same space, so no manual offset math is needed.
        x = entry.winfo_x()
        y = entry.winfo_y() + entry.winfo_height()
        # 2026-09-02 real complaint ("dropdown-la correct composition
        # kandupudika mudiyala" - pharmacist can't tell entries apart in
        # the dropdown): this used to be a flat 220px, which cuts off
        # combination-drug names ("Metformin 1000mg + Glimepiride 2mg +
        # Voglibose 0.3mg") right at the "+" - exactly the part that
        # tells two similar compositions apart. Measure the widest name
        # actually being shown, in this Listbox's own font, and size the
        # box to fit it (capped so it never runs off the right edge of
        # the form).
        try:
            lb_font = tkfont.Font(font=lb.cget("font"))
            text_width = max((lb_font.measure(name) for name in matches), default=0) + 24
        except tk.TclError:
            text_width = 220
        width = max(entry.winfo_width(), min(text_width, 520))
        lb.place(x=x, y=y, width=width)
        lb.lift()

    def _hide_generic_suggestions(self):
        if self._generic_suggest_listbox is not None:
            self._generic_suggest_listbox.place_forget()

    def _widen_generic_popdown(self):
        """postcommand for the Generic ttk.Combobox - runs right before
        Tk shows the NATIVE popdown (arrow click, or a stray mouse click
        that opens it despite _filter_composition_dropdown()'s Unpost).
        That popdown's internal listbox otherwise sizes itself to this
        widget's own width=23 characters, silently clipping longer
        composition names - especially multi-drug combinations like
        "Metformin 1000mg + Glimepiride 2mg + Voglibose 0.3mg" - right
        at the "+" that actually tells similar entries apart. ttk's own
        PopdownWindow Tcl proc is the only way to reach that internal
        listbox; this only widens it; it does not touch the separate
        typing/overlay behaviour above at all."""
        entry = self._generic_entry
        if entry is None:
            return
        try:
            values = entry["values"]
            max_len = max((len(v) for v in values), default=23)
            width = min(max(max_len + 2, 23), 80)
            popdown = entry.tk.call("ttk::combobox::PopdownWindow", entry)
            entry.tk.call(f"{popdown}.f.l", "configure", "-width", width)
        except tk.TclError:
            pass

    def _schedule_hide_generic_suggestions(self, event=None):
        # Delayed, not immediate: a mouse click landing on the
        # suggestion Listbox blurs this Entry first (like a click on any
        # other widget does) - hiding the list immediately on that
        # FocusOut would make it disappear before its own <<ListboxSelect>>
        # pick handler ever gets to fire. 200ms is plenty for that click
        # to land first, imperceptible for a genuine tab-away.
        self.frame.after(200, self._hide_generic_suggestions)

    def _pick_generic_suggestion(self, event=None):
        lb = self._generic_suggest_listbox
        if lb is None:
            return
        if event is not None and event.keysym in ("Return", "KP_Enter"):
            if not lb.winfo_ismapped() or lb.size() == 0:
                return  # no suggestions showing - let Enter behave normally
            sel = lb.curselection()
            value = lb.get(sel[0] if sel else 0)
        else:
            # <<ListboxSelect>> - an actual mouse click on the list.
            sel = lb.curselection()
            if not sel:
                return
            value = lb.get(sel[0])
        self.generic.set(value)
        self._hide_generic_suggestions()
        self.refresh_composition_info()
        return "break"

    def _get_or_create_composition_id(self, name):
        import sqlite3
        name = (name or "").strip()
        if not name:
            return None
        conn = sqlite3.connect(DB)  # FIX (Aug 2026): see _load_composition_list() above
        cur = conn.cursor()
        existing = cur.execute(
            "SELECT composition_id FROM composition_master WHERE lower(composition_name)=lower(?)", (name,)
        ).fetchone()
        if existing:
            conn.close()
            return existing[0]
        cur.execute("INSERT INTO composition_master (composition_name) VALUES (?)", (name,))
        conn.commit()
        comp_id = cur.lastrowid
        conn.close()
        # Keeps the Generic dropdown's cache (see _get_composition_names())
        # aware of a brand new composition immediately, without paying
        # for a full reload - a fresh Save/Update with a never-seen-before
        # Generic text should be searchable right away, same session.
        if self._composition_names_cache is not None and name not in self._composition_names_cache:
            self._composition_names_cache = sorted(self._composition_names_cache + [name], key=str.lower)
        return comp_id

    def _schedule_composition_info_refresh(self, event=None):
        """Debounced entry point for refresh_composition_info() from the
        Generic field's own KeyRelease - see this screen's __init__
        comment on self._composition_info_after_id. refresh_composition_
        info() itself runs three separate DB lookups (Uses/Action Class/
        Habit Forming); doing that after EVERY keystroke, on top of
        _filter_composition_dropdown()'s own work, was the real cause of
        typing in Generic feeling "stuck" - not a focus/cursor bug, just
        four-plus blocking sqlite connects per letter. Waiting for a
        short pause in typing before actually running the lookup keeps
        the info line just as up to date once the pharmacist stops to
        look at it, without paying the DB cost on every single letter."""
        if self._composition_info_after_id is not None:
            try:
                self.frame.after_cancel(self._composition_info_after_id)
            except (tk.TclError, ValueError):
                pass
        self._composition_info_after_id = self.frame.after(250, self.refresh_composition_info)


    def save(self):

        composition_id = self._get_or_create_composition_id(self.generic.get())

        if self.name.get() == "":
            ui_popups.show_error(self.frame, 
                "Error",
                "Medicine Name Required"
            )
            return

        conn = sqlite3.connect(DB)
        cur = conn.cursor()

        try:
            cur.execute("""
            INSERT INTO medicine_master(
                name, generic, company, category, hsn, gst,
                batch, expiry, purchase, mrp, sale, stock,
                pack_size, free_qty, barcode, rack, composition_id,
                reorder_level, dosage_form, needs_refrigeration
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.name.get(),
                self.generic.get(),
                self.company.get(),
                self.category.get(),
                self.hsn.get(),
                self.gst.get(),
                self.batch.get(),
                self.expiry.get(),
                self.purchase.get(),
                self.mrp.get(),
                self.sale.get(),
                self.stock.get(),
                self.pack_size.get(),
                self.free_qty.get(),
                self.barcode.get(),
                self.rack.get(),
                composition_id,
                self.reorder_level.get(),
                self.dosage_form.get(),
                1 if self.needs_refrigeration.get() else 0
            ))

            conn.commit()
            audit_log.log_action("Medicine Master", "Create", f"Created '{self.name.get()}', batch={self.batch.get()}")
            ui_popups.show_info(self.frame, "Success", "Medicine Saved")

        except Exception as e:
            conn.rollback()
            ui_popups.show_error(self.frame, "Database Error", str(e))

        finally:
            conn.close()
            self.load_data()
            self.clear()


    def calculate_unit_price(self):
        try:
            sale = float(self.sale.get())
            raw_pack = str(self.pack_size.get())
            import re
            pack_nums = re.findall(r'\d+', raw_pack)
            pack = int(pack_nums[0]) if pack_nums else 1
            
            if pack <= 0:
                pack = 1
            self.unit_price.set(round(sale / pack, 2))
        except Exception:
            self.unit_price.set(0)


    def auto_rack(self):
        company = self.company.get().upper()
        if company.startswith("SUN"):
            self.rack.set("A1")
        elif company.startswith("CIPLA"):
            self.rack.set("A2")
        elif company.startswith("MANKIND"):
            self.rack.set("B1")
        elif company.startswith("TORRENT"):
            self.rack.set("B2")
        elif company.startswith("ALKEM"):
            self.rack.set("C1")
        else:
            self.rack.set("GENERAL")


    def load_data(self):
        """Browse mode (no search text) - resets to page 1. See
        MEDICINE_MASTER_PAGE_SIZE's own comment for why this is
        paginated at all."""
        self._page_offset = 0
        self._is_search_mode = False
        self._fetch_medicine_page(reset=True)

    def _fetch_medicine_page(self, reset):
        """Fetches ONE page (MEDICINE_MASTER_PAGE_SIZE rows) starting at
        self._page_offset, appends it to self._all_rows (or replaces,
        on reset=True), then re-renders and updates the Load More bar.
        Shared by load_data() (first page) and _load_more_medicines()
        (every page after)."""
        conn = sqlite3.connect(DB)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM medicine_master")
        self._total_medicine_count = cur.fetchone()[0]

        cur.execute("""
        SELECT
            id, name, company, batch, expiry,
            purchase, mrp, stock, needs_review, reorder_level
        FROM medicine_master
        ORDER BY name
        LIMIT ? OFFSET ?
        """, (MEDICINE_MASTER_PAGE_SIZE, self._page_offset))

        page_rows = cur.fetchall()
        conn.close()

        if reset:
            self._all_rows = page_rows
        else:
            self._all_rows = self._all_rows + page_rows
        self._page_offset += len(page_rows)

        self._render_medicine_rows(self._all_rows)
        self._update_load_more_bar()

    def _load_more_medicines(self):
        self._fetch_medicine_page(reset=False)

    def _update_load_more_bar(self):
        """Shows/hides the Load More bar under the grid - hidden
        whenever every matching row is already on screen (today's real
        catalog size, or once the last page has been loaded), visible
        only when MEDICINE_MASTER_PAGE_SIZE rows aren't enough to cover
        everything yet. Never shown in search mode (see search_data() -
        search always returns its full, already-filtered match set in
        one go, so there's never a "next page" of search results)."""
        if getattr(self, "_is_search_mode", False) or self._page_offset >= self._total_medicine_count:
            self._load_more_bar.pack_forget()
            return
        remaining = self._total_medicine_count - self._page_offset
        self._load_more_label.config(
            text=f"Showing {self._page_offset} of {self._total_medicine_count} medicines"
        )
        self._load_more_btn.config(text=f"Load More ({min(remaining, MEDICINE_MASTER_PAGE_SIZE)} more)")
        self._load_more_bar.pack(side="bottom", fill="x")

    def _render_medicine_rows(self, rows):
        """
        Shared by load_data() and search_data() - builds the sheet data,
        the parallel self._row_ids list (row i's medicine id, since
        tksheet rows are plain positions with no id of their own the
        way a Treeview iid was), and which rows need which highlight,
        then applies everything in one shot. tksheet's alternate_color
        handles the plain zebra striping for every row that isn't
        explicitly highlighted.

        BUG FIX: the "Status" column used to be blank for every row
        except the rare needs_review one - it existed purely as a
        stretch target so the grid's last column would soak up leftover
        screen width instead of leaving a blank header-colored block
        (see create_ui()'s _stretch_last_column comment). That's still
        true, but it meant this whole column read as dead, broken-
        looking empty space on a normal day's catalog. It now shows a
        real per-row health status - same Expired/Low Stock rules
        stock.py's own table already highlights by row color, just
        surfaced here as text too, plus the reorder_level fallback
        Smart Alerts uses (see database.py's migration comment: a
        medicine with no reorder_level set falls back to a flat
        threshold of 10).
        """
        data = []
        self._row_ids = []
        pending_rows = []
        expired_rows = []
        low_rows = []
        today = datetime.today().replace(day=1)

        for index, row in enumerate(rows, start=1):
            pending = bool(row[8])
            stock = int(row[7] or 0)
            expiry = row[4]
            reorder_level = int(row[9]) if len(row) > 9 and row[9] else 0
            threshold = reorder_level if reorder_level > 0 else 10

            is_expired = False
            if expiry:
                try:
                    is_expired = datetime.strptime(expiry, "%m/%y") < today
                except Exception:
                    pass

            row_idx = index - 1
            if pending:
                status_text = "Details Pending"
                pending_rows.append(row_idx)
            elif is_expired:
                status_text = "Expired"
                expired_rows.append(row_idx)
            elif stock <= threshold:
                status_text = "Low Stock"
                low_rows.append(row_idx)
            else:
                status_text = "OK"

            data.append([index, row[1], row[2], row[3], row[4], row[5], row[6], row[7], status_text])
            self._row_ids.append(row[0])

        # Unpadded copy kept for Export/Print (2026-08-22) - those need
        # exactly what's currently on screen (post-search-filter, with
        # the computed Status text), not the blank padding rows added
        # below just to keep the grid's border/zebra look filled out.
        self._current_display_rows = list(data)

        # Padded with blank rows so the grid keeps its border/zebra look
        # all the way down a maximized window even with only a handful
        # of medicines - see ui_style.pad_for_full_grid()'s own
        # docstring (Aug 2026 "screens don't fill the window" pass).
        # self._row_ids is NOT padded, so select_record()'s existing
        # len() guard already treats a click on a padding row as no
        # selection, same as it does for an empty sheet.
        data = ui_style.pad_for_full_grid(data, len(self._med_cols))

        # reset_col_positions=False keeps our custom column widths from
        # resetting to tksheet's 120px default on every refresh.
        # reset_row_positions must stay True - tksheet only draws
        # len(row_positions)-1 rows, not len(data); with it False on a
        # sheet that started empty, rows never become visible even
        # though the data is really there.
        self.tree.set_sheet_data(data, reset_col_positions=False, reset_row_positions=True, reset_highlights=True)
        # Priority order (each row gets exactly one): a still-unreviewed
        # OCR/bulk-import row is the most actionable thing a pharmacist
        # can fix right now, so it's checked first in the loop above and
        # therefore never also lands in expired_rows/low_rows even if it
        # happens to also be expired or low. Painted last here so it
        # visually wins if tksheet's highlight_rows were ever called in
        # a different order in the future.
        if low_rows:
            self.tree.highlight_rows(rows=low_rows, bg=theme.STATUS_WARNING, fg="black")
        if expired_rows:
            self.tree.highlight_rows(rows=expired_rows, bg=theme.STATUS_DANGER, fg="white")
        if pending_rows:
            self.tree.highlight_rows(rows=pending_rows, bg="#FFF3CD", fg="black")

    def search_data(self, *args):
        text = self.search.get().strip()
        if not text:
            # Back to paginated Browse mode - reload from page 1 rather
            # than just re-rendering whatever was cached, since Browse
            # mode's self._all_rows may have been replaced by a search's
            # full (unpaginated) result set above.
            self.load_data()
            return
        # Queries the WHOLE table directly (2026-08-28, see
        # MEDICINE_MASTER_PAGE_SIZE's comment) - NOT filtered from
        # self._all_rows, which in Browse mode only holds whatever pages
        # have been "Load More"-d so far. Searching that in-memory list
        # instead of the database would silently miss any medicine not
        # yet loaded onto screen - a real correctness bug a paginated
        # Browse mode would otherwise introduce. A LIKE match across
        # every column the old in-memory "text in str(row).lower()"
        # check could match keeps search behaving the same as before.
        self._is_search_mode = True
        like = f"%{text}%"
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute("""
        SELECT
            id, name, company, batch, expiry,
            purchase, mrp, stock, needs_review, reorder_level
        FROM medicine_master
        WHERE name LIKE ? OR company LIKE ? OR batch LIKE ? OR expiry LIKE ?
           OR CAST(purchase AS TEXT) LIKE ? OR CAST(mrp AS TEXT) LIKE ?
           OR CAST(stock AS TEXT) LIKE ?
        ORDER BY name
        """, (like, like, like, like, like, like, like))
        filtered = cur.fetchall()
        conn.close()
        self._all_rows = filtered
        self._render_medicine_rows(filtered)
        self._update_load_more_bar()


    def select_record(self, event):
        # tksheet has no per-row iid like Treeview did - get_currently_
        # selected().row is a plain 0-based position, looked up against
        # self._row_ids (built alongside the sheet data in
        # _render_medicine_rows()) to get back the real medicine id.
        current = self.tree.get_currently_selected()
        if not current or current.row is None or current.row >= len(self._row_ids):
            return

        self.selected_id = self._row_ids[current.row]

        conn = sqlite3.connect(DB)
        cur = conn.cursor()

        cur.execute("""
            SELECT
                id, name, generic, company, category, hsn, gst,
                batch, expiry, purchase, mrp, sale, stock,
                pack_size, free_qty, barcode, rack, needs_review,
                reorder_level, dosage_form, needs_refrigeration
            FROM medicine_master
            WHERE id=?
        """, (self.selected_id,))

        row = cur.fetchone()
        conn.close()

        if row is None:
            return

        self.name.set(row[1])
        self.generic.set(row[2])
        self.company.set(row[3])
        self.category.set(row[4])
        self.hsn.set(row[5])
        self.gst.set(row[6] or 0)
        self.batch.set(row[7])
        self.expiry.set(row[8])
        self.purchase.set(row[9] or 0)
        self.mrp.set(row[10] or 0)
        self.sale.set(row[11] or 0)
        self.stock.set(row[12] or 0)
        self.pack_size.set(str(row[13] or "1"))
        self.free_qty.set(row[14] or 0)
        self.barcode.set(row[15])
        self.rack.set(row[16])
        self.reorder_level.set(row[18] or 0)
        self.dosage_form.set(row[19] or "")
        self.needs_refrigeration.set(bool(row[20]))

        if row[17]:
            self.pending_banner.config(
                text=(
                    "⚠ Details Pending - this medicine was auto-created "
                    "from Purchase. Fill in Generic/Company/Category/GST/"
                    "Rack below, then click Update to clear this notice."
                )
            )
            self.pending_banner.grid(
                row=0, column=0, columnspan=8, sticky="we", padx=5, pady=(0, 5)
            )
        else:
            self.pending_banner.grid_remove()

        self.calculate_profit()
        self.calculate_unit_price()
        self.refresh_composition_info()
        # Medicine Info is now a modal popup (Aug 2026 popup
        # standardization - see ui_popups.show_medicine_details()), so it
        # no longer auto-opens/updates on every row click here the way
        # the old non-modal reused-Toplevel version did - that would
        # force a Close click before the next row could be selected,
        # making it impossible to quickly scan down a page of rows.
        # Opened on demand instead via the "View Info" button.
        self.tree.focus_set()


    def update(self):
        if self.selected_id is None:
            return

        composition_id = self._get_or_create_composition_id(self.generic.get())

        conn = sqlite3.connect(DB)
        cur = conn.cursor()

        try:
            cur.execute("""
            UPDATE medicine_master SET
            name=?, generic=?, company=?, category=?, hsn=?, gst=?,
            batch=?, expiry=?, purchase=?, mrp=?, sale=?, stock=?,
            pack_size=?, free_qty=?, barcode=?, rack=?, needs_review=0, composition_id=?,
            reorder_level=?, dosage_form=?, needs_refrigeration=?
            WHERE id=?
            """, (
                self.name.get(),
                self.generic.get(),
                self.company.get(),
                self.category.get(),
                self.hsn.get(),
                float(self.gst.get()),
                self.batch.get(),
                self.expiry.get(),
                float(self.purchase.get()),
                float(self.mrp.get()),
                float(self.sale.get()),
                int(self.stock.get()),
                str(self.pack_size.get()),
                int(self.free_qty.get()),
                self.barcode.get(),
                self.rack.get(),
                composition_id,
                int(self.reorder_level.get() or 0),
                self.dosage_form.get(),
                1 if self.needs_refrigeration.get() else 0,
                self.selected_id
            ))
            conn.commit()
            audit_log.log_action(
                "Medicine Master", "Update",
                f"Updated '{self.name.get()}' (id={self.selected_id}), batch={self.batch.get()}"
            )
            ui_popups.show_info(self.frame, "Success", "Medicine Updated")
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", f"Update Failed: {str(e)}")
        finally:
            conn.close()
            self.load_data()
            self.clear()


    def delete(self):

        if self.selected_id is None:
            return

        if not ui_popups.show_confirmation(self.frame, "Confirm", "Delete selected medicine?"):
            return

        # Captured before the delete for the audit log message - once
        # the row is gone, self.name.get() still holds it (the form
        # isn't cleared until after), but reading it explicitly here
        # keeps this call site correct even if clear() timing ever changes.
        deleted_name = self.name.get()
        deleted_id = self.selected_id

        conn = sqlite3.connect(DB)
        cur = conn.cursor()

        cur.execute(
            "DELETE FROM medicine_master WHERE id=?",
            (self.selected_id,)
        )

        conn.commit()
        conn.close()

        audit_log.log_action("Medicine Master", "Delete", f"Deleted '{deleted_name}' (id={deleted_id})")

        self.load_data()
        self.clear()


    def calculate_profit(self):
        try:
            purchase = float(self.purchase.get())
            gst = float(self.gst.get())
            sale = float(self.sale.get())

            landed_cost = purchase * (1 + (gst / 100))

            if landed_cost > 0:
                profit_amount = sale - landed_cost
                percent = (profit_amount / landed_cost) * 100
                self.profit.set(round(percent, 2))
            else:
                self.profit.set(0)
        except Exception:
            self.profit.set(0)


    def clear(self):

        self.selected_id = None

        for var in (
            self.name,
            self.generic,
            self.company,
            self.category,
            self.hsn,
            self.batch,
            self.expiry,
            self.barcode,
            self.rack
        ):
            var.set("")

        self.gst.set(0)
        self.purchase.set(0)
        self.mrp.set(0)
        self.sale.set(0)
        self.stock.set(0)
        self.pack_size.set("1")
        self.free_qty.set(0)
        self.unit_price.set(0)
        self.profit.set(0)
        self.reorder_level.set(0)
        self.dosage_form.set("")
        self.needs_refrigeration.set(False)

        if hasattr(self.pending_banner, "grid_remove"):
            self.pending_banner.grid_remove()

        self.composition_info.set("")

    def refresh_composition_info(self, event=None):
        """
        Looks up Uses / Action Class / Habit Forming for whatever's
        currently typed/selected in Generic (via generic_mapping.py's
        Composition Master helpers - same data already shown in the
        Substitute Medicine popup) and shows it as a one-line summary
        right under the form. Purely informational - never blocks
        Save/Update, and silently clears itself for an empty or unknown
        generic rather than showing an error.
        """
        generic_text = self.generic.get().strip()
        if not generic_text or generic_text.lower() == "none":
            self.composition_info.set("")
            return

        uses = generic_mapping.get_composition_uses(generic_text)
        action_class = generic_mapping.get_composition_action_class(generic_text)
        habit_forming = generic_mapping.get_composition_habit_forming(generic_text)

        parts = []
        if action_class:
            parts.append(f"Class: {action_class}")
        if uses:
            parts.append(f"Uses: {uses}")

        text = "  |  ".join(parts) if parts else "No composition info on file for this generic yet."
        if habit_forming:
            text = "⚠ HABIT FORMING  |  " + text

        self.composition_info.set(text)

    def show_medicine_info_popup(self):
        """
        "View Info" button handler - shows Stock Value, expiry countdown,
        and Composition Master info (Class/Uses/Habit Forming) for
        whatever medicine is currently loaded in the form. Reads from the
        form fields (already populated by select_record()) - no extra DB
        query needed beyond the Composition Master lookup itself.

        Aug 2026 popup standardization: this used to be a non-modal
        Toplevel that auto-opened/updated on every row click in the grid
        (see git history / ui_popups.py's own docstring on
        show_medicine_details() for that older design). It's now the
        shared modal ui_popups.show_medicine_details() dialog instead,
        opened on demand from this button - a modal popup can't be left
        auto-triggering on every row click without forcing a Close click
        before the next row could be selected, which would make it
        impossible to quickly scan down a page of rows.
        """
        name = self.name.get().strip()
        if not name:
            ui_popups.show_warning(self.frame, "No Medicine Selected", "Select a medicine from the grid first.")
            return

        try:
            pack_mult = get_pack_multiplier(str(self.pack_size.get() or "1")) or 1
        except Exception:
            pack_mult = 1
        purchase = float(self.purchase.get() or 0.0)
        gst = float(self.gst.get() or 0.0)
        stock = int(self.stock.get() or 0)
        reorder_level = int(self.reorder_level.get() or 0)
        unit_price = (purchase + purchase * (gst / 100)) / pack_mult
        stock_value = unit_price * stock

        expiry = self.expiry.get()
        expiry_days_left = None
        try:
            exp_dt = datetime.strptime(expiry, "%m/%y").replace(day=1)
            expiry_days_left = (exp_dt - datetime.now().replace(day=1)).days
        except Exception:
            pass

        # Same Composition Master lookup refresh_composition_info() uses
        # for the inline bar under the form - shown again here so the
        # popup is self-contained (Class/Uses/Habit Forming) without the
        # pharmacist needing to look back at the form underneath it.
        generic_text = self.generic.get().strip()
        uses = action_class = habit_forming = None
        if generic_text and generic_text.lower() != "none":
            uses = generic_mapping.get_composition_uses(generic_text)
            action_class = generic_mapping.get_composition_action_class(generic_text)
            habit_forming = generic_mapping.get_composition_habit_forming(generic_text)

        ui_popups.show_medicine_details(self.frame, {
            "name": name,
            "stock": stock,
            "reorder_level": reorder_level,
            "mrp": self.mrp.get(),
            "purchase": purchase,
            "stock_value": round(stock_value, 2),
            "expiry": expiry,
            "expiry_days_left": expiry_days_left,
            "action_class": action_class,
            "uses": uses,
            "habit_forming": habit_forming,
        })