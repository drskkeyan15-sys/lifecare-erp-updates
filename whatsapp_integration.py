import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import webbrowser
import urllib.parse
from app_paths import DB_NAME
from icon_loader import get_icon
import ui_style
import ui_popups


def open_whatsapp_message(phone, message):
    """
    Shared send mechanism - opens a wa.me deep link with the message
    pre-filled, same as this screen's own "Send via WhatsApp" button.
    Pulled out to a module-level function so other screens (Refill
    Reminders) can reuse the exact same phone-normalization + encoding
    logic instead of a second copy that could drift (e.g. a different
    default country code).

    IMPORTANT LIMITATION: this only OPENS the compose window with the
    text filled in - it does not transmit the message. Requires WhatsApp
    Desktop or a logged-in WhatsApp Web session, and a human still has to
    click Send. There is no bulk/automatic sending here.
    """
    phone = (phone or "").strip()
    if not phone:
        raise ValueError("Phone number is required.")
    if not phone.startswith("+"):
        phone = "+91" + phone

    encoded_msg = urllib.parse.quote(message)
    url = f"https://wa.me/{phone}?text={encoded_msg}"
    webbrowser.open(url)


class WhatsAppIntegration:

    def __init__(self, frame):
        self.frame = frame
        self.create_variables()
        self.create_ui()
        self.load_bills()

    def create_variables(self):
        self.bill_no = tk.StringVar()
        self.phone_no = tk.StringVar()

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="WHATSAPP / SMS INVOICE INTEGRATION",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        # ---------------- Form Frame ----------------
        form_frame = tk.LabelFrame(
            self.frame,
            text="Send Digital Bill / Reminder via WhatsApp",
            font=("Segoe UI", 10, "bold")
        )
        form_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(form_frame, text="Select Bill No").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.cmbBill = ttk.Combobox(
            form_frame,
            textvariable=self.bill_no,
            width=25,
            state="normal"
        )
        self.cmbBill.grid(row=0, column=1, padx=5, pady=5)
        # ERP-wide keyboard-nav pass (Aug 2026): typing now narrows the
        # bill list live, and Enter/Tab-away act the same as the mouse
        # click that previously was the only way to fetch a bill's
        # details.
        ui_style.bind_search_combo(
            self.cmbBill,
            on_filter=self._filter_bill_dropdown,
            on_confirm=self.fetch_bill_details,
        )

        tk.Label(form_frame, text="Customer Phone No").grid(row=0, column=2, padx=5, pady=5, sticky="w")
        tk.Entry(form_frame, textvariable=self.phone_no, width=20).grid(row=0, column=3, padx=5, pady=5)

        tk.Label(form_frame, text="Message Content").grid(row=1, column=0, padx=5, pady=5, sticky="nw")
        self.txtMsg = tk.Text(form_frame, height=6, width=50, wrap="word")
        self.txtMsg.grid(row=1, column=1, columnspan=3, padx=5, pady=5)

        tk.Button(
            form_frame,
            text=" Send via WhatsApp",
            image=get_icon("chat"),
            compound="left",
            bg="green",
            fg="white",
            font=("Segoe UI", 11, "bold"),
            padx=16, pady=6,
            command=self.send_whatsapp
        ).grid(row=2, column=1, columnspan=2, pady=15)

    def load_bills(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute("SELECT bill_no FROM sales ORDER BY id DESC")
            rows = cur.fetchall()
            self._bill_numbers = [r[0] for r in rows]
        except Exception:
            self._bill_numbers = []
        con.close()
        self.cmbBill["values"] = self._bill_numbers

    def _filter_bill_dropdown(self, typed_text):
        typed = typed_text.lower()
        self.cmbBill["values"] = (
            self._bill_numbers if not typed
            else [b for b in self._bill_numbers if typed in str(b).lower()]
        )

    def fetch_bill_details(self, event=None):
        b_no = self.bill_no.get().strip()
        if not b_no:
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute("SELECT bill_date, customer, total FROM sales WHERE bill_no=?", (b_no,))
            row = cur.fetchone()
            if row:
                b_date, cust, total = row
                cust_name = cust if cust else "Valued Customer"
                msg = f"Hello {cust_name},\nThank you for shopping at Life Care Pharmacy!\nBill No: {b_no}\nDate: {b_date}\nTotal Amount: ₹{total:.2f}\nVisit Again!"
                self.txtMsg.delete("1.0", tk.END)
                self.txtMsg.insert("1.0", msg)
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", str(e))
        finally:
            con.close()

    def send_whatsapp(self):
        phone = self.phone_no.get().strip()
        msg = self.txtMsg.get("1.0", tk.END).strip()

        if not phone:
            ui_popups.show_error(self.frame, "Error", "Please enter customer phone number.")
            return

        try:
            open_whatsapp_message(phone, msg)
            ui_popups.show_info(self.frame, "Success", "WhatsApp opened with bill message successfully!")
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", str(e))