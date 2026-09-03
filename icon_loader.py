"""
icon_loader.py
LifeCare Pharmacy ERP - loads icons/*.png (see generate_icons.py) as
cached tk.PhotoImage objects.

Tkinter PhotoImage objects get garbage-collected the moment their last
Python reference disappears - well before the widget displaying them is
destroyed, since widgets only hold a Tcl-side handle, not a Python
reference. That's a well-known Tkinter gotcha where an icon silently
goes blank a few seconds after appearing. _cache below keeps one
persistent Python reference per icon name for the whole process, so
every screen that calls get_icon("home") gets back the SAME
already-loaded PhotoImage instead of a fresh one that could vanish.

tk.PhotoImage(file=...) is used directly (not PIL's ImageTk) - Tk 8.6+
(what this app's ttkbootstrap/Tcl build requires anyway) reads PNG
natively, alpha channel included, so there's no need for an extra PIL
round-trip just to display a static icon.
"""

import tkinter as tk

from app_paths import app_path

_cache = {}


def get_icon(name):
    """
    Returns a cached tk.PhotoImage for icons/<name>.png, or None if the
    file is missing/unreadable. Callers should treat None as "skip the
    image=, show text only" - a missing icon file should never be the
    reason a whole screen fails to open.
    """
    if name in _cache:
        return _cache[name]
    path = app_path("icons", f"{name}.png")
    try:
        img = tk.PhotoImage(file=path)
    except Exception:
        return None
    _cache[name] = img
    return img
