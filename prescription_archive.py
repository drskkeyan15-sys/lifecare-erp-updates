"""
prescription_archive.py
LifeCare Pharmacy ERP - Patient Prescription Archive.

Some patients buy the same medicines every month (chronic conditions -
BP, diabetes, thyroid etc). This screen saves a free-text prescription
note per customer visit (doctor, medicine list, any note) so the
pharmacist can pull it back up next time that customer walks in, instead
of relying on memory or re-reading a paper prescription each visit.

Deliberately simple / free-text rather than structured (one row per
medicine with dosage/frequency fields): a real prescription's medicine
list varies wildly in how it's written, and forcing it into rigid
columns would make data entry slower for zero real benefit here - this
is a REFERENCE note, not a billing or compliance record (Schedule H1's
own doctor/patient/address capture in Billing already handles the
compliance side, see billing.py + reports.py's H1/Schedule X registers).

customer_name is plain text, matching every other customer-linked table
in this app (sales.customer, customer_payments.customer, etc.) - none of
them use a foreign key to customers.id, so this stays consistent rather
than being the one table that's different.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from datetime import datetime

from app_paths import DB_NAME
import audit_log
import ui_popups


class PrescriptionArchive:

    def __init__(self, frame):
        self.frame = frame
        self.selected_id = None
        self._customer_names = []

        self.create_variables()
        self.create_ui()
        self.load_customers()
        self.load_history()

    # ==========================================
    # VARIABLES
    # ==========================================

    def create_variables(self):
        self.customer_name = tk.StringVar()
        self.rx_date = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.doctor = tk.StringVar()
        self.notes = tk.StringVar()
        self.search_customer = tk.StringVar()

    # ==========================================
    # UI
    # ==========================================

    def create_ui(self):
        tk.Label(
            self.frame, text="PATIENT PRESCRIPTION ARCHIVE",
            bg="#1565C0", fg="white", font=("Segoe UI", 18, "bold"), pady=10
        ).pack(fill="x")

        tk.Label(
            self.frame,
            text=(
                "Save a prescription note against a customer so it's there next time they "
                "come back for a refill - a reference note, not a legal/compliance record "
                "(Schedule H1 sales are already tracked separately in Reports)."
            ),
            fg="#555555", font=("Segoe UI", 9), wraplength=1400, justify="left", anchor="w"
        ).pack(fill="x", padx=10, pady=(8, 0))

        form = tk.LabelFrame(self.frame, text="Add / Edit Prescription Note", font=("Segoe UI", 11, "bold"))
        form.pack(fill="x", padx=10, pady=10)

        tk.Label(form, text="Customer").grid(row=0, column=0, padx=5, pady=6, sticky="w")
        self.cmbCustomer = ttk.Combobox(form, textvariable=self.customer_name, width=28)
        self.cmbCustomer.grid(row=0, column=1, padx=5, pady=6, sticky="w")
        self.cmbCustomer.bind("<KeyRelease>", self._filter_customer_dropdown)

        tk.Label(form, text="Date").grid(row=0, column=2, padx=5, pady=6, sticky="w")
        tk.Entry(form, textvariable=self.rx_date, width=15).grid(row=0, column=3, padx=5, pady=6, sticky="w")

        tk.Label(form, text="Doctor").grid(row=0, column=4, padx=5, pady=6, sticky="w")
        tk.Entry(form, textvariable=self.doctor, width=22).grid(row=0, column=5, padx=5, pady=6, sticky="w")

        tk.Label(form, text="Medicines").grid(row=1, column=0, padx=5, pady=6, sticky="nw")
        self.medicinesBox = tk.Text(form, width=70, height=4, font=("Segoe UI", 10))
        self.medicinesBox.grid(row=1, column=1, columnspan=5, padx=5, pady=6, sticky="we")

        tk.Label(form, text="Note").grid(row=2, column=0, padx=5, pady=6, sticky="w")
        tk.Entry(form, textvariable=self.notes, width=70).grid(
            row=2, column=1, columnspan=5, padx=5, pady=6, sticky="we"
        )

        btn_row = tk.Frame(form)
        btn_row.grid(row=3, column=0, columnspan=6, sticky="w", padx=5, pady=(4, 6))

        tk.Button(
            btn_row, text="Save", bg="green", fg="white", width=14,
            command=self.save_prescription
        ).pack(side="left", padx=(0, 5))

        tk.Button(
            btn_row, text="New / Clear", width=14,
            command=self.clear_form
        ).pack(side="left", padx=5)

        tk.Button(
            btn_row, text="Delete Selected", bg="#C62828", fg="white", width=14,
            command=self.delete_prescription
        ).pack(side="left", padx=5)

        # ---- Search + History ----
        search_frame = tk.Frame(self.frame)
        search_frame.pack(fill="x", padx=10, pady=(0, 10))

        tk.Label(search_frame, text="Search by Customer:").pack(side="left", padx=(0, 5))
        search_entry = tk.Entry(search_frame, textvariable=self.search_customer, width=30)
        search_entry.pack(side="left")
        search_entry.bind("<KeyRelease>", lambda e: self.load_history())

        tk.Button(
            search_frame, text="Show All", command=self._clear_search
        ).pack(side="left", padx=5)

        hist_frame = tk.LabelFrame(self.frame, text="Saved Prescriptions", font=("Segoe UI", 10, "bold"))
        hist_frame.pack(fill="both", expand=True, padx=10, pady=10)

        cols = ("Date", "Customer", "Doctor", "Medicines", "Note")
        self.historyTable = ttk.Treeview(hist_frame, columns=cols, show="headings", height=14, style="ERP.Treeview")
        widths = {"Date": 90, "Customer": 160, "Doctor": 140, "Medicines": 420, "Note": 220}
        for c in cols:
            self.historyTable.heading(c, text=c)
            self.historyTable.column(c, width=widths[c], anchor="w")

        vscroll = ttk.Scrollbar(hist_frame, orient="vertical", command=self.historyTable.yview)
        self.historyTable.configure(yscrollcommand=vscroll.set)
        self.historyTable.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        self.historyTable.bind("<<TreeviewSelect>>", self.select_record)

    # ==========================================
    # CUSTOMER AUTOCOMPLETE (same pattern as stock_adjustment.py's
    # medicine combobox / billing.py's customer field)
    # ==========================================

    def load_customers(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT DISTINCT customer_name FROM customers ORDER BY customer_name")
        self._customer_names = [r[0] for r in cur.fetchall() if r[0]]
        con.close()
        self.cmbCustomer["values"] = self._customer_names

    def _filter_customer_dropdown(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        typed = self.customer_name.get().lower()
        self.cmbCustomer["values"] = (
            self._customer_names if not typed
            else [n for n in self._customer_names if typed in n.lower()]
        )

    def _clear_search(self):
        self.search_customer.set("")
        self.load_history()

    # ==========================================
    # SAVE / DELETE
    # ==========================================

    def save_prescription(self):
        customer = self.customer_name.get().strip()
        if not customer:
            ui_popups.show_error(self.frame, "Error", "Enter/select a Customer.")
            return

        date = self.rx_date.get().strip()
        if not date:
            ui_popups.show_error(self.frame, "Error", "Enter a date.")
            return

        doctor = self.doctor.get().strip()
        medicines = self.medicinesBox.get("1.0", "end-1c").strip()
        note = self.notes.get().strip()

        if not medicines:
            if not ui_popups.show_confirmation(self.frame, 
                "No Medicines Entered",
                "Medicines field is empty - save this note anyway?"
            ):
                return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            if self.selected_id is None:
                cur.execute(
                    "INSERT INTO customer_prescriptions"
                    "(customer_name, rx_date, doctor, medicines, notes, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (customer, date, doctor, medicines, note, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
                action = "Create"
            else:
                cur.execute(
                    "UPDATE customer_prescriptions SET "
                    "customer_name=?, rx_date=?, doctor=?, medicines=?, notes=? "
                    "WHERE id=?",
                    (customer, date, doctor, medicines, note, self.selected_id)
                )
                action = "Update"
            con.commit()
        except Exception as e:
            con.rollback()
            con.close()
            ui_popups.show_error(self.frame, "Database Error", str(e))
            return
        con.close()

        audit_log.log_action(
            "Prescription Archive", action,
            f'{customer} ({date}), doctor="{doctor}"'
        )

        ui_popups.show_info(self.frame, "Saved", "Prescription note saved.")
        self.clear_form()
        self.load_customers()
        self.load_history()

    def delete_prescription(self):
        if self.selected_id is None:
            ui_popups.show_info(self.frame, "Select a Row", "Select a saved prescription from the list first.")
            return

        if not ui_popups.show_confirmation(self.frame, "Confirm", "Delete this prescription note?"):
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT customer_name, rx_date FROM customer_prescriptions WHERE id=?", (self.selected_id,))
        row = cur.fetchone()
        cur.execute("DELETE FROM customer_prescriptions WHERE id=?", (self.selected_id,))
        con.commit()
        con.close()

        if row:
            audit_log.log_action("Prescription Archive", "Delete", f"{row[0]} ({row[1]})")

        self.clear_form()
        self.load_history()

    def clear_form(self):
        self.selected_id = None
        self.customer_name.set("")
        self.rx_date.set(datetime.now().strftime("%Y-%m-%d"))
        self.doctor.set("")
        self.notes.set("")
        self.medicinesBox.delete("1.0", "end")

    # ==========================================
    # HISTORY / SELECT
    # ==========================================

    def load_history(self):
        self.historyTable.delete(*self.historyTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        search = self.search_customer.get().strip()
        if search:
            cur.execute(
                "SELECT id, rx_date, customer_name, doctor, medicines, notes "
                "FROM customer_prescriptions WHERE customer_name LIKE ? "
                "ORDER BY rx_date DESC, id DESC LIMIT 500",
                (f"%{search}%",)
            )
        else:
            cur.execute(
                "SELECT id, rx_date, customer_name, doctor, medicines, notes "
                "FROM customer_prescriptions ORDER BY rx_date DESC, id DESC LIMIT 500"
            )
        rows = cur.fetchall()
        con.close()

        # Parallel list mapping each Treeview row back to its id - kept
        # simple (Treeview iid = str(id) directly) since ids are already
        # unique, unlike tksheet screens elsewhere that needed a separate
        # positional lookup.
        for rid, rx_date, customer, doctor, medicines, note in rows:
            medicines_preview = (medicines or "").replace("\n", " | ")
            if len(medicines_preview) > 80:
                medicines_preview = medicines_preview[:77] + "..."
            self.historyTable.insert(
                "", "end", iid=str(rid),
                values=(rx_date, customer, doctor or "", medicines_preview, note or "")
            )

    def select_record(self, event=None):
        selection = self.historyTable.selection()
        if not selection:
            return
        rid = int(selection[0])

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute(
            "SELECT customer_name, rx_date, doctor, medicines, notes "
            "FROM customer_prescriptions WHERE id=?", (rid,)
        )
        row = cur.fetchone()
        con.close()

        if row is None:
            return

        self.selected_id = rid
        self.customer_name.set(row[0] or "")
        self.rx_date.set(row[1] or "")
        self.doctor.set(row[2] or "")
        self.medicinesBox.delete("1.0", "end")
        self.medicinesBox.insert("1.0", row[3] or "")
        self.notes.set(row[4] or "")
