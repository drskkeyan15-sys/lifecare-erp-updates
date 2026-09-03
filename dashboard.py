import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import inspect
import sqlite3
import time
from datetime import datetime, timedelta
from gst_reports import GSTReports
from user_management import UserManagement
from whatsapp_integration import WhatsAppIntegration
from expiry_return import ExpiryReturn
import license_reminders
from icon_loader import get_icon
import ui_style
import theme as app_theme
from web_dashboard_launcher import open_web_dashboard as _launch_web_dashboard
import idle_lock
import update_check

from app_paths import DB_NAME
import ui_popups

# Lazy matplotlib import (same pattern as bulk_import.py's openpyxl lazy
# import) - the Sales Trend chart below is an enhancement borrowed from the
# read-only Pharmacy_Advanced analytics companion app (D:\Pharmacy_Advanced,
# a Streamlit dashboard that mirrors this screen's card colors). If
# matplotlib isn't installed on this machine, the Dashboard just skips the
# chart instead of crashing the whole app on startup - matplotlib is not
# otherwise a dependency of this ERP.
matplotlib = None
FigureCanvasTkAgg = None
Figure = None
MATPLOTLIB_AVAILABLE = False


def _ensure_matplotlib_import():
    global matplotlib, FigureCanvasTkAgg, Figure, MATPLOTLIB_AVAILABLE
    if MATPLOTLIB_AVAILABLE:
        return True
    try:
        import matplotlib as _matplotlib
        _matplotlib.use("TkAgg")
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg as _FigureCanvasTkAgg
        from matplotlib.figure import Figure as _Figure
        matplotlib = _matplotlib
        FigureCanvasTkAgg = _FigureCanvasTkAgg
        Figure = _Figure
        MATPLOTLIB_AVAILABLE = True
    except Exception:
        MATPLOTLIB_AVAILABLE = False
    return MATPLOTLIB_AVAILABLE


# --------------------------------------------------------------
# Phase 4 - dark mode preference persistence
# --------------------------------------------------------------
# Reuses the existing single-row `settings` table (same one Settings
# screen's shop name/phone/GSTIN live in) rather than inventing a second
# storage mechanism - settings.py already ALTER TABLE ADD COLUMNs onto it
# for new preferences (e.g. show_payment_on_receipt), so this follows the
# same convention. The column is added here too (not only inside
# settings.py) because that screen's migration only runs once someone
# opens Settings - Dashboard needs to read this value at startup even on
# an install where Settings has never been opened yet.

def _ensure_dark_mode_column(db_name=None):
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    try:
        cur.execute("ALTER TABLE settings ADD COLUMN dark_mode_enabled INTEGER DEFAULT 0")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists (or `settings` table doesn't exist yet - handled below)
    finally:
        con.close()


def get_dark_mode_pref(db_name=None):
    db_name = db_name or DB_NAME
    _ensure_dark_mode_column(db_name)
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    try:
        cur.execute("SELECT dark_mode_enabled FROM settings LIMIT 1")
        row = cur.fetchone()
        return bool(row[0]) if row and row[0] is not None else False
    except sqlite3.OperationalError:
        # `settings` table itself doesn't exist yet (brand-new install,
        # database.py hasn't created it) - default to light mode.
        return False
    finally:
        con.close()


def set_dark_mode_pref(enabled, db_name=None):
    """Best-effort persistence. If `settings` has zero rows (shop details
    were never saved once via the Settings screen), this UPDATE affects 0
    rows - the toggle still works for the rest of this session, it just
    won't be remembered on next launch until a settings row exists. A
    missing settings row should never block using the dark-mode toggle."""
    db_name = db_name or DB_NAME
    _ensure_dark_mode_column(db_name)
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    try:
        cur.execute("UPDATE settings SET dark_mode_enabled=?", (1 if enabled else 0,))
        con.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()


class _CardValueProxy:
    """Phase 3 visual polish switched dashboard KPI cards from plain
    tk.Label widgets to hand-drawn rounded-rectangle Canvas cards (Tk has
    no native border-radius, so the rounded look is a
    create_polygon(..., smooth=True) trick - see Dashboard._round_rect).
    refresh_dashboard() below still just calls `.config(text=...)` on
    each of the 6 card handles like it always did; this proxy exists so
    that call site didn't need to change at all - it forwards straight to
    canvas.itemconfig() on the underlying value text item instead of a
    Label. Keeps the refresh logic decoupled from how a card happens to
    be drawn."""

    def __init__(self, canvas, item_id):
        self._canvas = canvas
        self._item_id = item_id

    def config(self, **kwargs):
        if "text" in kwargs:
            self._canvas.itemconfig(self._item_id, text=kwargs["text"])

    # tk widgets are commonly called via .configure() too - alias it so
    # any future call site written that way keeps working unchanged.
    configure = config


class Dashboard:

    # Phase 4 - dark mode. Only the Dashboard's own body area (KPI cards,
    # month-comparison labels, sales chart) switches palette - the header
    # and sidebar are already dark-navy/brand-blue and don't need to
    # change, and the other ~30 screens (Billing, Purchase, Medicine
    # Master, etc.) are intentionally out of scope for this phase (each
    # still has hardcoded light-theme colors of its own; a full app-wide
    # dark mode would mean touching every one of those files, discussed
    # and deferred - see the scope conversation before this phase).
    LIGHT_THEME = {
        "body_bg": "#ecf0f1",
        "shadow": "#c7ccd1",
        "text_primary": "#2C3E50",
        "text_secondary": "#555555",
        "chart_face": "#ffffff",
        "chart_text": "#333333",
        "empty_text": "#777777",
    }
    DARK_THEME = {
        "body_bg": "#121212",
        "shadow": "#000000",
        "text_primary": "#ECEFF1",
        "text_secondary": "#B0BEC5",
        "chart_face": "#1e1e1e",
        "chart_text": "#ECEFF1",
        "empty_text": "#9e9e9e",
    }

    # Role-based permissions (Phase 4, Aug 2026) - Cashier gets exactly
    # what User Management's own "Permissions" column has always claimed
    # ("Billing & Sales Return Only"), now actually enforced instead of
    # being just a text label. Any role other than "Admin" is treated as
    # restricted (see self.is_admin in build_ui()). Kept as ONE class-
    # level set (rather than duplicated in build_ui()'s sidebar filter
    # and here) so the sidebar and open_module()'s own defense-in-depth
    # check below can never drift apart on what "restricted" means.
    # These strings must exactly match the display_name every open_*()
    # method passes to open_module() (which is also each sidebar item's
    # own label in category_groups) - not a module or class name.
    CASHIER_ALLOWED_ITEMS = {"Billing", "Sales Return"}

    def __init__(self, root, user, role):
        self.root = root
        self.user = user
        self.role = role

        # Read before build_ui() so the header's moon/sun toggle icon
        # starts in the correct state instead of always defaulting to
        # light and then flipping right after.
        self.dark_mode = get_dark_mode_pref()

        self.root.title("Life Care Pharmacy ERP")
        self.root.geometry("1400x800")
        self.root.configure(bg=self._theme()["body_bg"])

        self.menu_buttons = []
        self.focus_index = 0

        # Screen cache (Aug 2026, perceived-speed pass 2) - TRIED AND
        # REVERTED SAME DAY. The idea: never rebuild a screen that was
        # already opened once this session - just keep its widget tree
        # alive (pack_forget, not destroyed) and re-show it later. Live
        # testing (screenshots again) showed this triggers the EXACT SAME
        # Windows Tk ghosting bug as the earlier "keep old screen alive
        # behind/beside the new one" attempts in open_module() - a fresh
        # screen built while a hidden-but-alive cached screen is also a
        # child of self.body renders with fields missing/misplaced
        # (confirmed by opening that same screen FIRST, with nothing
        # cached yet, where it rendered perfectly - the corruption only
        # appeared once a hidden sibling was present). So keeping ANY
        # widget alive-but-hidden alongside a new one is unsafe on this
        # Tk/Windows build, whether via place()+lower(), place() outside
        # self.body's rectangle, OR pack_forget() - not just the specific
        # trick tried earlier. Screens are always destroyed and rebuilt on
        # every open again now; see open_module()'s own comment. The
        # perceived-speed win that's kept instead is deferred data loading
        # (medicine_master.py/brand_master_gui.py/purchase.py all load
        # their DB rows a Tk idle tick after their widget tree is built,
        # not before) - the same safe, already-established pattern this
        # file's own Dashboard.__init__ uses for its KPI cards.

        # Throttle state for sidebar mouse-wheel scrolling - see
        # _on_sidebar_mousewheel()'s docstring below for why this exists.
        self._sidebar_wheel_last_ms = 0

        self.build_ui()
        # Flush all pending widget-creation/geometry work before any user
        # interaction is possible - guards against the same 'zoomed'-state-
        # vs-widget-construction timing issue clear_body() also defends
        # against (see its comment), by giving Tk a chance to fully settle
        # right after the window is built instead of only reacting to it
        # after the fact.
        self.root.update_idletasks()
        self.make_cards()
        # refresh_dashboard() is deliberately NOT called directly here -
        # it runs several SELECTs (fine, fast) but then hands off to
        # _draw_sales_chart(), whose FIRST call on a given run does a
        # lazy `import matplotlib` + TkAgg backend registration. On a
        # freshly-installed/PyInstaller-frozen exe that first import can
        # take a couple of seconds (DLL load, sometimes an antivirus
        # on-access scan on first touch of those files). login.py's
        # launch_dashboard() constructs this whole Dashboard object
        # BEFORE calling main_root.mainloop() - so if refresh_dashboard()
        # ran synchronously right here, that matplotlib delay would block
        # the window before Tk ever gets to paint it, which is exactly
        # what showed up as "dashboard opens blank / looks stuck" (Windows
        # shows an unpainted white window, sometimes marked "Not
        # Responding", even though nothing had actually crashed or
        # hung). Scheduling it via after() instead lets build_ui()'s
        # already-fast widget tree (make_cards() above included - it only
        # creates the KPI card widgets with placeholder "0" text, no DB
        # call) get painted the moment mainloop() starts, so the window
        # appears immediately and stays responsive; the KPI numbers and
        # chart then fill in a fraction of a second later once this
        # fires - a normal, expected "data is loading in" feel instead of
        # a frozen launch.
        self.root.after(50, self.refresh_dashboard)
        # Once per app session (not on every "Refresh Dashboard" click,
        # which would make this annoying to dismiss repeatedly) - see
        # license_reminders.py's own docstring for why this is checked
        # automatically rather than needing Settings to be opened.
        self.root.after(300, self.check_license_reminders)
        # Aug 2026 - background Auto Purchase Order draft generator (see
        # auto_po.py's own docstring for the full rationale). Same
        # once-per-session after() pattern as check_license_reminders()
        # above, at a slightly later delay so its popup (if any) doesn't
        # visually collide with a license-reminder popup appearing at
        # the same moment.
        self.root.after(500, self.check_auto_purchase_orders)
        # Idle-Timeout Auto-Lock (Sep 2026) - see idle_lock.py's own
        # docstring for why this exists and how it works. Created last,
        # after the window/sidebar/cards already exist, so its very
        # first bind_all() activity listener has a fully-built app to
        # watch from the first idle tick onward.
        self.idle_lock = idle_lock.IdleLockManager(self.root, self.user)

        # Auto-Update Notify (Sep 2026) - see update_check.py's own
        # docstring. Runs on a background thread and only ever shows a
        # popup if a newer version.txt is found - never downloads or
        # changes anything by itself. A few seconds after the other
        # startup popups so it never visually collides with them.
        self.root.after(3000, lambda: update_check.check_for_update(self.root))

    # -------------------------
    # MAIN UI & LIVE CLOCK
    # -------------------------

    def build_ui(self):
        # Top bar + sidebar colors below are the app's static brand
        # navy/blue chrome - NOT part of LIGHT_THEME/DARK_THEME above
        # (those only drive self.body's KPI cards/charts). Safe,
        # zero-visual-change swap to theme.py, same tier as login.py.
        self.top = tk.Frame(self.root, bg=app_theme.PRIMARY, height=60)
        self.top.pack(fill="x")

        tk.Label(
            self.top,
            text="Life Care Pharmacy ERP",
            fg="white",
            bg=app_theme.PRIMARY,
            font=("Segoe UI", 20, "bold")
        ).pack(side="left", padx=20)

        # ─── Live Clock Addition ───
        self.clock_label = tk.Label(
            self.top,
            fg="white",
            bg=app_theme.PRIMARY,
            font=("Segoe UI", 11, "bold")
        )
        self.clock_label.pack(side="right", padx=15)
        self.update_clock()

        tk.Label(
            self.top,
            text=f"Welcome : {self.user} ({self.role}) |",
            fg="white",
            bg=app_theme.PRIMARY,
            font=("Segoe UI", 11)
        ).pack(side="right", padx=5)

        # ─── Dark Mode Toggle (Phase 4) ───
        # Icon-only button, no width= set - never hits the
        # tk.Button height/width-becomes-pixels-with-image bug that bit
        # the sidebar buttons in Phase 2 (see dashboard_button/header_btn
        # below, both use pady= for that same reason).
        self.theme_toggle_btn = tk.Button(
            self.top,
            image=get_icon("sun" if self.dark_mode else "moon"),
            command=self.toggle_dark_mode,
            bg=app_theme.PRIMARY,
            activebackground=app_theme.PRIMARY_HOVER,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=8,
            pady=4,
            takefocus=False,
        )
        self.theme_toggle_btn.pack(side="right", padx=10)

        # -------------------------
        # Sidebar with Accordion & Sub-menu Logic
        # -------------------------
        self.sidebar_outer = tk.Frame(self.root, bg=app_theme.SIDEBAR_BG, width=250)
        self.sidebar_outer.pack(side="left", fill="y")
        self.sidebar_outer.pack_propagate(False)

        self.side_canvas = tk.Canvas(self.sidebar_outer, bg=app_theme.SIDEBAR_BG, highlightthickness=0, width=250)

        # ttkbootstrap-ல் மவுஸ் கிளிக் மற்றும் ஃபோகஸ் சரியாக வேலை செய்யச் சேர்க்கப்பட்டது
        self.side_canvas.bind("<Enter>", self._on_sidebar_enter)
        self.side_canvas.bind("<Leave>", self._on_sidebar_leave)

        sidebar_scroll = ttk.Scrollbar(self.sidebar_outer, orient="vertical", command=self.side_canvas.yview)
        self.sidebar = tk.Frame(self.side_canvas, bg=app_theme.SIDEBAR_BG)

        self.sidebar.bind(
            "<Configure>",
            lambda e: self.side_canvas.configure(scrollregion=self.side_canvas.bbox("all"))
        )
        self.side_canvas.create_window((0, 0), window=self.sidebar, anchor="nw", width=250)
        self.side_canvas.configure(yscrollcommand=sidebar_scroll.set)

        self.side_canvas.pack(side="left", fill="both", expand=True)
        sidebar_scroll.pack(side="right", fill="y")

        # Mouse Wheel Binding is intentionally NOT bound here (at setup
        # time) - see _on_sidebar_enter/_on_sidebar_leave/
        # _on_sidebar_mousewheel below, which bind/unbind it dynamically
        # instead. bind_all() registers globally for the whole app, not
        # just this canvas; binding the wheel handler with bind_all()
        # HERE (once, forever) meant scrolling the mouse wheel ANYWHERE
        # in the app (Purchase, Bulk Import, any screen) silently
        # scrolled the sidebar in the background. That left it sitting
        # part-scrolled - showing as a blank gap above "Dashboard" - the
        # next time the user looked back at it, even though they never
        # touched the sidebar itself. Binding/unbinding it only while the
        # pointer is actually over the sidebar (Enter/Leave) avoids that.

        # ─── Category Groups & Sub-menus ───
        # Real PNG icons (see generate_icons.py/icon_loader.py) instead
        # of emoji prefixes baked into the label text - drawn ourselves
        # with Pillow rather than relying on a Segoe MDL2/Fluent icon
        # font's private-use codepoints, which silently render as a
        # blank box if the codepoint's wrong and can't be verified
        # without a live Windows Tk session to check against.
        category_groups = [
            ("Inventory", "package", [
                ("Medicine Master", self.open_medicine_master),
                ("Brand Master", self.open_brand_master),
                ("Purchase", self.open_purchase),
                ("Purchase Item Summary", self.open_purchase_item_summary),
                ("Purchase Order", self.open_purchase_order),
                ("Purchase Return", self.open_purchase_return),
                ("Expiry Return", self.open_expiry_return),
                ("Price List", self.open_price_list),
                ("Stock", self.open_stock),
                ("Stock Summary", self.open_stock_summary),
                ("Stock Adjustment", self.open_stock_adjustment),
                ("Smart Alerts", self.open_smart_alerts),
            ]),
            ("Billing & Sales", "money", [
                ("Billing", self.open_billing),
                ("Sales Return", self.open_sales_return),
                ("Customer Ledger", self.open_customer_ledger),
                ("Supplier Ledger", self.open_supplier_ledger),
                ("Daybook", self.open_daybook),
                ("WhatsApp Invoice", self.open_whatsapp),
                ("Refill Reminders", self.open_refill_reminder),
                ("Prescription Archive", self.open_prescription_archive),
            ]),
            ("Contacts", "people", [
                ("Supplier", self.open_supplier),
                ("Customer", self.open_customer),
            ]),
            ("Reports", "chart", [
                ("Reports", self.open_reports),
                ("GST Reports", self.open_gst_reports),
            ]),
            ("Admin", "settings", [
                ("User Roles", self.open_user_management),
                ("Audit Trail", self.open_audit_log),
                ("Settings", self.open_settings),
                ("Factory Reset", self.open_factory_reset),
            ]),
            # Clinic Ledger (Aug 2026) - internal treatment/consultation
            # accounting, separate from Billing & Sales above (a Clinic
            # Visit is medicine USED on a patient during treatment, not a
            # counter sale - see clinic_repository.py's module docstring).
            # "clinic" has no icons/clinic.png yet - get_icon() returns
            # None for a missing file and every sidebar button already
            # tolerates that (text-only), so this is safe to ship before
            # a real icon is drawn via generate_icons.py.
            ("Clinic Ledger", "clinic", [
                ("Clinic Dashboard", self.open_clinic_dashboard),
                ("Patients", self.open_clinic_patients),
                ("New Visit", self.open_clinic_visit),
                ("Clinic Expenses", self.open_clinic_expenses),
                ("Clinic Reports", self.open_clinic_reports),
            ]),
        ]

        # Role-based sidebar permissions (Phase 4, Aug 2026) - deferred
        # since the Clinic Ledger work started, now implemented per the
        # user's explicit choice: Cashier gets EXACTLY what User
        # Management's own "Permissions" column has always claimed
        # ("Billing & Sales Return Only") - previously just a text label
        # in that table with nothing behind it (self.role was stored and
        # shown in the header, but never checked anywhere). Any role
        # other than "Admin" is treated as restricted (safer default -
        # a future/unexpected role string is locked down, not left wide
        # open, unless it's explicitly "Admin"). This filters the sidebar
        # ITEMS only - an entire category with zero surviving items is
        # dropped from category_groups entirely (never shown as an empty
        # expandable header) rather than filtering per-button visibility
        # after the fact.
        #
        # This is a UI-level gate, not a hardened security boundary -
        # matches this app's existing trust model (a single-till desktop
        # app with its own local sqlite file, not a multi-tenant server).
        # It stops a Cashier from finding these screens through normal
        # navigation; it does not encrypt/lock the underlying DB tables.
        self.is_admin = (self.role == "Admin")
        if not self.is_admin:
            category_groups = [
                (cat_name, icon_name, [
                    (text, cmd) for text, cmd in items if text in self.CASHIER_ALLOWED_ITEMS
                ])
                for cat_name, icon_name, items in category_groups
            ]
            category_groups = [g for g in category_groups if g[2]]

        self.dashboard_button = tk.Button(
            self.sidebar,
            text=" Dashboard",
            image=get_icon("home"),
            compound="left",
            command=self.open_dashboard,
            bg=app_theme.PRIMARY,
            fg="white",
            font=("Segoe UI", 11, "bold"),
            relief="flat",
            pady=10,
            anchor="w",
            padx=15,
            cursor="hand2",
            takefocus=True
        )
        self.dashboard_button.pack(fill="x", padx=5, pady=(10, 8))

        # ─── Web Dashboard (Analytics) - opens the separate Streamlit
        # companion app (D:\Pharmacy_Advanced) in the default browser,
        # starting it in the background first if it isn't already
        # running. See web_dashboard_launcher.py for how it locates/
        # launches that app in both dev and installed (frozen exe) mode.
        # Admin-only (Phase 4 role permissions, Aug 2026) - full business
        # analytics falls outside "Billing & Sales Return Only", same as
        # the Reports/Admin sidebar groups above.
        self.web_dashboard_button = tk.Button(
            self.sidebar,
            text=" Web Dashboard",
            image=get_icon("chart"),
            compound="left",
            command=self.open_web_dashboard,
            bg="#16A085",
            fg="white",
            font=("Segoe UI", 11),
            relief="flat",
            pady=10,
            anchor="w",
            padx=15,
            cursor="hand2",
            takefocus=True
        )
        if self.is_admin:
            self.web_dashboard_button.pack(fill="x", padx=5, pady=(0, 8))

        self.category_frames = {}
        self.category_expanded = {}

        for cat_name, icon_name, items in category_groups:
            header_btn = tk.Button(
                self.sidebar,
                text=f"▶  {cat_name}",
                image=get_icon(icon_name),
                compound="left",
                anchor="w",
                bg="#1B2631",
                fg="white",
                font=("Segoe UI", 10, "bold"),
                relief="flat",
                pady=10,
                padx=15,
                cursor="hand2",
                takefocus=True,
                command=lambda c=cat_name: self.toggle_category(c)
            )
            header_btn.pack(fill="x", padx=5, pady=(2, 0))

            child_frame = tk.Frame(self.sidebar, bg=app_theme.SIDEBAR_BG)
            self.category_frames[cat_name] = {"header": header_btn, "frame": child_frame, "items": items}
            self.category_expanded[cat_name] = False

        self.sidebar_buttons = []
        self._refresh_sidebar_keyboard_nav()
        self.dashboard_button.focus_set()

        # ─── Main Body Frame ───
        self.body = tk.Frame(self.root, bg=self._theme()["body_bg"])
        self.body.pack(side="left", fill="both", expand=True)

    def _theme(self):
        return self.DARK_THEME if self.dark_mode else self.LIGHT_THEME

    def toggle_dark_mode(self):
        self.dark_mode = not self.dark_mode
        set_dark_mode_pref(self.dark_mode)
        self.theme_toggle_btn.config(image=get_icon("sun" if self.dark_mode else "moon"))
        self.root.configure(bg=self._theme()["body_bg"])
        # open_dashboard() already does exactly what's needed to re-theme
        # the body: clear_body() + make_cards() + refresh_dashboard(). If
        # the user is looking at a different screen (Billing, Purchase,
        # etc.) when they toggle, this switches them back to Dashboard to
        # show the result immediately rather than applying invisibly
        # behind whatever screen is currently open.
        self.open_dashboard()

    def update_clock(self):
        current_time = time.strftime('%I:%M:%S %p  |  %d-%b-%Y')
        self.clock_label.config(text=current_time)
        self.root.after(1000, self.update_clock)

    def toggle_category(self, cat_name):
        info = self.category_frames[cat_name]
        expanded = self.category_expanded[cat_name]
        if not expanded:
            for other_name, other_expanded in list(self.category_expanded.items()):
                if other_name != cat_name and other_expanded:
                    self._collapse_category(other_name)
            if not info["frame"].winfo_children():
                for text, cmd in info["items"]:
                    b = tk.Button(
                        info["frame"], text=text, command=cmd, 
                        bg="#34495E", fg="white", font=("Segoe UI", 10), 
                        relief="flat", height=2, anchor="w", padx=30, cursor="hand2", takefocus=True
                    )
                    b.pack(fill="x", pady=1)
                    b.bind("<FocusIn>", lambda e, btn=b: btn.config(bg="#1B4F72"))
                    b.bind("<FocusOut>", lambda e, btn=b: btn.config(bg="#34495E"))
                    b.bind("<Return>", lambda e, btn=b: btn.invoke())
            info["frame"].pack(fill="x", after=info["header"])
            info["header"].config(text=f"▼  {cat_name}")
            self.category_expanded[cat_name] = True
        else:
            self._collapse_category(cat_name)
        self._refresh_sidebar_keyboard_nav()

    def _collapse_category(self, cat_name):
        info = self.category_frames[cat_name]
        info["frame"].pack_forget()
        info["header"].config(text=f"▶  {cat_name}")
        self.category_expanded[cat_name] = False

    def _refresh_sidebar_keyboard_nav(self):
        # Role-based permissions (Aug 2026): web_dashboard_button is only
        # PACKED for Admin (see build_ui()) - an unpacked Tk widget can
        # still take keyboard focus and still has its <Return>/command
        # binding live, so leaving it in this list unconditionally would
        # let ArrowDown+Enter reach and invoke it even for a Cashier who
        # can no longer see the button at all. Excluded here to match.
        self.sidebar_buttons = [self.dashboard_button]
        if self.is_admin:
            self.sidebar_buttons.append(self.web_dashboard_button)
        for cat_name, info in self.category_frames.items():
            self.sidebar_buttons.append(info["header"])
            if self.category_expanded[cat_name]:
                self.sidebar_buttons.extend(info["frame"].winfo_children())
        
        def global_navigate(direction):
            current = self.root.focus_get()
            if current in self.sidebar_buttons:
                try:
                    idx = self.sidebar_buttons.index(current)
                    new_idx = max(0, min(idx + direction, len(self.sidebar_buttons) - 1))
                    self.sidebar_buttons[new_idx].focus_set()
                except ValueError:
                    pass
                return "break"

        self.root.bind("<Down>", lambda e: global_navigate(1))
        self.root.bind("<Up>", lambda e: global_navigate(-1))

        for b in self.sidebar_buttons:
            b.bind("<Return>", lambda e, btn=b: btn.invoke())

    def scroll_sidebar(self, direction):
        self.side_canvas.yview_scroll(direction, "units")

    def _on_sidebar_enter(self, event=None):
        self.side_canvas.focus_set()
        # Bind <MouseWheel> globally (bind_all, not bind() on the canvas
        # alone) ONLY while the pointer is actually over the sidebar -
        # bind_all is required because the sidebar is full of Button
        # children, and a wheel event always targets whatever widget is
        # directly under the cursor, not the canvas behind it. Scoping
        # the bind_all to Enter/Leave (instead of registering it once at
        # startup) is what stops it from also silently scrolling the
        # sidebar in the background while the user scrolls some other
        # screen (Purchase, Bulk Import, etc.) - see the second attempt's
        # notes below for the touchpad-specific glitch this also had to
        # solve.
        self.root.bind_all("<MouseWheel>", self._on_sidebar_mousewheel)

    def _on_sidebar_leave(self, event=None):
        self.root.unbind_all("<MouseWheel>")

    # Second attempt (2026-08-27) at wheel/touchpad scrolling for the
    # sidebar - user asked for it again after the first attempt (see old
    # note below, kept for history) was pulled for a real bug: a laptop
    # touchpad's two-finger drag fires dozens of small-delta <MouseWheel>
    # events per second, and naively forwarding every single one straight
    # into yview_scroll() + a redraw left the sidebar visibly "running
    # away" with a stale-pixel trail on Windows - the app was trying to
    # repaint faster than Windows could actually flush each frame.
    #
    # Fix this time: THROTTLE, not just tune the delta math. At most one
    # scroll tick is processed every 40ms (~25/sec) via event.time (a
    # monotonic per-process millisecond clock Tk stamps on every event) -
    # a real wheel notch (sent as isolated, larger-delta events) still
    # feels instant since it's well under the 40ms gap between notches,
    # while a touchpad's flood of tiny-delta events mostly gets dropped
    # instead of queuing up faster than Windows can repaint. Each
    # processed tick still forces update_idletasks() so the visible
    # scroll never lags behind the actual scrollbar position.
    #
    # IMPORTANT: this sandbox has no live Windows Tk/display to test
    # against, so this is a best-effort fix based on the documented
    # cause, not a verified one - please test scrolling with both a real
    # mouse wheel AND a laptop touchpad drag and report back if the old
    # "runs away" glitch still shows up with either one. The ttk.
    # Scrollbar (sidebar_scroll, still wired to self.side_canvas.yview)
    # keeps working as a fallback regardless.
    def _on_sidebar_mousewheel(self, event):
        now = event.time
        if now - self._sidebar_wheel_last_ms < 40:
            return
        self._sidebar_wheel_last_ms = now
        # Windows sends event.delta as a signed multiple of 120 per wheel
        # notch (positive = scroll up/away from the user); dividing by
        # 120 would double-count a touchpad's fractional deltas, so this
        # only cares about the SIGN, not the magnitude - one scroll unit
        # per processed tick, same as clicking the scrollbar's arrows.
        direction = -1 if event.delta > 0 else 1
        self.side_canvas.yview_scroll(direction, "units")
        self.side_canvas.update_idletasks()

    # --- First attempt's original note (kept for history) ---
    # <MouseWheel>/touchpad-drag scrolling of the sidebar was removed
    # entirely (was previously bound in _on_sidebar_enter above) - a
    # laptop touchpad's two-finger drag fired dozens of small-delta
    # <MouseWheel> events per second, and even after tuning the delta
    # math and forcing update_idletasks() per event, the user kept
    # seeing the sidebar visibly "run away" scrolling under the drag
    # with a stale-pixel trail left behind on Windows. Rather than keep
    # chasing that under a repaint model this sandbox can't visually
    # test, wheel/touchpad-driven scrolling of the sidebar is disabled -
    # the ttk.Scrollbar dragged by hand (sidebar_scroll, still wired to
    # self.side_canvas.yview above) is the only way to scroll it now.

    def clear_body(self):
        if not self.body.winfo_exists():
            # Defensive: on some Windows Tk builds, forcing the window
            # into 'zoomed' state (main.py/login.py) while widgets are
            # still being built underneath it can leave an early frame's
            # underlying Tcl window invalidated even though the Python
            # object still looks alive - the very first module click
            # after login then crashes with "bad window path name"
            # instead of the app just working. Recreating the content
            # frame here self-heals that instead of crashing the whole
            # app on the user's first click.
            self.body = tk.Frame(self.root, bg=self._theme()["body_bg"])
            self.body.pack(side="left", fill="both", expand=True)
            return
        for widget in self.body.winfo_children():
            widget.destroy()

    def open_dashboard(self):
        self.clear_body()
        self.make_cards()
        self.refresh_dashboard()
        # Defensive reset in case the sidebar was left scrolled from
        # before the mouse-wheel fix above, or any other stray scroll.
        self.side_canvas.yview_moveto(0)

    def open_module(self, module_name, class_name, display_name, extra_kwargs=None):
        # Role-based permissions (Phase 4, Aug 2026) - defense-in-depth
        # check, not the primary gate (the sidebar simply never shows a
        # button for a restricted screen to a non-Admin - see build_ui()).
        # This exists so a future button/shortcut added elsewhere that
        # forgets about permissions still can't reach a restricted screen
        # through open_module() - every screen this app has (Inventory,
        # Contacts, Reports, Admin, Clinic Ledger, Smart Alerts, etc.)
        # funnels through this one function, so one check here backs up
        # every sidebar button at once instead of needing its own guard.
        if not self.is_admin and display_name not in self.CASHIER_ALLOWED_ITEMS:
            self.clear_body()
            tk.Label(
                self.body, text="Access Restricted", font=("Segoe UI", 18, "bold"), fg=app_theme.STATUS_DANGER
            ).pack(pady=(60, 10))
            tk.Label(
                self.body, text=f'"{display_name}" is only available to Admin accounts.',
                font=("Segoe UI", 11)
            ).pack()
            return
        # FLASH FIX ATTEMPT (Aug 2026) - REVERTED SAME DAY: this was
        # rewritten three times to keep the old screen visible while the
        # new one built off to the side (first hidden behind the old
        # screen via .lower(), then parked fully outside self.body's own
        # rectangle) so there would be no visible blank gap when
        # switching screens. Each version was verified with
        # py_compile and reasoned through carefully, but live testing on
        # the real Windows machine (screenshots caught it directly) showed
        # a WORSE bug each time: Brand Master's fields collapsing on top
        # of each other, then a partial overlap of the old Dashboard
        # cards and the new screen's content baked into the same frame
        # and NOT clearing even after the swap finished - old pixels
        # staying on screen (a "ghosting" artifact). This app already has
        # one other documented case of Windows Tk leaving stale pixels
        # behind after a widget change instead of repainting cleanly (see
        # the sidebar <MouseWheel> scrolling note above, which was
        # disabled for the same reason) - so this looks like a real
        # limitation of this Tk/Windows combination, not something a
        # cleverer container trick can reliably work around.
        #
        # PASS 2 (same day) - TRIED AND REVERTED: caching already-built
        # screens (skip rebuilding, just re-show the same widget tree) was
        # tried here next, to avoid the rebuild cost on repeat opens
        # instead of trying to avoid the blank gap. Live testing (again
        # caught on screenshots) showed keeping ANY screen's widgets alive-
        # but-hidden as a sibling of a freshly-built screen triggers this
        # same Windows Tk ghosting bug - confirmed by opening the same
        # screen FIRST with nothing else cached, where it rendered
        # perfectly. So every open_module() call destroys the old screen
        # and builds the new one from scratch again, same as before this
        # day's investigation - see medicine_master.py/brand_master_gui.py/
        # purchase.py for the part of this investigation that DID stick:
        # deferring each screen's DB-driven data load to a Tk idle tick
        # after its widget tree is built, so the structure appears sooner
        # and rows fill in a moment later.
        self.clear_body()
        container = None
        try:
            mod = __import__(module_name, fromlist=[class_name])
            module_class = getattr(mod, class_name)
            # Give the module its OWN disposable sub-frame inside
            # self.body, never self.body itself. Several modules
            # (Purchase, Customer, Settings) have a "Close" button that
            # calls self.frame.destroy() - if self.body were passed in
            # directly, that destroys Dashboard's one permanent content
            # area outright, leaving every future menu click pointed at a
            # dead Tcl window (blank screen, or the crash clear_body()
            # now recovers from above). A throwaway container absorbs
            # that destroy() harmlessly instead.
            container = tk.Frame(self.body, bg="#ecf0f1")
            container.pack(fill="both", expand=True)
            # Not every module's __init__ accepts on_close yet (only
            # Purchase/Customer/Settings do so far, since only they had a
            # Close button that needed it). Checking the signature first,
            # instead of try/except TypeError around the call, avoids ever
            # constructing the module twice - a second construction could
            # double up on-open side effects like DB writes.
            params = inspect.signature(module_class.__init__).parameters
            kwargs = {}
            if "on_close" in params:
                kwargs["on_close"] = self.open_dashboard
            # Price List's "Add Item" button (2026-08-22) needs to jump
            # to Medicine Master's own Add flow rather than duplicating
            # it - same optional-callback convention as on_close above,
            # so every other module's construction is unaffected.
            if "on_open_medicine_master" in params:
                kwargs["on_open_medicine_master"] = self.open_medicine_master
            # Predictive Inventory hand-off (Aug 2026) - Smart Alerts'
            # "Reorder Predictions" tab needs a way to send suggested
            # items into a freshly-opened Purchase Order screen. Same
            # optional-callback convention as on_close/
            # on_open_medicine_master above: only wired up if the target
            # class's __init__ actually declares this parameter.
            if "on_create_po" in params:
                kwargs["on_create_po"] = self.open_purchase_order_with_items
            if extra_kwargs:
                kwargs.update(extra_kwargs)
            module_class(container, **kwargs)
        except Exception:
            import traceback
            # BUG FIX: container is created and packed with
            # fill="both", expand=True BEFORE the module is constructed
            # (needed so the module's own __init__ has a real, already-
            # mapped parent - see open_smart_alerts()'s comment on why
            # that ordering matters for tksheet-based screens). If
            # construction then raises, that now-empty container is
            # still sitting there claiming the ENTIRE self.body area, so
            # _show_module_error()'s Label/Text (packed into self.body
            # AFTER it) had zero remaining space to actually appear in -
            # every module-load failure rendered as a silent blank
            # screen instead of the intended red error message. Destroy
            # the failed container first so the error has somewhere to go.
            if container is not None:
                try:
                    container.destroy()
                except Exception:
                    pass
            self._show_module_error(display_name, traceback.format_exc())

    def _show_module_error(self, display_name, detail):
        tk.Label(
            self.body,
            text=f"{display_name} failed to load",
            font=("Segoe UI", 18, "bold"),
            fg="#C62828"
        ).pack(pady=(60, 10))

        text = tk.Text(self.body, height=15, width=110, wrap="word")
        text.insert("1.0", detail)
        text.config(state="disabled")
        text.pack(padx=20, pady=10)

        ui_popups.show_error(self.root, 
            f"{display_name} Error",
            detail.strip().splitlines()[-1] if detail.strip() else "Unknown error"
        )

    def open_medicine_master(self):
        self.open_module('medicine_master', 'MedicineMaster', "Medicine Master")

    def open_brand_master(self):
        self.open_module('brand_master_gui', 'BrandMaster', "Brand Master")

    def open_purchase(self):
        self.open_module('purchase', 'Purchase', "Purchase")

    def open_purchase_item_summary(self):
        self.open_module('purchase_item_summary', 'PurchaseItemSummary', "Purchase Item Summary")

    def open_purchase_order(self):
        self.open_module('purchase_order', 'PurchaseOrder', "Purchase Order")

    def open_purchase_order_with_items(self, items):
        """Opens Purchase Order pre-filled with (medicine, qty) items -
        the receiving end of Smart Alerts' "Reorder Predictions" ->
        Create PO hand-off (see stock_alerts_gui.py's on_create_po and
        purchase_order.py's pending_items= parameter)."""
        self.open_module(
            'purchase_order', 'PurchaseOrder', "Purchase Order",
            extra_kwargs={"pending_items": items}
        )

    def open_purchase_return(self):
        self.open_module('purchase_return', 'PurchaseReturn', "Purchase Return")

    def open_billing(self):
        self.open_module('billing', 'Billing', "Billing")

    def open_sales_return(self):
        self.open_module('sales_return', 'SalesReturn', "Sales Return")

    def open_customer_ledger(self):
        self.open_module('customer_ledger', 'CustomerLedger', "Customer Ledger")

    def open_supplier_ledger(self):
        self.open_module('supplier_ledger', 'SupplierLedger', "Supplier Ledger")

    def open_price_list(self):
        self.open_module('price_list', 'PriceList', "Price List")

    def open_stock(self):
        self.open_module('stock', 'Stock', "Stock")

    def open_stock_summary(self):
        self.open_module('stock_summary', 'StockSummary', "Stock Summary")

    def open_stock_adjustment(self):
        self.open_module('stock_adjustment', 'StockAdjustment', "Stock Adjustment")

    def open_smart_alerts(self):
        # Was hand-written separately from open_module() below (probably
        # predates it) - constructed SmartAlertsDashboard(self.body)
        # BEFORE packing it, unlike every other screen which packs its
        # container FIRST via open_module(), then constructs the module
        # inside that already-mapped container. SmartAlertsDashboard's
        # __init__ loads and inserts all its Low Stock/Expiry/Return row
        # data immediately (refresh()), so on this screen specifically
        # that ordering meant every row got inserted into a tksheet grid
        # that had never been through a single real Tk geometry pass yet -
        # the widget was still 1x1/unmapped rather than "invisible but
        # ready like a normal Treeview." That's what caused the header
        # and every row to render as an empty solid block with no visible
        # text once the screen finally appeared, even though the Low
        # Stock/Expired/Expiring Soon summary CARDS (plain tk.Label text,
        # not tksheet) counted correctly the whole time - the data was
        # never missing, only the sheet's rendering of it was broken by
        # being built pre-map. Routing through open_module() like every
        # other screen (container packed before the module is
        # constructed) fixes that at the source instead of patching
        # around it inside stock_alerts_gui.py.
        self.open_module('stock_alerts_gui', 'SmartAlertsDashboard', "Smart Alerts")

    def open_supplier(self):
        self.open_module('supplier', 'Supplier', "Supplier")

    def open_customer(self):
        self.open_module('customer', 'Customer', "Customer")

    def open_reports(self):
        self.open_module('reports', 'Reports', "Reports")

    def open_barcode_print(self):
        self.open_module('barcode_print', 'BarcodePrint', "Barcode Print")

    def open_settings(self):
        self.open_module('settings', 'Settings', "Settings")

    def open_gst_reports(self):
        self.open_module('gst_reports', 'GSTReports', "GST Reports")

    def open_user_management(self):
        self.open_module('user_management', 'UserManagement', "User Management")

    def open_audit_log(self):
        self.open_module('audit_log_gui', 'AuditLogViewer', "Audit Trail")

    def open_factory_reset(self):
        """Admin -> Factory Reset / Clear Testing Data (Aug 2026). A
        modal confirmation + Admin-password dialog, not a full
        open_module() screen - see factory_reset.py's module docstring
        for the actual backup/scope/safety logic this only gates access
        to. Deliberately re-checks an Admin password here regardless of
        who is currently logged in (self.username/self.role), since the
        requirement is "an Admin password", not "whoever is logged in
        right now happens to be an Admin"."""
        import factory_reset

        win = tk.Toplevel(self.root)
        win.title("Factory Reset / Clear Testing Data")
        # NO hardcoded geometry() here - a fixed "480x460" set BEFORE the
        # widgets below existed used to guess the window's needed height
        # (Aug 2026 first cut), and guessed wrong: on the user's real
        # machine (different font metrics/DPI scaling than assumed) the
        # wrapped warning text alone ran past 460px, pushing the Admin
        # Password field and BOTH buttons (including "Cancel") off the
        # bottom of a non-resizable window - completely unreachable, no
        # way to even close the dialog without killing the app. Fixed by
        # building every widget first, then sizing the window to its own
        # real required size (see the update_idletasks()/geometry() call
        # at the end of this method) - correct on any machine's font/DPI
        # settings instead of a guessed constant. resizable() also left
        # True now as a second safety net.
        win.grab_set()
        win.transient(self.root)

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring) - purely cosmetic,
        # the dynamic "size to real content" fix in this method's own
        # docstring above is untouched.
        outer = ui_style.popup_header(win, "Factory Reset", bg=app_theme.STATUS_DANGER, icon="⚠")
        body = tk.Frame(outer, bg=app_theme.SURFACE_WHITE, padx=16, pady=12)
        body.pack(fill="both", expand=True)

        tk.Label(
            body, bg=app_theme.SURFACE_WHITE, justify="left", wraplength=440, font=("Segoe UI", 10),
            text=(
                "This permanently clears every Medicine Master, Inventory, "
                "Purchase, Sales, Customer/Supplier, and Clinic Ledger "
                "record in this database - restoring it to an empty, "
                "fresh-install state.\n\n"
                "A full backup of pharmacy.db is taken automatically "
                "BEFORE anything is cleared, so this can be undone later "
                "by restoring that backup file if needed.\n\n"
                "NOT affected: your login accounts (Users) and Shop "
                "Settings.\n\n"
                "This cannot be undone from inside the app. Enter an "
                "Admin password to continue."
            )
        ).pack(anchor="w")

        tk.Label(
            body, text="Admin Password", bg=app_theme.SURFACE_WHITE, fg=app_theme.TEXT_LABEL,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(16, 4))
        pwd_var = tk.StringVar()
        pwd_entry = tk.Entry(
            body, textvariable=pwd_var, show="*", width=30, font=("Segoe UI", 11),
            bg=app_theme.SURFACE_FIELD, relief="flat", highlightthickness=1,
            highlightbackground=app_theme.BORDER_DEFAULT, highlightcolor=app_theme.BORDER_FOCUS,
        )
        pwd_entry.pack(anchor="w", ipady=3)
        pwd_entry.focus_set()

        status_var = tk.StringVar()
        tk.Label(
            body, bg=app_theme.SURFACE_WHITE, textvariable=status_var, fg=app_theme.STATUS_DANGER, font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(6, 0))

        def do_reset():
            password = pwd_var.get()
            if not password:
                status_var.set("Enter the Admin password.")
                return

            authorized_by = factory_reset.verify_admin_password(password)
            if not authorized_by:
                status_var.set("Incorrect Admin password.")
                pwd_var.set("")
                pwd_entry.focus_set()
                return

            if not ui_popups.show_confirmation(win, 
                "Confirm Factory Reset",
                "This is the last confirmation.\n\n"
                "ALL Medicine Master, Inventory, Sales, Purchase, "
                "Customer/Supplier and Clinic Ledger data will be "
                "permanently cleared (a backup is taken first).\n\n"
                "Continue?"
            ):
                return

            try:
                backup_path = factory_reset.backup_reset_copy()
            except Exception as e:
                ui_popups.show_error(win, 
                    "Backup Failed",
                    f"Could not create a safety backup - reset cancelled, "
                    f"nothing was changed.\n\n{e}"
                )
                return

            try:
                cleared = factory_reset.reset_database(authorized_by)
            except Exception as e:
                ui_popups.show_error(win, 
                    "Reset Failed",
                    f"Something went wrong during the reset. A backup "
                    f"from just before this was saved at:\n{backup_path}\n\n"
                    f"Error: {e}"
                )
                return

            win.destroy()
            ui_popups.show_info(self.root, 
                "Factory Reset Complete",
                f"{len(cleared)} table(s) cleared. The database is now in "
                f"a fresh-install state.\n\n"
                f"Backup saved at:\n{backup_path}\n\n"
                "Please restart the application."
            )

        btns = tk.Frame(body, bg=app_theme.SURFACE_WHITE)
        btns.pack(fill="x", pady=(20, 0))
        ui_style.flat_button(btns, "Cancel", app_theme.ACCENT_NEUTRAL, win.destroy, width=12).pack(side="right", padx=(8, 0))
        ui_style.flat_button(
            btns, "Clear All Testing Data", app_theme.STATUS_DANGER, do_reset, width=22,
        ).pack(side="right")

        pwd_entry.bind("<Return>", lambda e: do_reset())

        # Size the window to what it actually needs, AFTER every widget
        # above has been packed - update_idletasks() forces Tk to lay
        # everything out first so winfo_reqheight() reflects the real
        # rendered height (wrapped text included) instead of 1px. +40
        # gives a little breathing room under the buttons rather than a
        # razor-exact fit. See the comment above this method's first
        # win.grab_set() for why a hardcoded guess broke this dialog.
        win.update_idletasks()
        req_w = max(480, win.winfo_reqwidth())
        req_h = win.winfo_reqheight() + 40
        # Centering math now goes through ui_style.center_window() (built
        # right after this dialog's own fix, per the user's explicit
        # "reusable center_window() utility" request) instead of the
        # inline x/y math this used to hand-roll here.
        ui_style.center_window(win, req_w, req_h, parent=self.root)
        win.minsize(req_w, req_h)

    def open_whatsapp(self):
        self.open_module('whatsapp_integration', 'WhatsAppIntegration', "WhatsApp Integration")

    def open_refill_reminder(self):
        self.open_module('refill_reminder', 'RefillReminder', "Refill Reminders")

    def open_prescription_archive(self):
        self.open_module('prescription_archive', 'PrescriptionArchive', "Prescription Archive")

    def open_daybook(self):
        self.open_module('daybook', 'Daybook', "Daybook")

    def open_expiry_return(self):
        self.open_module('expiry_return', 'ExpiryReturn', "Expiry Return")

    def open_clinic_dashboard(self):
        self.open_module('clinic_dashboard', 'ClinicDashboard', "Clinic Dashboard")

    def open_clinic_patients(self):
        self.open_module('clinic_patients', 'ClinicPatients', "Clinic Patients")

    def open_clinic_visit(self):
        self.open_module('clinic_visit', 'ClinicVisit', "New Clinic Visit")

    def open_clinic_expenses(self):
        self.open_module('clinic_expenses', 'ClinicExpenses', "Clinic Expenses")

    def open_clinic_reports(self):
        self.open_module('clinic_reports', 'ClinicReports', "Clinic Reports")

    def open_web_dashboard(self):
        # Non-blocking - see web_dashboard_launcher.py. Runs on a
        # background thread and opens the default browser once the
        # Streamlit server answers, so this call returns immediately and
        # never freezes the ERP window.
        _launch_web_dashboard(self.root)

    # --------------------------
    # DASHBOARD CARDS
    # --------------------------

    def make_cards(self):
        theme = self._theme()
        self.body.configure(bg=theme["body_bg"])
        self.cards = tk.Frame(self.body, bg=theme["body_bg"])
        self.cards.pack(pady=30)

        self.totalMedicine = self.create_card("Total Medicines", "#3498db", 0, 0)
        self.totalBills = self.create_card("Today's Bills", "#27ae60", 0, 1)
        self.totalSales = self.create_card("Today's Sales", "#9b59b6", 0, 2)
        self.lowStock = self.create_card(
            "Low Stock (click for list)", "#f39c12", 1, 0, command=self.show_low_stock_list
        )
        self.expiry = self.create_card(
            "Expiring Medicines (click for list)", "#e74c3c", 1, 1, command=self.show_expiring_list
        )
        self.users = self.create_card("Users", "#16a085", 1, 2)

        tk.Button(
            self.body,
            text="Refresh Dashboard",
            bg=app_theme.PRIMARY,
            fg="white",
            font=("Segoe UI", 12),
            command=self.refresh_dashboard,
            cursor="hand2"
        ).pack(pady=(20, 10))

        # ─── Month-over-month comparison (borrowed from the
        # Pharmacy_Advanced analytics companion app's Home page) - "This
        # month" is deliberately labelled "so far" since it's a partial
        # month vs last month's complete total, not a like-for-like number. ───
        self.month_compare_frame = tk.Frame(self.body, bg=theme["body_bg"])
        self.month_compare_frame.pack(pady=(0, 15))
        self.month_this_label = tk.Label(
            self.month_compare_frame, text="This month so far: ₹ 0.00",
            font=("Segoe UI", 12, "bold"), bg=theme["body_bg"], fg=theme["text_primary"]
        )
        self.month_this_label.pack(side="left", padx=20)
        self.month_last_label = tk.Label(
            self.month_compare_frame, text="Last month (complete): ₹ 0.00",
            font=("Segoe UI", 12), bg=theme["body_bg"], fg=theme["text_secondary"]
        )
        self.month_last_label.pack(side="left", padx=20)
        self.month_delta_label = tk.Label(
            self.month_compare_frame, text="", font=("Segoe UI", 12, "bold"), bg=theme["body_bg"]
        )
        self.month_delta_label.pack(side="left", padx=20)

        # ─── Sales Trend (last 30 days) - the ERP Dashboard never had a
        # chart before; this mirrors the Pharmacy_Advanced companion app's
        # own added value on top of the same KPI cards. Rebuilt fresh on
        # every refresh (see _draw_sales_chart) rather than kept as one
        # long-lived widget, since a Tk Frame full of prior chart canvases
        # is simplest to just clear and redraw. ───
        self.chart_frame = tk.Frame(self.body, bg=theme["body_bg"])
        self.chart_frame.pack(pady=(0, 20), fill="x", padx=40)

    # Phase 3 - dashboard visual polish helpers
    # --------------------------------------------------------------
    # Rounded-rectangle drawing itself now lives in ui_style.round_rect()
    # (shared with login.py's field pills - see that module's docstring
    # for why plain Tk can't do border-radius natively). This wrapper is
    # kept so every existing self._round_rect(...) call site in this file
    # doesn't need to change.
    @staticmethod
    def _round_rect(canvas, x1, y1, x2, y2, radius=20, **kwargs):
        return ui_style.round_rect(canvas, x1, y1, x2, y2, radius, **kwargs)

    @staticmethod
    def _lighten_color(hex_color, factor=0.18):
        """Used for the card's hover feedback - blends the card's own
        color toward white by `factor` instead of hard-coding one hover
        shade for every card color."""
        hex_color = hex_color.lstrip("#")
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        r = min(255, int(r + (255 - r) * factor))
        g = min(255, int(g + (255 - g) * factor))
        b = min(255, int(b + (255 - b) * factor))
        return f"#{r:02x}{g:02x}{b:02x}"

    def create_card(self, title, color, row, col, command=None):
        CARD_W, CARD_H, RADIUS = 250, 130, 16
        theme = self._theme()
        BG = theme["body_bg"]  # matches self.body's background so the canvas edges are invisible

        outer = tk.Frame(self.cards, bg=BG, width=CARD_W + 8, height=CARD_H + 8)
        outer.grid(row=row, column=col, padx=20, pady=20)
        outer.grid_propagate(False)

        # Drop shadow: a second rounded rect, offset down-right, drawn
        # first (so the real card paints over most of it) - the only way
        # to fake elevation/depth in plain Tk, no real blur available.
        shadow = tk.Canvas(outer, width=CARD_W, height=CARD_H, bg=BG, highlightthickness=0)
        shadow.place(x=6, y=6)
        self._round_rect(shadow, 0, 0, CARD_W, CARD_H, RADIUS, fill=theme["shadow"], outline=theme["shadow"])

        canvas = tk.Canvas(outer, width=CARD_W, height=CARD_H, bg=BG, highlightthickness=0)
        canvas.place(x=0, y=0)
        card_id = self._round_rect(canvas, 0, 0, CARD_W, CARD_H, RADIUS, fill=color, outline=color)

        canvas.create_text(
            CARD_W / 2, 34, text=title, fill="white",
            font=("Segoe UI", 13, "bold"), width=CARD_W - 30, justify="center"
        )
        value_id = canvas.create_text(
            CARD_W / 2, 84, text="0", fill="white",
            font=("Segoe UI", 26, "bold")
        )

        if command is not None:
            canvas.config(cursor="hand2")
            hover_color = self._lighten_color(color)
            canvas.bind("<Button-1>", lambda e: command())
            canvas.bind("<Enter>", lambda e: canvas.itemconfig(card_id, fill=hover_color, outline=hover_color))
            canvas.bind("<Leave>", lambda e: canvas.itemconfig(card_id, fill=color, outline=color))

        return _CardValueProxy(canvas, value_id)

    # ----------------------
    # REFRESH LOGIC
    # ----------------------

    def refresh_dashboard(self):
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM medicine_master")
        totalMedicine = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]

        today = datetime.now().strftime("%Y-%m-%d")

        cur.execute("SELECT COUNT(*) FROM sales WHERE bill_date=?", (today,))
        bills = cur.fetchone()[0]

        cur.execute("SELECT SUM(total) FROM sales WHERE bill_date=?", (today,))
        result = cur.fetchone()[0]
        if result is None:
            result = 0

        cur.execute("SELECT COUNT(*) FROM medicine_master WHERE stock<10")
        low = cur.fetchone()[0]

        cur.execute("SELECT expiry FROM medicine_master WHERE stock > 0 AND expiry <> ''")
        cutoff = (datetime.now() + timedelta(days=90)).replace(day=1)
        exp = 0
        for (expiry_val,) in cur.fetchall():
            try:
                exp_dt = datetime.strptime(expiry_val, "%m/%y").replace(day=1)
                if exp_dt <= cutoff:
                    exp += 1
            except Exception:
                continue

        # This month so far vs last month (complete) - same
        # strftime('%Y-%m', ...) comparison Pharmacy_Advanced's
        # kpi_month_sales/kpi_last_month_sales use, so the numbers agree.
        cur.execute(
            "SELECT COALESCE(SUM(total), 0) FROM sales "
            "WHERE strftime('%Y-%m', bill_date) = strftime('%Y-%m', 'now')"
        )
        this_month_sales = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT COALESCE(SUM(total), 0) FROM sales "
            "WHERE strftime('%Y-%m', bill_date) = strftime('%Y-%m', 'now', '-1 month')"
        )
        last_month_sales = cur.fetchone()[0] or 0

        # Sales Trend (last 30 days) - one row per day with any sales.
        cur.execute(
            "SELECT bill_date, SUM(total) FROM sales "
            "WHERE bill_date >= date('now', '-30 days') "
            "GROUP BY bill_date ORDER BY bill_date"
        )
        daily_sales = cur.fetchall()

        conn.close()

        self.totalMedicine.config(text=str(totalMedicine))
        self.totalBills.config(text=str(bills))
        self.totalSales.config(text="₹ {:.2f}".format(result))
        self.lowStock.config(text=str(low))
        self.expiry.config(text=str(exp))
        self.users.config(text=str(users))

        self.month_this_label.config(text="This month so far: ₹ {:,.2f}".format(this_month_sales))
        self.month_last_label.config(text="Last month (complete): ₹ {:,.2f}".format(last_month_sales))
        if last_month_sales > 0:
            delta_pct = (this_month_sales - last_month_sales) / last_month_sales * 100
            arrow, color = ("▲", "#2E7D32") if delta_pct >= 0 else ("▼", "#C62828")
            self.month_delta_label.config(text=f"{arrow} {delta_pct:+.1f}%", fg=color)
        else:
            self.month_delta_label.config(text="")

        self._draw_sales_chart(daily_sales)

    # --------------------------
    # SALES TREND CHART
    # --------------------------

    def _draw_sales_chart(self, daily_sales):
        """Redraws the 'last 30 days' line chart. daily_sales is a list of
        (bill_date, total) tuples, already summed per day by the caller.
        Silently shows a one-line note instead of a chart if matplotlib
        isn't installed, or if there's no sales data yet - never raises,
        since a chart failure should never take down the whole Dashboard."""
        theme = self._theme()
        for widget in self.chart_frame.winfo_children():
            widget.destroy()

        if not daily_sales:
            tk.Label(
                self.chart_frame, text="No sales recorded yet in the last 30 days.",
                bg=theme["body_bg"], fg=theme["empty_text"], font=("Segoe UI", 10, "italic")
            ).pack(pady=10)
            return

        if not _ensure_matplotlib_import():
            tk.Label(
                self.chart_frame,
                text="Sales Trend chart needs the 'matplotlib' package "
                     "(pip install matplotlib) - showing totals above only.",
                bg=theme["body_bg"], fg=theme["empty_text"], font=("Segoe UI", 10, "italic")
            ).pack(pady=10)
            return

        try:
            # daily_sales' dates are raw "YYYY-MM-DD" (bill_date's real
            # stored format - see this app's own bill_date parsing
            # convention documented in project memory). Displayed here as
            # "27-Aug" instead, matching the header clock's own
            # "27-Aug-2026" style rather than the raw ISO string (Aug
            # 2026, user asked why the chart showed "yyy-mm-dd" dates
            # when the rest of the app shows DD-Mon-YYYY) - year is
            # dropped since every point here already falls within the
            # last 30 days. A date that fails to parse (shouldn't happen
            # given the query above, but never worth crashing the whole
            # Dashboard over a label) falls back to the raw string as-is.
            def _display_date(d):
                try:
                    return datetime.strptime(d, "%Y-%m-%d").strftime("%d-%b")
                except (ValueError, TypeError):
                    return d
            dates = [_display_date(d) for d, _ in daily_sales]
            totals = [float(t or 0) for _, t in daily_sales]

            fig = Figure(figsize=(9, 2.6), dpi=100, facecolor=theme["chart_face"])
            ax = fig.add_subplot(111)
            ax.set_facecolor(theme["chart_face"])
            ax.plot(dates, totals, marker="o", color="#3498db", linewidth=2)
            ax.set_title("Sales Trend - Last 30 Days", fontsize=11, fontweight="bold", color=theme["chart_text"])
            ax.tick_params(axis="x", labelrotation=45, labelsize=7, colors=theme["chart_text"])
            ax.tick_params(axis="y", labelsize=8, colors=theme["chart_text"])
            for spine in ax.spines.values():
                spine.set_color(theme["chart_text"])
            ax.grid(True, alpha=0.3, color=theme["chart_text"])
            fig.tight_layout()

            canvas = FigureCanvasTkAgg(fig, master=self.chart_frame)
            canvas.draw()
            canvas.get_tk_widget().configure(bg=theme["chart_face"], highlightthickness=0)
            canvas.get_tk_widget().pack(fill="x")
        except Exception:
            # Never let a plotting error block the rest of the Dashboard.
            tk.Label(
                self.chart_frame, text="Sales Trend chart could not be drawn.",
                bg=theme["body_bg"], fg="#C62828", font=("Segoe UI", 10, "italic")
            ).pack(pady=10)

    # --------------------------
    # ALERT DRILL-DOWN LISTS (Low Stock / Expiring Medicines)
    # --------------------------

    def show_low_stock_list(self):
        """Same flat 'stock < 10' rule as the Low Stock card above (and
        Pharmacy_Advanced's kpi_low_stock_dashboard_style/load_low_stock_list),
        so the list always matches the card's own count."""
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute(
            "SELECT name, company, stock, reorder_level FROM medicine_master "
            "WHERE stock < 10 ORDER BY stock ASC"
        )
        rows = cur.fetchall()
        conn.close()

        self._show_alert_list_window(
            title="Low Stock Medicines",
            columns=("Medicine", "Company", "Stock", "Reorder Level"),
            rows=rows,
            empty_text="No low-stock medicines right now.",
        )

    def show_expiring_list(self):
        """Same cutoff logic as the Expiring Medicines card above (stock >
        0, parseable MM/YY expiry, at or before today+90 days month-
        truncated) - shows the actual medicines behind that count."""
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute(
            "SELECT name, company, batch, expiry, stock FROM medicine_master "
            "WHERE stock > 0 AND expiry <> ''"
        )
        all_rows = cur.fetchall()
        conn.close()

        cutoff = (datetime.now() + timedelta(days=90)).replace(day=1)
        matching = []
        for name, company, batch, expiry_val, stock in all_rows:
            try:
                exp_dt = datetime.strptime(expiry_val, "%m/%y").replace(day=1)
            except Exception:
                continue
            if exp_dt <= cutoff:
                matching.append((name, company, batch, expiry_val, stock))
        matching.sort(key=lambda r: r[3])

        self._show_alert_list_window(
            title="Expiring Medicines (within ~90 days)",
            columns=("Medicine", "Company", "Batch", "Expiry", "Stock"),
            rows=matching,
            empty_text="No medicines expiring soon.",
        )

    def _show_alert_list_window(self, title, columns, rows, empty_text):
        win = tk.Toplevel(self.root)
        win.title(title)
        ui_style.center_window(win, 700, 450, parent=self.root)
        win.grab_set()
        # Esc key also closes this popup (same as Close/the window's X).
        win.bind("<Escape>", lambda event: win.destroy())
        win.focus_force()

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring).
        outer = ui_style.popup_header(win, title)
        body = tk.Frame(outer, bg=app_theme.SURFACE_WHITE)
        body.pack(fill="both", expand=True)

        if not rows:
            tk.Label(
                body, text=empty_text, bg=app_theme.SURFACE_WHITE, font=("Segoe UI", 11), pady=30,
            ).pack()
            ui_style.flat_button(body, "Close", app_theme.PRIMARY, win.destroy).pack(pady=10)
            return

        table_frame = tk.Frame(body, bg=app_theme.SURFACE_WHITE)
        table_frame.pack(fill="both", expand=True, padx=10, pady=10)

        tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=140, anchor="center")
        for row in rows:
            # clean_row() so a NULL column shows blank instead of the
            # literal text "None" - see ui_style.clean_row().
            tree.insert("", "end", values=ui_style.clean_row(row))

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        ui_style.flat_button(body, "Close", app_theme.PRIMARY, win.destroy).pack(pady=10)

    # --------------------------
    # LICENSE EXPIRY REMINDER
    # --------------------------

    def check_license_reminders(self):
        try:
            expiring = license_reminders.get_expiring_licenses()
        except Exception:
            return  # never let a reminder-check failure disrupt login

        if not expiring:
            return

        win = tk.Toplevel(self.root)
        win.title("License Renewal Reminder")
        win.resizable(False, False)
        win.grab_set()

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring).
        outer = ui_style.popup_header(win, "LICENSE RENEWAL REMINDER", bg=app_theme.STATUS_DANGER, icon="⚠")
        body = tk.Frame(outer, bg=app_theme.SURFACE_WHITE, padx=15, pady=15)
        body.pack(fill="both", expand=True)

        for label, expiry, days_left in expiring:
            if days_left < 0:
                status, color = f"EXPIRED {abs(days_left)} day(s) ago", app_theme.STATUS_DANGER
            elif days_left == 0:
                status, color = "Expires TODAY", app_theme.STATUS_DANGER
            else:
                status, color = f"Expires in {days_left} day(s)", app_theme.STATUS_WARNING
            tk.Label(
                body, text=f"{label} - {expiry}", bg=app_theme.SURFACE_WHITE,
                font=("Segoe UI", 11, "bold"), anchor="w",
            ).pack(fill="x", pady=(6, 0))
            tk.Label(
                body, text=status, bg=app_theme.SURFACE_WHITE, fg=color,
                font=("Segoe UI", 10, "bold"), anchor="w",
            ).pack(fill="x")

        def send_reminder():
            phone = license_reminders.get_shop_phone()
            if not phone:
                ui_popups.show_info(win, 
                    "No Phone Number",
                    "No phone number set in Settings - add one there first, "
                    "then use this button again."
                )
                return
            message = license_reminders.build_reminder_message(expiring)
            try:
                from whatsapp_integration import open_whatsapp_message
                open_whatsapp_message(phone, message)
            except Exception as e:
                ui_popups.show_error(win, "Error", str(e))

        def _close():
            win.grab_release()
            win.destroy()

        btns = tk.Frame(body, bg=app_theme.SURFACE_WHITE)
        btns.pack(fill="x", pady=(12, 0))
        ui_style.flat_button(
            btns, "Send WhatsApp Reminder to Self", app_theme.STATUS_SUCCESS, send_reminder, width=26,
        ).pack(side="left")
        ui_style.flat_button(btns, "Dismiss", app_theme.ACCENT_NEUTRAL, _close).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", _close)
        # Esc key also closes this popup (same as Dismiss/the window's X).
        win.bind("<Escape>", lambda event: _close())
        win.focus_force()

        # No explicit width/height (was a fixed 450x350 guess) - see
        # ui_style.center_window()'s own docstring for why sizing to
        # real packed content, after building it, is safer.
        ui_style.center_window(win, parent=self.root)

    # --------------------------
    # AUTO PURCHASE ORDER (background draft generator)
    # --------------------------

    def check_auto_purchase_orders(self):
        """Runs auto_po.generate_auto_draft_pos() once per app session
        (see self.root.after(500, ...) above) and, only if it actually
        created something, shows a popup summary + an optional WhatsApp
        heads-up to the shop's own phone (Settings) - never to the
        supplier, since every PO it creates is a Draft that still needs
        a pharmacist to review it in Purchase Order before it's marked
        Sent. A failure here (e.g. DB locked) is swallowed, same as
        check_license_reminders() above - this must never disrupt
        startup."""
        try:
            import auto_po
            created = auto_po.generate_auto_draft_pos()
        except Exception:
            return

        if not created:
            return

        win = tk.Toplevel(self.root)
        win.title("Auto Purchase Order - Draft(s) Created")
        win.resizable(False, False)
        win.grab_set()

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring) - orange header,
        # same ACCENT_SUBSTITUTE shade billing.py's own "View
        # Substitutes" button already uses for this "review before
        # acting" flavor.
        outer = ui_style.popup_header(win, "AUTO PURCHASE ORDER - DRAFT(S) CREATED", bg=app_theme.ACCENT_SUBSTITUTE, icon="🛒")
        body = tk.Frame(outer, bg=app_theme.SURFACE_WHITE, padx=15, pady=10)
        body.pack(fill="both", expand=True)

        tk.Label(
            body,
            text="These medicines hit their reorder level. Review and edit\n"
                 "before marking Sent - nothing has gone to any supplier yet.",
            bg=app_theme.SURFACE_WHITE, font=("Segoe UI", 9), fg=app_theme.TEXT_MUTED, justify="left",
        ).pack(fill="x", pady=(0, 6))

        table_area = tk.Frame(body, bg=app_theme.SURFACE_WHITE)
        table_area.pack(fill="both", expand=True)

        canvas = tk.Canvas(table_area, bg=app_theme.SURFACE_WHITE, highlightthickness=0)
        vsb = ttk.Scrollbar(table_area, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=app_theme.SURFACE_WHITE)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for po_no, supplier, items in created:
            tk.Label(
                inner, text=f"{po_no}  ({supplier})", bg=app_theme.SURFACE_WHITE,
                font=("Segoe UI", 10, "bold"), anchor="w",
            ).pack(fill="x", pady=(6, 0))
            for name, qty in items:
                tk.Label(
                    inner, text=f"    • {name} — qty {qty}", bg=app_theme.SURFACE_WHITE, anchor="w",
                ).pack(fill="x")

        def send_whatsapp_alert():
            phone = license_reminders.get_shop_phone()
            if not phone:
                ui_popups.show_info(win, 
                    "No Phone Number",
                    "No phone number set in Settings - add one there first, "
                    "then use this button again."
                )
                return
            lines = ["Life Care Pharmacy - Auto Purchase Order Draft(s)", ""]
            for po_no, supplier, items in created:
                lines.append(f"{po_no} ({supplier}):")
                for name, qty in items:
                    lines.append(f"  - {name} x {qty}")
            lines.append("")
            lines.append("Review in Purchase Order screen before marking Sent.")
            message = "\n".join(lines)
            try:
                from whatsapp_integration import open_whatsapp_message
                open_whatsapp_message(phone, message)
            except Exception as e:
                ui_popups.show_error(win, "Error", str(e))

        def _close():
            win.grab_release()
            win.destroy()

        btns = tk.Frame(body, bg=app_theme.SURFACE_WHITE)
        btns.pack(fill="x", pady=(10, 0))
        ui_style.flat_button(
            btns, "Send WhatsApp Alert to Self", app_theme.STATUS_SUCCESS, send_whatsapp_alert, width=24,
        ).pack(side="left")
        ui_style.flat_button(btns, "Dismiss", app_theme.ACCENT_NEUTRAL, _close).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", _close)
        win.bind("<Escape>", lambda event: _close())
        win.focus_force()

        # Width bumped 480->620 (2026-08-28 user report: the header text
        # "AUTO PURCHASE ORDER - DRAFT(S) CREATED" was getting clipped
        # at 480px - popup_header()'s title Label has no wraplength/
        # truncation, so a window narrower than the title's own natural
        # rendered width just cuts it off at the window's right edge
        # instead of wrapping or shrinking the font). Height stays 420 -
        # the scrollable PO list inside (see the Canvas+Scrollbar above)
        # is what actually needs a cap, not the header.
        ui_style.center_window(win, 620, 420, parent=self.root)