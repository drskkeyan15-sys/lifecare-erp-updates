import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter import messagebox
import sqlite3
from threading import Thread

from app_paths import DB_NAME, app_path, APP_VERSION
from icon_loader import get_icon
import ui_style
import theme
import session
import auth_utils
import ui_popups


def _prewarm_matplotlib():
    """Loads matplotlib's TkAgg backend in a background thread while the
    Login screen is on screen, so it's already in memory by the time
    Dashboard's Sales Trend chart needs it right after login (see
    dashboard.py's _ensure_matplotlib_import()/Dashboard.__init__ comments
    for why that first import used to make the Dashboard look "stuck").
    Safe to run off the main/Tk thread: this only imports the module and
    registers the backend - it does not create or touch any Tk widget,
    which is the part that must stay on the main thread. If matplotlib
    isn't installed, or anything else goes wrong, this silently does
    nothing - dashboard.py's own _ensure_matplotlib_import() already
    handles "matplotlib missing" as a normal, non-fatal case."""
    try:
        import matplotlib
        matplotlib.use("TkAgg")
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: F401
        from matplotlib.figure import Figure  # noqa: F401
    except Exception:
        pass


class LoginWindow:

    def __init__(self, root):
        self.root = root
        self.root.title("Life Care Pharmacy ERP")
        # BUG FIX (2026-08-27, user report): this used to be a plain
        # geometry("460x460") with no x/y offset at all, which just
        # resized the window wherever the OS happened to have placed it
        # (typically the top-left corner) - unlike the post-login loading
        # splash (splash_screen.py), which always computed a real
        # screen-centered x/y. ui_style.center_window() (built for this
        # fix, per the user's own "reusable center_window() utility"
        # recommendation) now centers Login on the screen the same way.
        ui_style.center_window(self.root, 460, 460)
        self.root.resizable(False, False)
        self.root.configure(bg="white")

        self._password_visible = False

        # ─── Brand header band + logo badge ───
        # Matches the same brand blue used for every screen's title bar
        # and Dashboard's own header - this was the one screen in the app
        # still on plain black-on-white text before this redesign.
        # Colors sourced from theme.py (single source of truth) instead
        # of hardcoded hex literals.
        header = tk.Frame(root, bg=theme.PRIMARY, height=150)
        header.pack(fill="x")
        header.pack_propagate(False)

        badge = tk.Canvas(header, width=64, height=64, bg=theme.PRIMARY, highlightthickness=0)
        badge.pack(pady=(22, 8))
        badge.create_oval(2, 2, 62, 62, fill="white", outline="white")
        logo_img = get_icon("logo_mark")
        if logo_img is not None:
            badge.create_image(32, 32, image=logo_img)
        else:
            # Falls back to text initials if icons/logo_mark.png is ever
            # missing (e.g. a dev checkout without the icons/ folder) -
            # matches icon_loader.get_icon()'s own "never let a missing
            # icon break the screen" contract.
            badge.create_text(32, 32, text="LC", font=("Segoe UI", 17, "bold"), fill=theme.PRIMARY)

        self._set_window_icon()

        tk.Label(
            header, text="Life Care Pharmacy ERP", bg=theme.PRIMARY, fg="white",
            font=("Segoe UI", 15, "bold")
        ).pack()
        tk.Label(
            header, text="Billing and inventory management", bg=theme.PRIMARY, fg=theme.PRIMARY_SUBTLE,
            font=("Segoe UI", 9)
        ).pack(pady=(2, 0))

        # ─── Form body ───
        body = tk.Frame(root, bg="white")
        body.pack(fill="both", expand=True)

        form = tk.Frame(body, bg="white")
        form.pack(pady=(26, 8))

        self.user, _ = self._build_field(form, "Username", "user")
        self.user.focus_set()

        self.password, pw_toggle = self._build_field(form, "Password", "lock", is_password=True)
        self._pw_toggle_lbl = pw_toggle
        pw_toggle.bind("<Button-1>", lambda e: self._toggle_password_visibility())

        self.user.bind("<Return>", lambda e: self.password.focus_set())
        self.password.bind("<Return>", lambda e: self.login())

        login_btn = tk.Button(
            body,
            text="LOGIN",
            bg=theme.PRIMARY,
            fg="white",
            activebackground=theme.PRIMARY_HOVER,
            activeforeground="white",
            font=("Segoe UI", 11, "bold"),
            relief="flat",
            bd=0,
            cursor="hand2",
            command=self.login,
            pady=10,
        )
        login_btn.pack(fill="x", padx=40, pady=(10, 6))

        # theme.TEXT_MUTED (was a lighter grey, #9AA3B0, that failed WCAG
        # AA contrast at 2.55:1 on white - see theme.py's docstring).
        tk.Label(body, text=f"v{APP_VERSION}", bg="white", fg=theme.TEXT_MUTED, font=("Segoe UI", 8)).pack(pady=(8, 0))

        # Give matplotlib's slow first-import a head start while the user
        # is still looking at/typing into this screen - see
        # _prewarm_matplotlib()'s own docstring above.
        Thread(target=_prewarm_matplotlib, daemon=True).start()

    def _set_window_icon(self):
        """Sets the window/taskbar icon from app_icon.ico (multi-resolution,
        16-256px, generated from the same heart+hand brand mark as the
        login badge). Wrapped in try/except since iconbitmap() can fail
        on Tk builds/platforms that don't support .ico (this app targets
        Windows via PyInstaller, but a dev running it elsewhere should
        never crash on startup just because the icon didn't apply) - a
        missing/failed icon silently falls back to Tk's own default
        feather icon instead of blocking login."""
        try:
            self.root.iconbitmap(app_path("app_icon.ico"))
        except Exception:
            pass

    def _build_field(self, parent, label_text, icon_name, is_password=False):
        """Builds one labeled, icon-prefixed, rounded 'pill' input field -
        a Canvas draws the rounded background (see ui_style.round_rect,
        Tk has no border-radius), with the icon Label and a borderless
        plain tk.Entry placed on top of it via place(), the same
        Canvas-plus-overlaid-widgets pattern already used for
        dashboard.py's KPI cards. Returns (entry_widget, toggle_label_or_None)
        - the caller wires up the show/hide click binding itself, since
        only the Password field needs it."""
        FIELD_W, FIELD_H, RADIUS = 340, 36, 10

        tk.Label(
            parent, text=label_text, bg="white", fg=theme.TEXT_LABEL,
            font=("Segoe UI", 10), anchor="w"
        ).pack(fill="x", pady=(0, 4))

        wrap = tk.Frame(parent, bg="white", width=FIELD_W, height=FIELD_H)
        wrap.pack(pady=(0, 16))
        wrap.pack_propagate(False)

        canvas = tk.Canvas(wrap, width=FIELD_W, height=FIELD_H, bg="white", highlightthickness=0)
        canvas.place(x=0, y=0)
        # theme.BORDER_DEFAULT (was #D0D5DB, 1.40:1 contrast against the
        # field's own background - an unfocused field's outline was
        # nearly invisible, failing WCAG's 3:1 minimum for UI component
        # boundaries; see theme.py's docstring).
        border_id = ui_style.round_rect(
            canvas, 0, 0, FIELD_W, FIELD_H, RADIUS,
            fill=theme.SURFACE_FIELD, outline=theme.BORDER_DEFAULT, width=1
        )

        tk.Label(wrap, image=get_icon(icon_name), bg=theme.SURFACE_FIELD).place(x=10, y=FIELD_H / 2, anchor="w")

        right_reserve = 30 if is_password else 10
        entry = tk.Entry(
            wrap,
            font=("Segoe UI", 11),
            bd=0,
            bg=theme.SURFACE_FIELD,
            fg=theme.TEXT_PRIMARY,
            insertbackground=theme.TEXT_PRIMARY,
            highlightthickness=0,
            show="*" if is_password else "",
        )
        entry.place(x=38, y=FIELD_H / 2, width=FIELD_W - 38 - right_reserve, anchor="w")

        # Focus feedback: since the field has no native highlightthickness
        # ring (that would draw a square border, defeating the rounded
        # look), the canvas border color itself shifts to brand blue on
        # focus instead - the only way to show focus state on a
        # hand-drawn field like this.
        entry.bind("<FocusIn>", lambda e: canvas.itemconfig(border_id, outline=theme.BORDER_FOCUS))
        entry.bind("<FocusOut>", lambda e: canvas.itemconfig(border_id, outline=theme.BORDER_DEFAULT))

        toggle_lbl = None
        if is_password:
            toggle_lbl = tk.Label(wrap, image=get_icon("eye"), bg=theme.SURFACE_FIELD, cursor="hand2")
            toggle_lbl.place(x=FIELD_W - 10, y=FIELD_H / 2, anchor="e")

        return entry, toggle_lbl

    def _toggle_password_visibility(self):
        self._password_visible = not self._password_visible
        self.password.config(show="" if self._password_visible else "*")
        self._pw_toggle_lbl.config(image=get_icon("eye_off" if self._password_visible else "eye"))

    def login(self):
        u = self.user.get().strip()
        p = self.password.get().strip()

        if u == "" or p == "":
            ui_popups.show_warning(self.root, "Warning", "Please enter both Username and Password")
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        # Fetch by username only, then verify the password against the
        # stored hash (auth_utils.verify_password) instead of matching
        # it in the SQL itself - a plaintext WHERE password=? comparison
        # is exactly the vulnerability database.py's startup migration
        # now fixes at rest; this is the matching fix on the read side.
        cur.execute("SELECT password, role FROM users WHERE username=?", (u,))
        row = cur.fetchone()
        con.close()

        if row and not auth_utils.verify_password(p, row[0]):
            row = None
        elif row:
            row = (row[1],)  # keep the rest of login() working with just the role, unchanged

        if row:
            # No more messagebox.showinfo("Success", ...) here - it used
            # to force an extra "click OK to continue" step between
            # Login and the Dashboard opening, which fought directly
            # against the animated Welcome screen added below (an
            # animation that's supposed to feel smooth and automatic
            # shouldn't be interrupted by a blocking popup right before
            # it) - the Welcome screen itself already makes login success
            # obvious, so the popup was pure friction, not information.
            self.root.withdraw()

            username_val = u
            role_val = row[0]
            # Recorded once here, read by session.get_current_user()
            # anywhere an "adjusted by" / "changed by" audit value is
            # needed (Stock Adjustment, Audit Trail) - see session.py's
            # own docstring for why this is a deliberate exception to
            # avoiding global state.
            session.set_current_user(username_val, role_val)

            def launch_dashboard():
                self.root.destroy()
                from dashboard import Dashboard
                main_root = ttk.Window(themename="flatly")
                main_root.state('zoomed')   # <-- இந்த வரி சேருங்க
                try:
                    main_root.iconbitmap(app_path("app_icon.ico"))
                except Exception:
                    pass
                Dashboard(main_root, username_val, role_val)
                main_root.mainloop()

            def show_welcome_screen():
                # Animated "Life Care Pharmacy ERP" welcome screen shown
                # between Login and the Dashboard opening (see
                # splash_screen.py's own docstring for the full design
                # rationale) - runs on self.root's still-active mainloop
                # since self.root is only withdrawn above, not destroyed
                # yet. launch_dashboard() (which does destroy it) only
                # runs once this finishes its fade-out.
                from splash_screen import SplashScreen
                SplashScreen(self.root, on_done=launch_dashboard)

            self.root.after(100, show_welcome_screen)
        else:
            ui_popups.show_error(self.root, "Error", "Invalid Username or Password")
            self.password.delete(0, "end")
            self.password.focus_set()