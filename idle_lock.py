"""
idle_lock.py
Life Care Pharmacy ERP - Idle-Timeout Auto-Lock (Sep 2026)

Why this exists: a pharmacy counter PC is often left unlocked between
customers - if staff steps away without manually logging out, anyone who
walks up gets full access to Billing, Stock, and every Admin screen for
whoever was last logged in. This adds a Windows-style auto-lock: after
IDLE_LOCK_MINUTES of no mouse/keyboard activity ANYWHERE in the app, a
full-screen "Locked" overlay appears. Getting back in needs the SAME
logged-in user's password (or "Log Out Instead", which returns to the
normal Login screen so a different staff member can sign in).

Single source-of-truth timeout, same pattern as app_paths.py's
APP_VERSION - change this one number to change how long the app waits
before locking. Set to 0 to disable auto-lock entirely.
"""

IDLE_LOCK_MINUTES = 5

import time
import sqlite3
import tkinter as tk

from app_paths import DB_NAME
import auth_utils
import theme as app_theme


class IdleLockManager:
    """One instance per logged-in session - created right after Dashboard
    is built (see dashboard.py's Dashboard.__init__, near the end, right
    after the other one-time-per-session after() calls). Watches for any
    mouse/keyboard activity anywhere in the app via bind_all() - events
    bubble up from whatever screen is currently open inside Dashboard's
    body (Billing, Purchase, Medicine Master, etc.), so this manager
    doesn't need to know anything about individual screens - and shows a
    blocking lock overlay once IDLE_LOCK_MINUTES passes with zero
    activity anywhere.

    This is an app-level lock (a Toplevel overlay), not a Windows-level
    lock - it stops someone from casually using an unattended, already-
    logged-in ERP window, the same way this app's other protections
    (role-based sidebar, PBKDF2 password hashing) are app-level rather
    than OS-level. Good enough for a single shop-counter PC; not a
    replacement for Windows' own Win+L if the PC itself is shared.
    """

    def __init__(self, root, username):
        self.root = root
        self.username = username
        self.last_activity = time.time()
        self.locked = False
        self._lock_win = None

        if IDLE_LOCK_MINUTES <= 0:
            return  # auto-lock disabled

        # bind_all (not bind() on self.root alone) so activity landing on
        # any child widget - a click inside Billing's item table, typing
        # in Medicine Master's search box, etc. - still counts. add="+"
        # so this never overwrites any other bind_all() already
        # registered for these sequences elsewhere in the app.
        for seq in ("<Motion>", "<Button>", "<Key>"):
            self.root.bind_all(seq, self._on_activity, add="+")

        self._schedule_check()

    def _on_activity(self, event=None):
        self.last_activity = time.time()

    def _schedule_check(self):
        self.root.after(1000, self._check_idle)

    def _check_idle(self):
        if not self.locked:
            idle_seconds = time.time() - self.last_activity
            if idle_seconds >= IDLE_LOCK_MINUTES * 60:
                self.lock()
        self._schedule_check()

    def lock(self):
        if self.locked or not self.root.winfo_exists():
            return
        self.locked = True

        win = tk.Toplevel(self.root)
        self._lock_win = win
        win.overrideredirect(True)  # no title bar/border - no X button to close this with
        win.attributes("-topmost", True)
        win.configure(bg="#1B2631")

        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win.geometry(f"{sw}x{sh}+0+0")

        card = tk.Frame(win, bg="#1B2631")
        card.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            card, text="\U0001F512", font=("Segoe UI", 48), bg="#1B2631", fg="white"
        ).pack(pady=(0, 10))
        tk.Label(
            card, text="Life Care Pharmacy ERP - Locked", font=("Segoe UI", 18, "bold"),
            bg="#1B2631", fg="white"
        ).pack()
        tk.Label(
            card, text=f"Logged in as: {self.username}", font=("Segoe UI", 11),
            bg="#1B2631", fg="#B0BEC5"
        ).pack(pady=(4, 20))

        pwd_var = tk.StringVar()
        pwd_entry = tk.Entry(
            card, textvariable=pwd_var, show="*", width=28, font=("Segoe UI", 12),
            justify="center"
        )
        pwd_entry.pack(ipady=5)
        pwd_entry.focus_set()

        status_var = tk.StringVar()
        tk.Label(
            card, textvariable=status_var, bg="#1B2631", fg="#e74c3c", font=("Segoe UI", 9)
        ).pack(pady=(8, 0))

        def try_unlock(event=None):
            entered = pwd_var.get()
            con = sqlite3.connect(DB_NAME)
            cur = con.cursor()
            cur.execute("SELECT password FROM users WHERE username=?", (self.username,))
            row = cur.fetchone()
            con.close()
            if row and auth_utils.verify_password(entered, row[0]):
                self.locked = False
                self.last_activity = time.time()
                win.destroy()
                self._lock_win = None
            else:
                status_var.set("Incorrect password. Try again.")
                pwd_var.set("")
                pwd_entry.focus_set()

        pwd_entry.bind("<Return>", try_unlock)

        tk.Button(
            card, text="Unlock", command=try_unlock, bg=app_theme.PRIMARY, fg="white",
            font=("Segoe UI", 11, "bold"), relief="flat", padx=20, pady=6, cursor="hand2"
        ).pack(pady=(16, 6))

        def log_out_instead():
            # Same "destroy this root, build a fresh one, mainloop again"
            # pattern login.py's own launch_dashboard() already uses for
            # the Login -> Dashboard handoff - not a new pattern, just
            # run in reverse (Dashboard -> a fresh Login) so a different
            # staff member can sign in without needing this user's
            # password.
            win.destroy()
            self.root.destroy()
            import ttkbootstrap as ttk
            from login import LoginWindow
            new_root = ttk.Window(themename="flatly")
            LoginWindow(new_root)
            new_root.mainloop()

        tk.Button(
            card, text="Log Out Instead", command=log_out_instead, bg="#7f8c8d", fg="white",
            font=("Segoe UI", 9), relief="flat", padx=10, pady=4, cursor="hand2"
        ).pack()

        # overrideredirect() already removed the window's own close
        # button - this also stops Escape from doing anything useful on
        # this overlay, and grab_set() keeps every click/keypress
        # trapped on this window until it's unlocked.
        win.bind("<Escape>", lambda e: "break")
        win.grab_set()
