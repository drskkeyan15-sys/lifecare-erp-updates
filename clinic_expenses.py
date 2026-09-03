import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

import clinic_repository as repo
import session
import theme
from money import to_money
import ui_popups

EXPENSE_CATEGORIES = [
    "Staff Salary", "Rent", "Electricity Bill", "Internet", "Water",
    "Cleaning", "Equipment Maintenance", "Consumables", "Other",
]


class ClinicExpenses:
    """Clinic-side expense entry - writes into the SAME `expenses` table
    the rest of the app already has (module='Clinic'), not a new table.
    See clinic_repository.add_clinic_expense()."""

    def __init__(self, frame, on_close=None):
        self.frame = frame
        self.on_close = on_close
        self.create_variables()
        self.create_ui()
        self.load_expenses()

    def create_variables(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self.expense_date = tk.StringVar(value=today)
        self.category = tk.StringVar(value=EXPENSE_CATEGORIES[0])
        self.description = tk.StringVar()
        self.amount = tk.StringVar(value="0")
        self.payment_mode = tk.StringVar(value="Cash")
        self.date_from = tk.StringVar(value=today[:8] + "01")
        self.date_to = tk.StringVar(value=today)

    def create_ui(self):
        title = tk.Label(
            self.frame, text="CLINIC EXPENSES",
            bg=theme.PRIMARY, fg="white", font=("Segoe UI", 18, "bold"), pady=10
        )
        title.pack(fill="x")

        form = tk.LabelFrame(self.frame, text="New Expense", font=("Segoe UI", 10, "bold"))
        form.pack(fill="x", padx=10, pady=10)

        tk.Label(form, text="Date (YYYY-MM-DD)").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.expense_date, width=14).grid(row=0, column=1)

        tk.Label(form, text="Category").grid(row=0, column=2, padx=5)
        ttk.Combobox(form, textvariable=self.category, values=EXPENSE_CATEGORIES,
                     state="readonly", width=20).grid(row=0, column=3)

        tk.Label(form, text="Amount (₹)").grid(row=0, column=4, padx=5)
        tk.Entry(form, textvariable=self.amount, width=12).grid(row=0, column=5)

        tk.Label(form, text="Payment Mode").grid(row=0, column=6, padx=5)
        ttk.Combobox(form, textvariable=self.payment_mode, values=["Cash", "UPI", "Card", "Bank Transfer"],
                     state="readonly", width=12).grid(row=0, column=7)

        tk.Label(form, text="Description / Notes").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.description, width=70).grid(row=1, column=1, columnspan=6, sticky="we")

        tk.Button(form, text="Add Expense", bg=theme.STATUS_SUCCESS, fg="white",
                  command=self.add_expense).grid(row=1, column=7, padx=5)

        filt = tk.Frame(self.frame)
        filt.pack(fill="x", padx=10, pady=5)
        tk.Label(filt, text="From").pack(side="left", padx=5)
        tk.Entry(filt, textvariable=self.date_from, width=12).pack(side="left")
        tk.Label(filt, text="To").pack(side="left", padx=5)
        tk.Entry(filt, textvariable=self.date_to, width=12).pack(side="left")
        tk.Button(filt, text="Filter", bg=theme.PRIMARY, fg="white",
                  command=self.load_expenses).pack(side="left", padx=10)
        if self.on_close:
            tk.Button(filt, text="Close", bg=theme.STATUS_DANGER, fg="white",
                      command=self.on_close).pack(side="right", padx=5)

        table = tk.Frame(self.frame)
        table.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("ID", "Date", "Category", "Description", "Amount", "Payment Mode")
        self.expenseTable = ttk.Treeview(table, columns=cols, show="headings", height=16, style="ERP.Treeview")
        for c in cols:
            self.expenseTable.heading(c, text=c)
            self.expenseTable.column(c, width=280 if c == "Description" else 120, anchor="center")
        self.expenseTable.pack(fill="both", expand=True)

        footer = tk.Frame(self.frame)
        footer.pack(fill="x", padx=10, pady=(0, 10))
        self.lblTotal = tk.Label(footer, text="Total : ₹ 0.00", font=("Segoe UI", 10, "bold"), fg=theme.PRIMARY)
        self.lblTotal.pack(side="left")

    def add_expense(self):
        if not self.expense_date.get().strip():
            ui_popups.show_error(self.frame, "Error", "Date is required")
            return
        try:
            amount = to_money(self.amount.get())
        except Exception:
            ui_popups.show_error(self.frame, "Error", "Invalid amount")
            return
        if amount <= 0:
            ui_popups.show_error(self.frame, "Error", "Amount must be greater than 0")
            return
        try:
            repo.add_clinic_expense(
                self.expense_date.get().strip(), self.category.get(),
                self.description.get().strip(), amount, self.payment_mode.get(),
                created_by=session.get_current_user()
            )
            ui_popups.show_info(self.frame, "Success", "Expense Saved")
            self.amount.set("0")
            self.description.set("")
            self.load_expenses()
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))

    def load_expenses(self):
        self.expenseTable.delete(*self.expenseTable.get_children())
        rows = repo.list_clinic_expenses(self.date_from.get().strip(), self.date_to.get().strip())
        total = 0.0
        for row in rows:
            self.expenseTable.insert("", "end", values=row)
            total += row[4] or 0
        self.lblTotal.config(text=f"Total : ₹ {to_money(total):,.2f}  ({len(rows)} entries)")
