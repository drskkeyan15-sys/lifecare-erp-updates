"""
daybook.py
Daily Cash Register - opening/closing cash reconciliation.

Opening Balance (pharmacist enters, or auto-suggested from the most
recent earlier day's saved Closing Balance) + Cash Sales - Cash Expenses
- Cash Paid to Suppliers = Closing Balance. Card/UPI/other-mode sales are
shown separately, informational only - they don't touch the physical
cash drawer, so they don't belong in this reconciliation.

Deliberately NOT included: purchase invoice totals themselves
(purchase.py doesn't record how/when a supplier was actually paid - only
supplier_payments does, via its new payment_mode column added alongside
this feature). Counting invoice totals here would double-count against
the same-day-or-later supplier_payments entry for that invoice.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from datetime import datetime

from app_paths import DB_NAME
import ui_popups


class Daybook:

    def __init__(self, frame):
        self.frame = frame
        self._cash_sales_val = 0.0
        self._cash_expenses_val = 0.0
        self._cash_paid_val = 0.0
        self.create_variables()
        self.create_ui()
        self.load_day()

    def create_variables(self):
        self.entry_date = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.opening_balance = tk.DoubleVar(value=0.0)
        self.cash_sales = tk.StringVar(value="₹ 0.00")
        self.other_sales = tk.StringVar(value="₹ 0.00")
        self.cash_expenses = tk.StringVar(value="₹ 0.00")
        self.cash_paid_suppliers = tk.StringVar(value="₹ 0.00")
        self.closing_balance = tk.StringVar(value="₹ 0.00")

        self.exp_category = tk.StringVar()
        self.exp_description = tk.StringVar()
        self.exp_amount = tk.DoubleVar(value=0.0)
        self.exp_mode = tk.StringVar(value="Cash")

    def create_ui(self):
        title = tk.Label(
            self.frame, text="DAYBOOK / CASH REGISTER",
            bg="#1565C0", fg="white", font=("Segoe UI", 18, "bold"), pady=10
        )
        title.pack(fill="x")

        top = tk.LabelFrame(self.frame, text="Select Date", font=("Segoe UI", 10, "bold"))
        top.pack(fill="x", padx=10, pady=10)
        tk.Label(top, text="Date (YYYY-MM-DD)").pack(side="left", padx=5)
        date_entry = tk.Entry(top, textvariable=self.entry_date, width=15)
        date_entry.pack(side="left", padx=5)
        date_entry.bind("<Return>", lambda e: self.load_day())
        tk.Button(
            top, text="Load Day", bg="#1565C0", fg="white", width=12,
            font=("Segoe UI", 10, "bold"), command=self.load_day, cursor="hand2"
        ).pack(side="left", padx=10)

        summary = tk.LabelFrame(self.frame, text="Cash Summary", font=("Segoe UI", 10, "bold"))
        summary.pack(fill="x", padx=10, pady=10)

        tk.Label(summary, text="Opening Balance (₹)").grid(row=0, column=0, sticky="w", padx=10, pady=6)
        opening_entry = tk.Entry(summary, textvariable=self.opening_balance, width=15)
        opening_entry.grid(row=0, column=1, padx=10, pady=6, sticky="w")
        opening_entry.bind("<KeyRelease>", lambda e: self._recompute_closing())

        tk.Label(summary, text="+ Cash Sales").grid(row=1, column=0, sticky="w", padx=10, pady=4)
        tk.Label(summary, textvariable=self.cash_sales, fg="green", font=("Segoe UI", 10, "bold")).grid(
            row=1, column=1, sticky="w", padx=10)

        tk.Label(summary, text="Card / UPI / Other sales (info only)").grid(row=1, column=2, sticky="w", padx=10)
        tk.Label(summary, textvariable=self.other_sales, fg="#607D8B").grid(row=1, column=3, sticky="w", padx=10)

        tk.Label(summary, text="- Cash Expenses").grid(row=2, column=0, sticky="w", padx=10, pady=4)
        tk.Label(summary, textvariable=self.cash_expenses, fg="#C62828", font=("Segoe UI", 10, "bold")).grid(
            row=2, column=1, sticky="w", padx=10)

        tk.Label(summary, text="- Cash Paid to Suppliers").grid(row=3, column=0, sticky="w", padx=10, pady=4)
        tk.Label(summary, textvariable=self.cash_paid_suppliers, fg="#C62828", font=("Segoe UI", 10, "bold")).grid(
            row=3, column=1, sticky="w", padx=10)

        tk.Label(summary, text="= Closing Balance").grid(row=4, column=0, sticky="w", padx=10, pady=10)
        tk.Label(summary, textvariable=self.closing_balance, fg="blue", font=("Segoe UI", 13, "bold")).grid(
            row=4, column=1, sticky="w", padx=10)

        tk.Button(
            summary, text="Save Day's Closing Balance", bg="green", fg="white", width=22,
            font=("Segoe UI", 10, "bold"), command=self.save_day, cursor="hand2"
        ).grid(row=4, column=3, padx=10, sticky="w")

        exp_frame = tk.LabelFrame(self.frame, text="Add Expense", font=("Segoe UI", 10, "bold"))
        exp_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(exp_frame, text="Category").grid(row=0, column=0, padx=5, pady=8)
        ttk.Combobox(
            exp_frame, textvariable=self.exp_category, width=15,
            values=["Rent", "Electricity", "Staff", "Transport", "Misc"]
        ).grid(row=0, column=1, padx=5)

        tk.Label(exp_frame, text="Description").grid(row=0, column=2, padx=5)
        tk.Entry(exp_frame, textvariable=self.exp_description, width=25).grid(row=0, column=3, padx=5)

        tk.Label(exp_frame, text="Amount (₹)").grid(row=0, column=4, padx=5)
        tk.Entry(exp_frame, textvariable=self.exp_amount, width=12).grid(row=0, column=5, padx=5)

        tk.Label(exp_frame, text="Mode").grid(row=0, column=6, padx=5)
        ttk.Combobox(
            exp_frame, textvariable=self.exp_mode, width=10, state="readonly",
            values=["Cash", "Bank", "UPI"]
        ).grid(row=0, column=7, padx=5)

        tk.Button(
            exp_frame, text="Add", bg="green", fg="white", width=10,
            command=self.add_expense, cursor="hand2"
        ).grid(row=0, column=8, padx=10)

        list_frame = tk.Frame(self.frame)
        list_frame.pack(fill="both", expand=True, padx=10, pady=10)

        columns = ("Category", "Description", "Amount", "Mode")
        self.expenseTable = ttk.Treeview(list_frame, columns=columns, show="headings", height=8, style="ERP.Treeview")
        for c in columns:
            self.expenseTable.heading(c, text=c)
            self.expenseTable.column(c, width=140, anchor="center")

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.expenseTable.yview)
        self.expenseTable.configure(yscrollcommand=scrollbar.set)
        self.expenseTable.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def load_day(self):
        date = self.entry_date.get().strip()
        if not date:
            ui_popups.show_error(self.frame, "Error", "Enter a date.")
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            # Opening balance: this day's already-saved value if it
            # exists, else auto-suggest the most recent EARLIER day's
            # saved closing balance, else 0 for a brand-new first day.
            cur.execute("SELECT opening_balance FROM daybook WHERE entry_date=?", (date,))
            row = cur.fetchone()
            if row:
                self.opening_balance.set(row[0])
            else:
                cur.execute(
                    "SELECT closing_balance FROM daybook WHERE entry_date < ? ORDER BY entry_date DESC LIMIT 1",
                    (date,)
                )
                prev = cur.fetchone()
                self.opening_balance.set(prev[0] if prev else 0.0)

            cur.execute(
                "SELECT COALESCE(SUM(received_amt),0) FROM sales WHERE bill_date=? AND payment_mode='Cash'",
                (date,)
            )
            self._cash_sales_val = cur.fetchone()[0] or 0.0

            cur.execute(
                "SELECT COALESCE(SUM(total),0) FROM sales WHERE bill_date=? AND payment_mode != 'Cash'",
                (date,)
            )
            other_sales_val = cur.fetchone()[0] or 0.0

            cur.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE expense_date=? AND payment_mode='Cash'",
                (date,)
            )
            self._cash_expenses_val = cur.fetchone()[0] or 0.0

            cur.execute(
                "SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE pay_date=? AND payment_mode='Cash'",
                (date,)
            )
            self._cash_paid_val = cur.fetchone()[0] or 0.0

            self.cash_sales.set(f"₹ {self._cash_sales_val:,.2f}")
            self.other_sales.set(f"₹ {other_sales_val:,.2f}")
            self.cash_expenses.set(f"₹ {self._cash_expenses_val:,.2f}")
            self.cash_paid_suppliers.set(f"₹ {self._cash_paid_val:,.2f}")

            self._recompute_closing()
            self._load_expense_list(cur, date)
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", str(e))
        finally:
            con.close()

    def _recompute_closing(self):
        try:
            opening = self.opening_balance.get()
        except tk.TclError:
            opening = 0.0
        closing = opening + self._cash_sales_val - self._cash_expenses_val - self._cash_paid_val
        self._closing_val = closing
        self.closing_balance.set(f"₹ {closing:,.2f}")

    def _load_expense_list(self, cur, date):
        self.expenseTable.delete(*self.expenseTable.get_children())
        cur.execute(
            "SELECT category, description, amount, payment_mode FROM expenses WHERE expense_date=? ORDER BY id",
            (date,)
        )
        for category, description, amount, mode in cur.fetchall():
            self.expenseTable.insert("", "end", values=(category, description, f"₹ {amount:.2f}", mode))

    def add_expense(self):
        date = self.entry_date.get().strip()
        category = self.exp_category.get().strip()
        description = self.exp_description.get().strip()
        try:
            amount = self.exp_amount.get()
        except tk.TclError:
            amount = 0.0
        mode = self.exp_mode.get()

        if not date:
            ui_popups.show_error(self.frame, "Error", "Enter a date first.")
            return
        if not category:
            ui_popups.show_error(self.frame, "Error", "Select or type a category.")
            return
        if amount <= 0:
            ui_popups.show_error(self.frame, "Error", "Enter a valid amount greater than zero.")
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute(
                "INSERT INTO expenses(expense_date, category, description, amount, payment_mode) "
                "VALUES (?,?,?,?,?)",
                (date, category, description, amount, mode)
            )
            con.commit()
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", str(e))
            return
        finally:
            con.close()

        self.exp_category.set("")
        self.exp_description.set("")
        self.exp_amount.set(0.0)
        self.exp_mode.set("Cash")
        self.load_day()

    def save_day(self):
        date = self.entry_date.get().strip()
        if not date:
            ui_popups.show_error(self.frame, "Error", "Enter a date.")
            return

        self._recompute_closing()
        try:
            opening_val = self.opening_balance.get()
        except tk.TclError:
            opening_val = 0.0

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute(
                "INSERT OR REPLACE INTO daybook(entry_date, opening_balance, closing_balance) "
                "VALUES (?,?,?)",
                (date, opening_val, self._closing_val)
            )
            con.commit()
            ui_popups.show_info(self.frame, "Saved", f"Closing balance for {date} saved: ₹{self._closing_val:,.2f}")
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", str(e))
        finally:
            con.close()
