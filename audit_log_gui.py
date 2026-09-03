"""
audit_log_gui.py
LifeCare Pharmacy ERP - Audit Trail viewer.

Read-only browse/search screen over the audit_log table (see
audit_log.py for the logger itself, session.py for how "who" is known).
Lives under Admin - this is an accountability tool for the owner, not a
day-to-day operational screen.
"""

import tkinter as tk
from tkinter import ttk

import audit_log


class AuditLogViewer:

    def __init__(self, frame):
        self.frame = frame
        self.search = tk.StringVar()

        self.create_ui()
        self.load_list()

    def create_ui(self):
        tk.Label(
            self.frame, text="AUDIT TRAIL - Who Changed What, When",
            bg="#1565C0", fg="white", font=("Segoe UI", 18, "bold"), pady=10
        ).pack(fill="x")

        top = tk.LabelFrame(self.frame, text="Search", font=("Segoe UI", 10, "bold"))
        top.pack(fill="x", padx=10, pady=10)

        tk.Label(top, text="Search (username / screen / action / details):").pack(side="left")
        tk.Entry(top, textvariable=self.search, width=40).pack(side="left", padx=5)
        self.search.trace_add("write", lambda *a: self.load_list())

        tk.Button(top, text="Refresh", bg="#1565C0", fg="white", width=12, command=self.load_list).pack(side="right")

        list_frame = tk.Frame(self.frame)
        list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        cols = ("Time", "User", "Screen", "Action", "Details")
        self.table = ttk.Treeview(list_frame, columns=cols, show="headings", height=22, style="ERP.Treeview")
        widths = {"Time": 140, "User": 100, "Screen": 130, "Action": 110, "Details": 460}
        for c in cols:
            self.table.heading(c, text=c)
            self.table.column(c, width=widths[c], anchor="w")

        vscroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=vscroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        self.countLabel = tk.Label(self.frame, text="", fg="#555555")
        self.countLabel.pack(anchor="w", padx=10, pady=(0, 10))

    def load_list(self):
        self.table.delete(*self.table.get_children())
        rows = audit_log.search_entries(self.search.get(), limit=500)
        for log_time, username, screen, action, details in rows:
            self.table.insert("", "end", values=(log_time, username, screen, action, details))
        self.countLabel.config(text=f"{len(rows)} entrie(s) shown (most recent 500 max)")
