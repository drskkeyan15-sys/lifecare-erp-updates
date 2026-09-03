from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3

from app_paths import DB_NAME
import ui_style
import ui_popups


class SalesReturn:

    def __init__(self, frame):
        self.frame = frame

        self.bill_no = tk.StringVar()
        self.customer = tk.StringVar()
        self.return_qty = tk.IntVar(value=1)

        self.create_ui()

    # =====================================
    # UI
    # =====================================

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="SALES RETURN",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        top = tk.LabelFrame(
            self.frame,
            text="Search Bill",
            font=("Segoe UI", 10, "bold")
        )
        top.pack(fill="x", padx=10, pady=10)

        tk.Label(top, text="Bill No").grid(row=0, column=0, padx=5, pady=5)
        
        # என்ட்ரி பாக்ஸில் என்டர் தட்டினால் தேடும் வசதி இணைக்கப்பட்டுள்ளது
        entry_bill = tk.Entry(top, textvariable=self.bill_no, width=25)
        entry_bill.grid(row=0, column=1, padx=5)
        entry_bill.bind("<Return>", lambda e: self.search_bill())

        tk.Button(
            top, text="Search", bg="green", fg="white", width=12, command=self.search_bill
        ).grid(row=0, column=2, padx=5)

        tk.Label(top, text="Customer").grid(row=0, column=3, padx=5)
        tk.Entry(top, textvariable=self.customer, state="readonly", width=30).grid(row=0, column=4, padx=5)

        table = tk.Frame(self.frame)
        table.pack(fill="both", expand=True, padx=10, pady=10)

        cols = ("Medicine", "Batch", "Qty", "Price", "Total")
        self.returnTable = ttk.Treeview(table, columns=cols, show="headings", height=15, style="ERP.Treeview")

        for c in cols:
            self.returnTable.heading(c, text=c)
            w_size = 200 if c == "Medicine" else 120
            self.returnTable.column(c, width=w_size, anchor="center")

        self.returnTable.pack(fill="both", expand=True)
        self.returnTable.bind("<<TreeviewSelect>>", self.select_item)

        bottom = tk.Frame(self.frame)
        bottom.pack(fill="x", padx=10, pady=10)

        tk.Label(bottom, text="Return Qty").pack(side="left", padx=10)
        
        txt_return_qty = tk.Entry(bottom, textvariable=self.return_qty, width=10)
        txt_return_qty.pack(side="left")
        txt_return_qty.bind("<Return>", lambda e: self.return_item())

        tk.Button(
            bottom, text="Return Item", bg="red", fg="white", width=15, command=self.return_item
        ).pack(side="left", padx=20)

    # =====================================
    # FUNCTIONS
    # =====================================

    def search_bill(self):
        self.returnTable.delete(*self.returnTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()

        bill = self.bill_no.get().strip()
        self.bill_no.set(bill)

        cur.execute("SELECT customer FROM sales WHERE bill_no=?", (bill,))
        row = cur.fetchone()

        if not row:
            ui_popups.show_error(self.frame, "Error", "Bill Not Found")
            self.customer.set("")
            con.close()
            return

        self.customer.set(row[0] or "Walk-in Customer")

        cur.execute("""
            SELECT medicine, batch, qty, sale, total FROM sales_items WHERE bill_no=?
        """, (bill,))
        rows = cur.fetchall()

        for r in rows:
            # clean_row() so a NULL column shows blank instead of the
            # literal text "None" - see ui_style.clean_row().
            self.returnTable.insert("", "end", values=ui_style.clean_row(r))

        con.close()

    def select_item(self, event=None):
        selected = self.returnTable.focus()
        if not selected:
            return

        values = self.returnTable.item(selected)["values"]
        self.selected_medicine = values[0]
        self.selected_batch = values[1]
        self.sold_qty = int(values[2]) 
        self.price = float(values[3])

    def return_item(self):
        selected = self.returnTable.focus()
        if not selected:
            ui_popups.show_error(self.frame, "Error", "Select a medicine from the table first.")
            return

        values = self.returnTable.item(selected)["values"]
        medicine = values[0]
        batch = values[1]
        sold_qty = int(values[2])

        try:
            qty = self.return_qty.get()
        except:
            ui_popups.show_error(self.frame, "Error", "Invalid Number Format")
            return

        if qty <= 0:
            ui_popups.show_error(self.frame, "Error", "Invalid Return Quantity")
            return

        if qty > sold_qty:
            ui_popups.show_error(self.frame, "Error", "Return quantity is greater than sold quantity.")
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            # 1. Increase master inventory stock
            cur.execute("""
                UPDATE medicine_master SET stock = stock + ? WHERE name=? AND batch=?
            """, (qty, medicine, batch))

            # 2. Reduce sales quantity or delete row if fully returned
            new_qty = sold_qty - qty
            if new_qty == 0:
                cur.execute("""
                    DELETE FROM sales_items WHERE bill_no=? AND medicine=? AND batch=?
                """, (self.bill_no.get(), medicine, batch))
            else:
                cur.execute("""
                    UPDATE sales_items SET qty=?, total=? * sale WHERE bill_no=? AND medicine=? AND batch=?
                """, (new_qty, new_qty, self.bill_no.get(), medicine, batch))

            # 3. Log into sales_return audit table
            cur.execute("""
            INSERT INTO sales_return (return_date, bill_no, medicine, batch, qty, price, total, customer)
            VALUES(?,?,?,?,?,?,?,?)
            """, (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                self.bill_no.get(),
                medicine,
                batch,
                qty,
                values[3],
                round(qty * float(values[3]), 2),
                self.customer.get()
            ))

            # 4. Calculate fresh bill total from remaining items
            cur.execute("SELECT IFNULL(SUM(total),0) FROM sales_items WHERE bill_no=?", (self.bill_no.get(),))
            new_total = round(cur.fetchone()[0], 2)

            # ─── திருத்தம்: Reports பக்க கணக்கீடுகளுக்காக subtotal மற்றும் total இரண்டையும் அப்டேட் செய்கிறோம் ───
            cur.execute("""
                UPDATE sales SET subtotal=?, total=? WHERE bill_no=?
            """, (new_total, new_total, self.bill_no.get()))

            con.commit()
            ui_popups.show_info(self.frame, "Success", "Item Returned Successfully.")
            self.return_qty.set(1)

        except Exception as e:
            con.rollback()
            ui_popups.show_error(self.frame, "Database Error", str(e))
        finally:
            con.close()
            self.search_bill()