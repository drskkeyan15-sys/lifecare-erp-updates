"""
ui_popups.py
LifeCare Pharmacy ERP - Standard, Modern-Styled Popup Dialogs

Why this exists (2026-08-27, user's own "give your ideas first" request):
Every screen in this app used to call tkinter.messagebox.showinfo/
showwarning/showerror/askyesno directly - those are the OS's own plain
grey dialog boxes (no control over colour, font, or button styling),
which looked inconsistent with the rest of this app's flat, blue/white,
Segoe UI look (the same "generic Tkinter" gap that led to the Excel-
style tksheet grids and the shared ui_style.py button/label
conventions). This module is the same idea applied to message and
confirmation popups: ONE place defining what an info box / warning box
/ error box / confirmation box / medicine-details box looks like, so
every screen shares the identical look instead of each one hand-rolling
(or not rolling at all) its own.

Drop-in replacements (same call shape, same return value):
    messagebox.showinfo(title, msg)      -> show_info(parent, title, msg)
    messagebox.showwarning(title, msg)   -> show_warning(parent, title, msg)
    messagebox.showerror(title, msg)     -> show_error(parent, title, msg)
    messagebox.askyesno(title, msg)      -> show_confirmation(parent, title, msg)   (same True/False return)

`parent` is required (not optional, unlike messagebox's implicit
default-root behaviour) - every popup here uses transient() + grab_set(),
which need a real owner window to sit on top of and disable while open.
Pass the calling screen's own frame or window - `self.frame` for a
screen embedded in dashboard.py's content area, `self.win`/`self.root`
for a screen that already IS its own Toplevel/root. This module resolves
it to a real top-level window itself via winfo_toplevel() (same
"whatever's handed in, normalize it" convention ui_style.center_window()
already uses for its own `parent=` argument), so passing either kind of
reference in works correctly.

Sizing/centering is delegated to ui_style.center_window() (not
reimplemented here) - same "one shared utility, no drift" reasoning as
everywhere else in this codebase. All popups below build their real
content FIRST and only call center_window() at the end with no explicit
width/height, so the window is always sized to what it actually needs to
show (never a guessed fixed size that could clip a long medicine name or
composition description - the exact anti-pattern already fixed once in
this codebase for the Factory Reset dialog).
"""
import tkinter as tk

import theme
import ui_style

# ---- shared visual constants for this module only -----------------------
_TITLE_FONT = ("Segoe UI", 13, "bold")
_BODY_FONT = ("Segoe UI", 10)
_BODY_FONT_BOLD = ("Segoe UI", 10, "bold")
_BUTTON_FONT = ("Segoe UI", 10, "bold")
_WRAPLENGTH = 380

_KIND_STYLE = {
    "info": {"icon": "ℹ", "header_bg": theme.PRIMARY},
    "warning": {"icon": "⚠", "header_bg": theme.STATUS_WARNING},
    "error": {"icon": "✖", "header_bg": theme.STATUS_DANGER},
    "confirm": {"icon": "?", "header_bg": theme.PRIMARY},
}


def _flat_button(parent, text, bg, command, fg="white", width=12):
    """
    One shared flat-button factory (relief='flat', hover highlight,
    hand2 cursor) - every button in every popup below is built through
    this, so "modern flat button" means exactly one look app-wide
    instead of four slightly different hand-rolled ones.
    """
    btn = tk.Button(
        parent, text=text, bg=bg, fg=fg, font=_BUTTON_FONT,
        relief="flat", bd=0, padx=18, pady=8, width=width,
        activebackground=bg, activeforeground=fg,
        cursor="hand2", command=command,
    )
    hover_bg = theme.PRIMARY_HOVER if bg == theme.PRIMARY else bg
    resting_bg = bg
    btn.bind("<Enter>", lambda e: btn.configure(bg=hover_bg))
    btn.bind("<Leave>", lambda e: btn.configure(bg=resting_bg))
    return btn


def _base_popup(parent, kind, title):
    """
    Builds the shared shell every popup below starts from: white body,
    a coloured header strip (colour depends on kind), transient()
    + resizable(False) modal shell. Returns (win, body, parent_top) -
    callers pack their own message/buttons into `body`, then call
    _finalize_modal(win, parent_top) once everything is built.
    """
    parent_top = parent.winfo_toplevel()
    style = _KIND_STYLE[kind]

    win = tk.Toplevel(parent_top)
    win.title(title)
    win.configure(bg=theme.SURFACE_WHITE)
    win.transient(parent_top)
    win.resizable(False, False)

    header = tk.Frame(win, bg=style["header_bg"])
    header.pack(fill="x")
    tk.Label(
        header, text=f"{style['icon']}  {title}", bg=style["header_bg"],
        fg="white", font=_TITLE_FONT, anchor="w", padx=16, pady=12,
    ).pack(fill="x")

    body = tk.Frame(win, bg=theme.SURFACE_WHITE, padx=24, pady=20)
    body.pack(fill="both", expand=True)
    return win, body, parent_top


def _finalize_modal(win, parent_top):
    """
    Sizes/centers the now-fully-built `win` over `parent_top` and makes
    it modal. Deliberately the LAST thing every popup function does -
    see ui_style.center_window()'s own docstring for why calling it only
    after real content is packed (not before, and never with a guessed
    fixed size) is required to avoid both a wrong size AND the "flash a
    tiny/wrong window first" bug fixed there.
    """
    ui_style.center_window(win, parent=parent_top)
    win.grab_set()
    win.focus_force()


def show_info(parent, title, message):
    """Drop-in replacement for messagebox.showinfo(title, message)."""
    win, body, parent_top = _base_popup(parent, "info", title)
    tk.Label(
        body, text=message, bg=theme.SURFACE_WHITE, fg=theme.TEXT_PRIMARY,
        font=_BODY_FONT, justify="left", wraplength=_WRAPLENGTH,
    ).pack(anchor="w", pady=(0, 20))
    btns = tk.Frame(body, bg=theme.SURFACE_WHITE)
    btns.pack(fill="x")
    _flat_button(btns, "OK", theme.PRIMARY, win.destroy).pack(side="right")
    _finalize_modal(win, parent_top)
    win.bind("<Return>", lambda e: win.destroy())
    win.bind("<Escape>", lambda e: win.destroy())
    win.wait_window()


def show_warning(parent, title, message):
    """Drop-in replacement for messagebox.showwarning(title, message)."""
    win, body, parent_top = _base_popup(parent, "warning", title)
    tk.Label(
        body, text=message, bg=theme.SURFACE_WHITE, fg=theme.STATUS_WARNING,
        font=_BODY_FONT_BOLD, justify="left", wraplength=_WRAPLENGTH,
    ).pack(anchor="w", pady=(0, 20))
    btns = tk.Frame(body, bg=theme.SURFACE_WHITE)
    btns.pack(fill="x")
    _flat_button(btns, "OK", theme.PRIMARY, win.destroy).pack(side="right")
    _finalize_modal(win, parent_top)
    win.bind("<Return>", lambda e: win.destroy())
    win.bind("<Escape>", lambda e: win.destroy())
    win.wait_window()


def show_error(parent, title, message):
    """Drop-in replacement for messagebox.showerror(title, message)."""
    win, body, parent_top = _base_popup(parent, "error", title)
    tk.Label(
        body, text=message, bg=theme.SURFACE_WHITE, fg=theme.STATUS_DANGER,
        font=_BODY_FONT_BOLD, justify="left", wraplength=_WRAPLENGTH,
    ).pack(anchor="w", pady=(0, 20))
    btns = tk.Frame(body, bg=theme.SURFACE_WHITE)
    btns.pack(fill="x")
    _flat_button(btns, "OK", theme.STATUS_DANGER, win.destroy).pack(side="right")
    _finalize_modal(win, parent_top)
    win.bind("<Return>", lambda e: win.destroy())
    win.bind("<Escape>", lambda e: win.destroy())
    win.wait_window()


def show_confirmation(parent, title, message, yes_text="Yes", no_text="No"):
    """
    Drop-in replacement for messagebox.askyesno(title, message) - same
    True/False return (True = Yes/confirm clicked; False = No clicked,
    OR the window closed via Esc/the X button without choosing, which
    matches askyesno's own "closing the dialog counts as No" behaviour).
    """
    result = {"value": False}
    win, body, parent_top = _base_popup(parent, "confirm", title)
    tk.Label(
        body, text=message, bg=theme.SURFACE_WHITE, fg=theme.TEXT_PRIMARY,
        font=_BODY_FONT, justify="left", wraplength=_WRAPLENGTH,
    ).pack(anchor="w", pady=(0, 20))

    def _choose(value):
        result["value"] = value
        win.destroy()

    btns = tk.Frame(body, bg=theme.SURFACE_WHITE)
    btns.pack(fill="x")
    _flat_button(btns, yes_text, theme.PRIMARY, lambda: _choose(True)).pack(side="right", padx=(8, 0))
    _flat_button(btns, no_text, theme.ACCENT_NEUTRAL, lambda: _choose(False)).pack(side="right")

    _finalize_modal(win, parent_top)
    win.bind("<Escape>", lambda e: _choose(False))
    win.protocol("WM_DELETE_WINDOW", lambda: _choose(False))
    win.wait_window()
    return result["value"]


def show_medicine_details(parent, medicine):
    """
    Custom popup for a quick "what is this medicine" summary - the
    styled generic successor to medicine_master.py's original hand-
    rolled "Selected Medicine Info" Toplevel (same information, this
    module's shared look/colour rules applied instead of a one-off
    bespoke popup, so there's only ONE medicine-details look in the app
    rather than two competing ones).

    `medicine` is a plain dict - callers pass only the keys they have;
    every key is optional and a missing one just skips that row instead
    of raising:
        name, company, batch, expiry (display string, any format the
        caller already uses), stock (int), reorder_level (int, drives
        the low-stock red colour), mrp, purchase, stock_value (float -
        computed from stock*purchase if omitted), uses, action_class,
        habit_forming (str/bool), expiry_days_left (int - drives the
        near-expiry colour: <=0 red/expired, <=90 amber/expiring soon,
        else green; omit entirely to leave Expiry uncoloured).
    """
    win, body, parent_top = _base_popup(parent, "info", medicine.get("name") or "Medicine Details")

    def _row(label, value, color=theme.TEXT_PRIMARY, bold=False):
        if value in (None, ""):
            return
        r = tk.Frame(body, bg=theme.SURFACE_WHITE)
        r.pack(fill="x", pady=3)
        tk.Label(
            r, text=label, bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
            font=_BODY_FONT, width=14, anchor="w",
        ).pack(side="left")
        tk.Label(
            r, text=str(value), bg=theme.SURFACE_WHITE, fg=color,
            font=_BODY_FONT_BOLD if bold else _BODY_FONT,
            anchor="w", wraplength=_WRAPLENGTH - 100, justify="left",
        ).pack(side="left", fill="x")

    _row("Company", medicine.get("company"))
    _row("Batch", medicine.get("batch"))

    expiry_days = medicine.get("expiry_days_left")
    expiry_color = theme.TEXT_PRIMARY
    if expiry_days is not None:
        if expiry_days <= 0:
            expiry_color = theme.STATUS_DANGER
        elif expiry_days <= 90:
            expiry_color = theme.STATUS_WARNING
        else:
            expiry_color = theme.STATUS_SUCCESS
    _row("Expiry", medicine.get("expiry"), color=expiry_color, bold=(expiry_color != theme.TEXT_PRIMARY))

    stock = medicine.get("stock")
    reorder_level = medicine.get("reorder_level")
    stock_color = theme.STATUS_SUCCESS
    if stock is not None and reorder_level is not None and stock <= reorder_level:
        stock_color = theme.STATUS_DANGER
    _row("Stock", stock, color=stock_color, bold=(stock_color == theme.STATUS_DANGER))

    _row("MRP", medicine.get("mrp"))
    _row("Purchase", medicine.get("purchase"))
    stock_value = medicine.get("stock_value")
    if stock_value is None and stock is not None and medicine.get("purchase") is not None:
        stock_value = round(stock * medicine["purchase"], 2)
    _row("Stock Value", stock_value)

    if medicine.get("action_class"):
        _row("Class", medicine.get("action_class"))
    if medicine.get("uses"):
        _row("Uses", medicine.get("uses"))
    if medicine.get("habit_forming"):
        _row("Habit Forming", "Yes", color=theme.STATUS_DANGER, bold=True)

    btns = tk.Frame(body, bg=theme.SURFACE_WHITE)
    btns.pack(fill="x", pady=(16, 0))
    _flat_button(btns, "Close", theme.PRIMARY, win.destroy).pack(side="right")

    _finalize_modal(win, parent_top)
    win.bind("<Escape>", lambda e: win.destroy())
