import ctypes
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from database import create_database
from app_paths import APP_VERSION

# ---------------------------------------------------------------------------
# 1. High-DPI Fix (sharp fonts on Windows)
# ---------------------------------------------------------------------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# 2. Database init (unchanged - runs before UI)
# ---------------------------------------------------------------------------
create_database()

# ---------------------------------------------------------------------------
# 2b. Daily Auto-Backup (app open ஆகும்போது ஒரு தடவை)
# ---------------------------------------------------------------------------
from backup_manager import backup_now
try:
    backup_now()
except Exception as e:
    print(f"Backup warning: {e}")   # backup fail ஆனாலும் app crash ஆகக்கூடாது

# ---------------------------------------------------------------------------
# 3. Root window - Login screen முதலில் வரும்
# ---------------------------------------------------------------------------
root = ttk.Window(themename="flatly")
root.title(f"Life Care Pharmacy ERP v{APP_VERSION}")
# ---------------------------------------------------------------------------
# 4. Global font
# ---------------------------------------------------------------------------
import tkinter.font as tkfont
default_font = tkfont.nametofont("TkDefaultFont")
default_font.configure(family="Segoe UI", size=10)
root.option_add("*Font", default_font)

# ---------------------------------------------------------------------------
# 5. Named styles for ERP widgets (Dashboard/Treeview etc. use these)
# ---------------------------------------------------------------------------
style = ttk.Style()

style.configure(
    "ERP.Treeview",
    rowheight=32,
    font=("Segoe UI", 10),
    borderwidth=0,
)
# Blue header (#1565C0/white) matches Medicine Master's tksheet grid header
# and every screen's own title bar - previously this style only set the
# heading FONT, so every plain ttk.Treeview screen (16 of 23 sidebar
# screens) still showed flatly's default heading look instead of the
# app's blue, which is what the Aug 2026 UI-consistency audit flagged.
# This is a NAMED style ("ERP.Treeview.Heading", not the literal
# "Treeview.Heading") deliberately - it only affects widgets that opt in
# via style="ERP.Treeview", so it can't clash with ttkbootstrap's own
# "flatly" theme_use() the way ui_style.py's setup_excel_style() would
# (that one calls style.theme_use("clam") globally, which would fight
# ttkbootstrap's own theming across the WHOLE app, not just tables - not
# used for this reason).
style.configure(
    "ERP.Treeview.Heading",
    font=("Segoe UI Semibold", 10),
    background="#1565C0",
    foreground="white",
    relief="flat",
    padding=(6, 6),
)
style.map(
    "ERP.Treeview",
    background=[("selected", "#0d6efd")],
    foreground=[("selected", "white")],
)
# Without this map, the header flashes back to flatly's default grey the
# moment the mouse hovers over it (clam/flatly's Heading style has its
# own active/pressed background baked in that a plain configure() above
# doesn't override) - same issue ui_style.py's setup_excel_style() docs
# already called out for the literal Treeview style.
style.map(
    "ERP.Treeview.Heading",
    background=[("active", "#1565C0"), ("pressed", "#1565C0")],
    foreground=[("active", "white"), ("pressed", "white")],
)

style.configure("Sidebar.TFrame", background="#1e2a38")
style.configure(
    "Sidebar.TButton",
    font=("Segoe UI", 11),
    padding=(16, 12),
)

# Notebook tabs (Smart Alerts' Low Stock/Expiry/Distributor Return,
# Brand Master's Browse/Bulk Add, Bulk Import's paste/OCR tabs) were the
# last "heading"-like element still using flatly's plain default look
# instead of the app's blue theme - unlike Treeview above, this DOES
# configure the base "TNotebook"/"TNotebook.Tab" style names directly
# (not a named "ERP.*" variant) since every ttk.Notebook(...) call in the
# app already uses the default style with no style= kwarg, and nothing
# else in the app relies on Notebook's stock look, so there's no
# conflict to avoid the way there was with Treeview/tksheet.
style.configure("TNotebook", background="white", borderwidth=0, tabmargins=(2, 4, 2, 0))
style.configure(
    "TNotebook.Tab",
    font=("Segoe UI Semibold", 10),
    padding=(16, 8),
    background="#CFD8DC",
    foreground="#37474F",
)
style.map(
    "TNotebook.Tab",
    background=[("selected", "#1565C0"), ("active", "#90A4AE")],
    foreground=[("selected", "white"), ("active", "#37474F")],
)

# ---------------------------------------------------------------------------
# 6. Launch LOGIN screen (Dashboard-ஐ login.py தான் launch பண்ணும்,
#    login success ஆனப்பறகு)
# ---------------------------------------------------------------------------
from login import LoginWindow
LoginWindow(root)

root.mainloop()