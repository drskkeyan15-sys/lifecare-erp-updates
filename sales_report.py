import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3

from app_paths import DB_NAME
import ui_popups


class SalesReport:

    def __init__(self, frame):
        self.frame = frame
        
        # வேரியபிள்கள் முதலில் பிரகடனம் செய்யப்படுகின்றன
        self.search_bill = tk.StringVar()
        self.search_customer = tk.StringVar()
        
        # ─── திருத்தம் 1: UI-ஐ முதலில் உருவாக்கிவிட்டு, அதன் பின் தரவுகளை லோடு செய்கிறோம் ───
        self.create_ui()
        self.load_sales()

    # ==========================================
    # UI DESIGN (விடுபட்ட பங்க்ஷன் முறைப்படி பிரிக்கப்பட்டுள்ளது)
    # ==========================================

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="SALES REPORT",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        cols = ("Bill No", "Date", "Customer", "Amount")

        searchFrame = tk.LabelFrame(
            self.frame,
            text="Search Filter",
            font=("Segoe UI", 10, "bold")
        )
        searchFrame.pack(fill="x", padx=10, pady=5)

        tk.Label(searchFrame, text="Bill No").grid(row=0, column=0, padx=5, pady=5)
        
        # என்டர் தட்டினால் தேடும் வசதி
        entry_bill = tk.Entry(searchFrame, textvariable=self.search_bill, width=20)
        entry_bill.grid(row=0, column=1, padx=5)
        entry_bill.bind("<Return>", lambda e: self.search_sales())

        tk.Label(searchFrame, text="Customer").grid(row=0, column=2, padx=5)
        entry_cust = tk.Entry(searchFrame, textvariable=self.search_customer, width=25)
        entry_cust.grid(row=0, column=3, padx=5)
        entry_cust.bind("<Return>", lambda e: self.search_sales())

        tk.Button(
            searchFrame, text="Search", bg="#2E7D32", fg="white", width=12, command=self.search_sales
        ).grid(row=0, column=4, padx=5)

        tk.Button(
            searchFrame, text="Refresh", width=12, command=self.load_sales
        ).grid(row=0, column=5, padx=5)

        # Table Grid Layout
        table_container = tk.Frame(self.frame)
        table_container.pack(fill="both", expand=True, padx=10, pady=5)

        self.salesTable = ttk.Treeview(table_container, columns=cols, show="headings", height=18)
        
        for c in cols:
            self.salesTable.heading(c, text=c)
            w_size = 220 if c == "Customer" else 140
            self.salesTable.column(c, width=w_size, anchor="center")

        scroll = ttk.Scrollbar(table_container, orient="vertical", command=self.salesTable.yview)
        self.salesTable.configure(yscrollcommand=scroll.set)
        
        self.salesTable.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # டபுள் கிளிக் பைண்டிங்
        self.salesTable.bind("<Double-1>", self.view_bill)

        # ─── திருத்தம் 2: லேபிள் அட்ரிபியூட் எரர் வராமல் தடுக்க இங்கேயே பிரகடனம் செய்யப்பட்டுள்ளது ───
        self.lblTotal = tk.Label(
            self.frame,
            text="Total Sales : ₹0.00",
            fg="blue",
            font=("Segoe UI", 12, "bold")
        )
        self.lblTotal.pack(pady=10)

    # ==========================================
    # BACKEND DATA LOAD & SEARCH
    # ==========================================

    def load_sales(self):
        self.salesTable.delete(*self.salesTable.get_children())
        self.search_bill.set("")
        self.search_customer.set("")

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT bill_no, bill_date, customer, total FROM sales ORDER BY id DESC
        """)
        rows = cur.fetchall()
        con.close()

        total = 0.0
        for row in rows:
            self.salesTable.insert("", "end", values=row)
            total += float(row[3] or 0.0)

        self.lblTotal.config(text=f"Total Sales : ₹{total:,.2f}")

    def search_sales(self):
        self.salesTable.delete(*self.salesTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT bill_no, bill_date, customer, total FROM sales
            WHERE bill_no LIKE ? AND customer LIKE ? ORDER BY id DESC
        """, (
            "%" + self.search_bill.get().strip() + "%",
            "%" + self.search_customer.get().strip() + "%"
        ))
        rows = cur.fetchall()
        con.close()

        total = 0.0
        for row in rows:
            self.salesTable.insert("", "end", values=row)
            total += float(row[3] or 0.0)

        self.lblTotal.config(text=f"Total Sales : ₹{total:,.2f}")

    # ==========================================
    # VIEW BILL DETAILS (திருத்தப்பட்ட அசல் பங்க்ஷன்)
    # ==========================================

    def view_bill(self, event=None):
        selected = self.salesTable.focus()
        if not selected:
            return

        values = self.salesTable.item(selected)["values"]
        bill_no = values[0]

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT medicine, qty, sale, total FROM sales_items WHERE bill_no=?
        """, (bill_no,))
        rows = cur.fetchall()
        con.close()

        if not rows:
            ui_popups.show_info(self.frame, "Bill", "No items found for this bill.")
            return

        text = f"Bill No : {bill_no}\nCustomer : {values[2]}\nDate : {values[1]}\n"
        text += "-----------------------------------------\n\n"

        for r in rows:
            text += f"💊 {r[0]}\n   Qty: {r[1]}  |  Price: ₹{r[2]:.2f}  |  Total: ₹{r[3]:.2f}\n\n"

        text += "-----------------------------------------\n"
        text += f"Grand Total : ₹{float(values[3]):,.2f}"

        ui_popups.show_info(self.frame, "Bill Details", text)