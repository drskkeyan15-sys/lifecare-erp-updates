import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from datetime import datetime

from app_paths import DB_NAME
import ui_style
import ui_popups


class PurchaseReturn:

    def __init__(self, frame):
        self.frame = frame

        self.bill_no = tk.StringVar()
        self.supplier = tk.StringVar()
        self.return_qty = tk.IntVar(value=1)

        self.create_ui()
        self.load_purchase_bills()  # விண்டோ ஓபன் ஆனதும் பில் எண்களை லோடு செய்ய

    # =====================================
    # UI
    # =====================================

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="PURCHASE RETURN",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        top = tk.LabelFrame(
            self.frame,
            text="Search Purchase Bill",
            font=("Segoe UI", 10, "bold")
        )
        top.pack(fill="x", padx=10, pady=10)

        tk.Label(top, text="Bill No").grid(row=0, column=0, padx=5)

        # 💡 Entry-க்கு பதிலாக Combobox பயன்படுத்தப்பட்டுள்ளது
        self.cmbBillNo = ttk.Combobox(
            top,
            textvariable=self.bill_no,
            width=18,
            state="normal"
        )
        self.cmbBillNo.grid(row=0, column=1, padx=5)
        self.cmbBillNo.bind("<KeyRelease>", self._filter_bill_dropdown)
        self.cmbBillNo.bind("<Return>", lambda e: self.search_bill())
        self.cmbBillNo.bind("<<ComboboxSelected>>", lambda e: self.search_bill())

        tk.Button(
            top, text="Search", bg="green", fg="white", command=self.search_bill
        ).grid(row=0, column=2, padx=5)

        tk.Label(top, text="Supplier").grid(row=0, column=3, padx=5)
        tk.Entry(top, textvariable=self.supplier, state="readonly", width=30).grid(row=0, column=4, padx=5)

        table = tk.Frame(self.frame)
        table.pack(fill="both", expand=True, padx=10, pady=10)

        cols = ("Medicine", "Batch", "Qty", "Purchase", "Total")
        self.returnTable = ttk.Treeview(table, columns=cols, show="headings", height=15, style="ERP.Treeview")

        for c in cols:
            self.returnTable.heading(c, text=c)
            w_size = 200 if c == "Medicine" else 120
            self.returnTable.column(c, width=w_size, anchor="center")

        self.returnTable.pack(fill="both", expand=True)
        self.returnTable.bind("<<TreeviewSelect>>", self.select_item)

        # ERP-wide keyboard-nav pass (Aug 2026): after search_bill() (run
        # by cmbBillNo's <Return>/<<ComboboxSelected>> binds above) loads
        # this bill's items into returnTable, Down/Enter jumps straight
        # into the table's first row - see ui_style.bind_search_to_grid()'s
        # docstring. Chained AFTER the existing <Return> bind (add="+"),
        # so a real bill search always runs first, then the jump lands on
        # whatever it just loaded.
        ui_style.bind_search_to_grid(self.cmbBillNo, self.returnTable)

        bottom = tk.Frame(self.frame)
        bottom.pack(fill="x", padx=10, pady=10)

        tk.Label(bottom, text="Return Qty").pack(side="left", padx=10)

        txt_return_qty = tk.Entry(bottom, textvariable=self.return_qty, width=10)
        txt_return_qty.pack(side="left")
        txt_return_qty.bind("<Return>", lambda e: self.return_item())

        tk.Button(
            bottom, text="Return", bg="red", fg="white", width=15, command=self.return_item
        ).pack(side="left", padx=20)

    # =====================================
    # FUNCTIONS
    # =====================================

    def load_purchase_bills(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute("SELECT DISTINCT bill_no FROM purchase")
            self._bill_numbers = [row[0] for row in cur.fetchall() if row[0]]
            self.cmbBillNo["values"] = self._bill_numbers
        except Exception as e:
            self._bill_numbers = []
            ui_popups.show_error(self.frame, "Error", f"பில் எண்களை எடுப்பதில் பிழை:\n{e}")
        finally:
            con.close()

    def _filter_bill_dropdown(self, event):
        # ERP-wide keyboard-nav pass (Aug 2026): this box previously
        # never filtered as you typed - the dropdown always showed every
        # bill number regardless of what was typed. Guards nav/confirm
        # keys the same way every other live-filtered box in the app
        # does, so arrowing/confirming never gets treated as "new text".
        if event.keysym in ("Up", "Down", "Left", "Right", "Return", "Escape", "Tab"):
            return
        typed = self.bill_no.get().lower()
        self.cmbBillNo["values"] = (
            self._bill_numbers if not typed
            else [b for b in self._bill_numbers if typed in b.lower()]
        )

    def search_bill(self):
        self.returnTable.delete(*self.returnTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()

        bill = self.bill_no.get().strip()
        self.bill_no.set(bill)

        if not bill:
            con.close()
            return

        cur.execute("""
            SELECT supplier FROM purchase WHERE bill_no=? LIMIT 1
        """, (bill,))
        row = cur.fetchone()

        if not row:
            ui_popups.show_error(self.frame, "Error", "Purchase Bill Not Found")
            self.supplier.set("")
            con.close()
            return

        self.supplier.set(row[0] or "Unknown Supplier")

        # பர்ச்சேஸ் டேபிளிலிருந்து அசல் விபரங்களை எடுக்கிறது
        cur.execute("""
            SELECT medicine, batch, qty, purchase, total FROM purchase WHERE bill_no=?
        """, (bill,))
        rows = cur.fetchall()

        for r in rows:
            self.returnTable.insert("", "end", values=r)

        con.close()

    def select_item(self, event=None):
        selected = self.returnTable.focus()
        if not selected:
            return

        values = self.returnTable.item(selected)["values"]
        self.selected_medicine = values[0]
        self.selected_batch = values[1]
        self.purchase_qty = int(values[2])
        self.purchase_price = float(values[3])

    def return_item(self):
        selected = self.returnTable.focus()
        if not selected:
            ui_popups.show_error(self.frame, "Error", "Select a medicine from the table first.")
            return

        values = self.returnTable.item(selected)["values"]
        medicine = values[0]
        batch = values[1]
        purchase_qty = int(values[2])

        try:
            qty = self.return_qty.get()
        except:
            ui_popups.show_error(self.frame, "Error", "Invalid Number Format")
            return

        if qty <= 0:
            ui_popups.show_error(self.frame, "Error", "Invalid Return Quantity")
            return

        if qty > purchase_qty:
            ui_popups.show_error(self.frame, "Error", "Return quantity exceeds purchased quantity.")
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            # 1. Master stock-ஐ குறைப்பது
            cur.execute("""
                UPDATE medicine_master SET stock = stock - ? WHERE name=? AND batch=?
            """, (qty, medicine, batch))

            # 2. Purchase டேபிளை அப்டேட் செய்வது அல்லது முழுமையாக நீக்குவது
            new_qty = purchase_qty - qty
            if new_qty == 0:
                cur.execute("""
                    DELETE FROM purchase WHERE bill_no=? AND medicine=? AND batch=?
                """, (self.bill_no.get(), medicine, batch))
            else:
                cur.execute("""
                    UPDATE purchase SET qty=?, total=? * purchase WHERE bill_no=? AND medicine=? AND batch=?
                """, (new_qty, new_qty, self.bill_no.get(), medicine, batch))

            # 3. Purchase Return ஆடித்த டேபிளில் லாக் செய்வது (Table இல்லையெனில் ஆட்டோவாக உருவாக்கும்)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS purchase_return (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    return_date TEXT,
                    bill_no TEXT,
                    supplier TEXT,
                    medicine TEXT,
                    batch TEXT,
                    qty INTEGER,
                    purchase REAL,
                    total REAL
                )
            """)
            
            cur.execute("""
                INSERT INTO purchase_return (return_date, bill_no, supplier, medicine, batch, qty, purchase, total)
                VALUES(?,?,?,?,?,?,?,?)
            """, (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                self.bill_no.get(),
                self.supplier.get(),
                medicine,
                batch,
                qty,
                values[3],
                round(qty * float(values[3]), 2)
            ))

            con.commit()
            ui_popups.show_info(self.frame, "Success", "Purchase Return Saved Successfully.")
            self.return_qty.set(1)

        except Exception as e:
            con.rollback()
            ui_popups.show_error(self.frame, "Database Error", str(e))
        finally:
            con.close()
            self.search_bill()
            self.load_purchase_bills()