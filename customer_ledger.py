import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
# Aug 2026 repository-layer pass: all direct sqlite3 access has since
# moved into customer_ledger_repository.py (see that module's
# docstring) - DB_NAME itself is no longer imported here, only by the
# repository.
import customer_ledger_repository as repo
import ui_style
import ui_popups


class CustomerLedger:

    def __init__(self, frame):
        self.frame = frame
        self.create_variables()
        self.create_ui()
        self.load_customers()

    def create_variables(self):
        self.customer_name = tk.StringVar()
        self.paid_amount = tk.DoubleVar(value=0.0)
        self.total_credit = tk.StringVar(value="₹ 0.00")
        self.total_paid = tk.StringVar(value="₹ 0.00")
        self.balance_due = tk.StringVar(value="₹ 0.00")

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="CUSTOMER CREDIT & LEDGER (KHATA)",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        # ---------------- Top Selection Frame ----------------
        top_frame = tk.LabelFrame(
            self.frame,
            text="Customer Selection & Payment",
            font=("Segoe UI", 10, "bold")
        )
        top_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(top_frame, text="Customer Name").grid(row=0, column=0, padx=5, pady=5)
        self.cmbCustomer = ttk.Combobox(
            top_frame,
            textvariable=self.customer_name,
            width=25,
            state="normal"
        )
        self.cmbCustomer.grid(row=0, column=1, padx=5, pady=5)
        # ERP-wide keyboard-nav pass (Aug 2026): previously only a mouse
        # click on the dropdown (or the separate "Load Ledger" button)
        # did anything - typing a full customer name and pressing Enter
        # silently did nothing. Now typing narrows the list live, and
        # Enter/Tab-away/a mouse click all load the ledger identically.
        ui_style.bind_search_combo(
            self.cmbCustomer,
            on_filter=self._filter_customer_dropdown,
            on_confirm=self.load_customer_ledger,
        )

        tk.Button(
            top_frame,
            text="Load Ledger",
            bg="blue",
            fg="white",
            width=15,
            command=self.load_customer_ledger
        ).grid(row=0, column=2, padx=10)

        tk.Label(top_frame, text="Receive Amount (₹)").grid(row=0, column=3, padx=5)
        tk.Entry(top_frame, textvariable=self.paid_amount, width=15).grid(row=0, column=4, padx=5)

        tk.Button(
            top_frame,
            text="Add Payment",
            bg="green",
            fg="white",
            width=15,
            command=self.record_payment
        ).grid(row=0, column=5, padx=10)

        # ---------------- Summary Frame ----------------
        summary_frame = tk.Frame(self.frame)
        summary_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(summary_frame, text="Total Credit :", font=("Segoe UI", 11, "bold")).pack(side="left", padx=5)
        tk.Label(summary_frame, textvariable=self.total_credit, fg="red", font=("Segoe UI", 11, "bold")).pack(side="left", padx=10)

        tk.Label(summary_frame, text="Total Paid :", font=("Segoe UI", 11, "bold")).pack(side="left", padx=20)
        tk.Label(summary_frame, textvariable=self.total_paid, fg="green", font=("Segoe UI", 11, "bold")).pack(side="left", padx=10)

        tk.Label(summary_frame, text="Balance Due :", font=("Segoe UI", 11, "bold")).pack(side="left", padx=20)
        tk.Label(summary_frame, textvariable=self.balance_due, fg="blue", font=("Segoe UI", 11, "bold")).pack(side="left", padx=10)

        # ---------------- Ledger Table ----------------
        table_frame = tk.Frame(self.frame)
        table_frame.pack(fill="both", expand=True, padx=10, pady=10)

        columns = ("Date", "Bill No / Ref", "Type", "Amount", "Paid / Received")
        self.ledgerTable = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=15,
            style="ERP.Treeview"
        )

        for c in columns:
            self.ledgerTable.heading(c, text=c)
            self.ledgerTable.column(c, width=150, anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.ledgerTable.yview)
        self.ledgerTable.configure(yscrollcommand=scrollbar.set)
        self.ledgerTable.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def load_customers(self):
        self._customer_names = repo.list_customer_names_from_sales()
        self.cmbCustomer["values"] = self._customer_names

    def _filter_customer_dropdown(self, typed_text):
        typed = typed_text.lower()
        self.cmbCustomer["values"] = (
            self._customer_names if not typed
            else [n for n in self._customer_names if typed in n.lower()]
        )

    def load_customer_ledger(self, event=None):
        cust = self.customer_name.get().strip()
        if not cust:
            return

        self.ledgerTable.delete(*self.ledgerTable.get_children())

        try:
            repo.ensure_schema()
            sales_rows, payment_rows = repo.get_ledger_rows(cust)
            all_records = sorted(sales_rows + payment_rows, key=lambda x: x[0])

            tot_cred = 0.0
            tot_pyd = 0.0

            for r in all_records:
                self.ledgerTable.insert("", "end", values=(r[0], r[1], r[2], f"₹ {r[3]:.2f}", f"₹ {r[4]:.2f}"))
                tot_cred += float(r[3])
                tot_pyd += float(r[4])

            balance = tot_cred - tot_pyd

            self.total_credit.set(f"₹ {tot_cred:,.2f}")
            self.total_paid.set(f"₹ {tot_pyd:,.2f}")
            self.balance_due.set(f"₹ {balance:,.2f}")

        except Exception as e:
            ui_popups.show_error(self.frame, "Error", str(e))

    def record_payment(self):
        cust = self.customer_name.get().strip()
        amt = self.paid_amount.get()

        if not cust:
            ui_popups.show_error(self.frame, "Error", "Please select or enter a customer name.")
            return

        if amt <= 0:
            ui_popups.show_error(self.frame, "Error", "Enter a valid payment amount.")
            return

        try:
            repo.record_payment(cust, amt, datetime.now().strftime("%Y-%m-%d"))
            ui_popups.show_info(self.frame, "Success", f"Payment of ₹ {amt:.2f} recorded successfully for {cust}.")
            self.paid_amount.set(0.0)
            self.load_customer_ledger()
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))