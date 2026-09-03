"""
refill_reminder.py
LifeCare Pharmacy ERP - WhatsApp Refill Reminder

What this is: a list of (customer, medicine) pairs where enough days have
passed since their last purchase of that medicine that they MIGHT be due
for a refill, with a one-click way to send them a WhatsApp nudge.

What this deliberately is NOT: a dosing/frequency engine. It does not try
to infer how many days a pack should last from qty purchased, pack size,
or the medicine's usual dosing - that would be a clinical judgement this
app has no business making silently. It only ever surfaces one plain
fact - "days since they last bought this" - past a threshold the
pharmacist sets and can change any time. The pharmacist's own judgement
decides which of these are genuinely worth a reminder.

Sending mechanism: reuses whatsapp_integration.py's open_whatsapp_message()
- a wa.me deep link opened in the browser/WhatsApp Desktop with the
message pre-filled. It does NOT send automatically; each reminder still
needs a manual click to actually transmit, and there's no bulk-send here
(same limitation as the existing WhatsApp Invoice screen).
"""

import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from datetime import datetime

from app_paths import DB_NAME
from whatsapp_integration import open_whatsapp_message
from icon_loader import get_icon
import ui_popups


class RefillReminder:

    DEFAULT_THRESHOLD_DAYS = 25

    def __init__(self, frame):
        self.frame = frame
        self.threshold_days = tk.IntVar(value=self.DEFAULT_THRESHOLD_DAYS)
        self._due_rows = []

        self.create_ui()
        self.refresh()

    # ==========================================
    # UI
    # ==========================================

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="REFILL REMINDERS",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        tk.Label(
            self.frame,
            text=(
                "Customers who haven't repurchased a medicine in a while - based only on "
                "days since their last purchase of it, not any dosing/frequency assumption. "
                "Use your own judgement on which ones are genuinely due for a refill."
            ),
            fg="#555555", font=("Segoe UI", 9), wraplength=1400, justify="left", anchor="w"
        ).pack(fill="x", padx=10, pady=(8, 0))

        controls = tk.LabelFrame(self.frame, text="Refill Reminder Settings", font=("Segoe UI", 10, "bold"))
        controls.pack(fill="x", padx=10, pady=10)

        tk.Label(controls, text="Days since last purchase:").pack(side="left", padx=(0, 5))
        spin = tk.Spinbox(
            controls, from_=7, to=180, width=6, textvariable=self.threshold_days,
            command=self.refresh
        )
        spin.pack(side="left")
        spin.bind("<Return>", lambda e: self.refresh())

        tk.Button(
            controls, text="Refresh", bg="#1565C0", fg="white", width=12, command=self.refresh
        ).pack(side="left", padx=10)

        tk.Button(
            controls, text=" Send WhatsApp Reminder", image=get_icon("chat"), compound="left",
            bg="green", fg="white", padx=14, pady=4,
            command=self.send_reminder
        ).pack(side="left", padx=5)

        table_frame = tk.Frame(self.frame)
        table_frame.pack(fill="both", expand=True, padx=10, pady=10)

        cols = ("Customer", "Medicine", "Last Purchased", "Days Since", "Qty Last Time", "Phone")
        self.table = ttk.Treeview(table_frame, columns=cols, show="headings", height=18, style="ERP.Treeview")
        for c in cols:
            self.table.heading(c, text=c)
            self.table.column(c, width=180 if c in ("Customer", "Medicine") else 110, anchor="center")

        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.table.tag_configure("no_phone", foreground="#999999")

        self.lblCount = tk.Label(self.frame, text="", fg="blue", font=("Segoe UI", 10, "bold"))
        self.lblCount.pack(anchor="w", padx=10, pady=(0, 10))

    # ==========================================
    # DATA
    # ==========================================

    def refresh(self):
        self.table.delete(*self.table.get_children())

        try:
            threshold = int(self.threshold_days.get())
        except Exception:
            threshold = self.DEFAULT_THRESHOLD_DAYS

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        # bill_date is stored ISO ("%Y-%m-%d") by billing.py's save_bill()
        # consistently, so plain MAX()/string comparison is safe here -
        # same convention reports.py already relies on for its date-range
        # filters.
        cur.execute("""
            SELECT s.customer, si.medicine, MAX(s.bill_date) AS last_date, SUM(si.qty) AS last_qty
            FROM sales_items si
            JOIN sales s ON si.bill_no = s.bill_no
            WHERE s.customer IS NOT NULL AND TRIM(s.customer) <> ''
            GROUP BY s.customer, si.medicine
        """)
        rows = cur.fetchall()

        # customer_name -> phone, built once rather than one query per
        # row. sales.customer is free text (no FK to customers.id), so
        # this is a best-effort exact-name match - a customer billed
        # under a slightly different spelling won't get a phone number
        # here and the row will just show blank (Send button then warns
        # instead of guessing a number).
        cur.execute("SELECT customer_name, phone FROM customers")
        phone_by_customer = {name: (phone or "") for name, phone in cur.fetchall() if name}
        con.close()

        today = datetime.now()
        due = []
        for customer, medicine, last_date, qty in rows:
            try:
                last_dt = datetime.strptime(last_date, "%Y-%m-%d")
            except Exception:
                continue  # unparsable/legacy date format, skip rather than guess
            days_since = (today - last_dt).days
            if days_since >= threshold:
                phone = phone_by_customer.get(customer, "")
                due.append((customer, medicine, last_date, days_since, int(qty or 0), phone))

        due.sort(key=lambda r: -r[3])  # most overdue first
        self._due_rows = due

        for row in due:
            tag = "no_phone" if not row[5] else ""
            self.table.insert("", "end", values=row, tags=(tag,) if tag else ())

        self.lblCount.config(
            text=f"{len(due)} customer-medicine pair(s) due for a reminder "
                 f"(>= {threshold} days since last purchase)"
        )

    # ==========================================
    # SEND
    # ==========================================

    def send_reminder(self):
        selection = self.table.selection()
        if not selection:
            ui_popups.show_info(self.frame, "Select a Row", "Select a customer from the list first.")
            return

        customer, medicine, last_date, days_since, qty, phone = self.table.item(selection[0])["values"]

        if not phone:
            ui_popups.show_warning(self.frame, 
                "No Phone on File",
                f'"{customer}" has no phone number saved in Customer Master (or their '
                "billing name doesn't exactly match a Customer Master record).\n\n"
                "Add/fix their phone number there, then Refresh this screen."
            )
            return

        message = (
            f"Hi {customer}, this is a reminder from Life Care Pharmacy.\n"
            f'Your medicine "{medicine}" was last purchased on {last_date} '
            f"({days_since} days ago) - it may be time for a refill.\n"
            "Please visit us or reply to this message to reorder. Thank you!"
        )

        try:
            open_whatsapp_message(phone, message)
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", str(e))
