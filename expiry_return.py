import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from datetime import datetime
from app_paths import DB_NAME
from icon_loader import get_icon
import ui_style
import ui_popups


# ==========================================
# SHARED DATA-LAYER HELPERS
# ==========================================
# Pulled out of the ExpiryReturn class so stock_alerts_gui.py's
# Distributor Return tab (near-expiry stock grouped by supplier, fed by
# Smart Alerts) can record a return the exact same way this screen's own
# "Process Return" button does, instead of a second copy of the
# stock-decrement + credit-note-insert logic drifting out of sync.

def ensure_expiry_returns_table(db_name=None):
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expiry_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            credit_note TEXT,
            supplier TEXT,
            medicine TEXT,
            batch TEXT,
            qty INTEGER,
            return_date TEXT
        )
    """)
    con.commit()
    con.close()


def record_expiry_return(credit_note, supplier, medicine, batch, qty, db_name=None):
    """
    Decrements medicine_master.stock and inserts one expiry_returns row,
    in a single transaction. Raises ValueError with a user-facing message
    on any validation failure (medicine not found / qty exceeds stock) -
    callers show that message in a messagebox rather than duplicating the
    checks themselves.
    """
    db_name = db_name or DB_NAME
    ensure_expiry_returns_table(db_name)

    if qty <= 0 or not supplier or not medicine or not credit_note:
        raise ValueError("Please fill all required details correctly.")

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    try:
        cur.execute("SELECT stock FROM medicine_master WHERE name=?", (medicine,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Medicine not found in master.")

        current_stock = row[0]
        if qty > current_stock:
            raise ValueError(f"Return quantity cannot exceed available stock ({current_stock}).")

        cur.execute("UPDATE medicine_master SET stock = stock - ? WHERE name=?", (qty, medicine))
        cur.execute("""
            INSERT INTO expiry_returns (credit_note, supplier, medicine, batch, qty, return_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (credit_note, supplier, medicine, batch, qty, datetime.now().strftime("%Y-%m-%d")))
        con.commit()
    except ValueError:
        con.rollback()
        raise
    except Exception as e:
        con.rollback()
        raise ValueError(str(e))
    finally:
        con.close()


class ExpiryReturn:

    def __init__(self, frame):
        self.frame = frame
        self.create_variables()
        self.create_ui()
        self.load_suppliers()
        self.load_medicines()
        self.load_returns()

    def create_variables(self):
        self.supplier_name = tk.StringVar()
        self.medicine_name = tk.StringVar()
        self.batch_no = tk.StringVar()
        self.qty = tk.IntVar(value=1)
        self.credit_note = tk.StringVar(value=f"CN-{datetime.now().strftime('%Y%m%d%H%M')}")

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="EXPIRY / NEAR-EXPIRY RETURN TO SUPPLIER",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        # ---------------- Form Frame ----------------
        form_frame = tk.LabelFrame(
            self.frame,
            text="Return Expired Medicines & Get Credit Note",
            font=("Segoe UI", 10, "bold")
        )
        form_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(form_frame, text="Supplier Name").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.cmbSupplier = ttk.Combobox(form_frame, textvariable=self.supplier_name, width=25, state="normal")
        self.cmbSupplier.grid(row=0, column=1, padx=5, pady=5)

        tk.Label(form_frame, text="Medicine Name").grid(row=0, column=2, padx=5, pady=5, sticky="w")
        self.cmbMedicine = ttk.Combobox(form_frame, textvariable=self.medicine_name, width=25, state="normal")
        self.cmbMedicine.grid(row=0, column=3, padx=5, pady=5)

        tk.Label(form_frame, text="Batch No").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form_frame, textvariable=self.batch_no, width=27).grid(row=1, column=1, padx=5, pady=5)

        tk.Label(form_frame, text="Return Quantity").grid(row=1, column=2, padx=5, pady=5, sticky="w")
        self.txtQty = tk.Entry(form_frame, textvariable=self.qty, width=27)
        self.txtQty.grid(row=1, column=3, padx=5, pady=5)
        self.txtQty.bind("<Return>", lambda e: self.process_return())

        # ERP-wide keyboard-nav pass (Aug 2026): both boxes were static,
        # unfiltered dropdowns before - Supplier had NO selection handler
        # at all, Medicine only reacted to a mouse click. Now: typing
        # narrows both live, selecting a Supplier (keyboard or mouse)
        # advances to Medicine, and selecting/confirming a Medicine
        # (which auto-fills Batch No, same as before) advances to
        # Quantity - Batch No is skipped since it's already auto-filled,
        # matching the same fast-entry pattern used in Stock Adjustment.
        ui_style.bind_search_combo(
            self.cmbSupplier,
            on_filter=self._filter_supplier_dropdown,
            on_confirm=lambda e=None: bool(self.supplier_name.get().strip()),
            next_widget=self.cmbMedicine,
        )
        ui_style.bind_search_combo(
            self.cmbMedicine,
            on_filter=self._filter_medicine_dropdown,
            on_confirm=self.fetch_medicine_info,
            next_widget=self.txtQty,
        )

        tk.Label(form_frame, text="Credit Note No").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form_frame, textvariable=self.credit_note, width=27).grid(row=2, column=1, padx=5, pady=5)

        tk.Button(
            form_frame,
            text=" Process Return & Get Credit Note",
            image=get_icon("refresh"),
            compound="left",
            bg="red",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            padx=16, pady=6,
            command=self.process_return
        ).grid(row=3, column=1, columnspan=2, pady=15)

        # ---------------- Table Frame ----------------
        table_frame = tk.Frame(self.frame)
        table_frame.pack(fill="both", expand=True, padx=10, pady=10)

        columns = ("ID", "Credit Note", "Supplier", "Medicine", "Batch", "Qty", "Date")
        self.returnTable = ttk.Treeview(table_frame, columns=columns, show="headings", height=12, style="ERP.Treeview")

        for c in columns:
            self.returnTable.heading(c, text=c)
            self.returnTable.column(c, width=120, anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.returnTable.yview)
        self.returnTable.configure(yscrollcommand=scrollbar.set)
        self.returnTable.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def load_suppliers(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            # Table is `supplier` (singular) per database.py's schema -
            # this was querying the never-existent `suppliers`, so the
            # dropdown was silently empty (caught by except below) and
            # supplier name always had to be typed by hand.
            cur.execute("SELECT name FROM supplier")
            self._supplier_names = [r[0] for r in cur.fetchall()]
        except Exception:
            self._supplier_names = []
        con.close()
        self.cmbSupplier["values"] = self._supplier_names

    def load_medicines(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute("SELECT name FROM medicine_master WHERE stock > 0")
            self._medicine_names = [r[0] for r in cur.fetchall()]
        except Exception:
            self._medicine_names = []
        con.close()
        self.cmbMedicine["values"] = self._medicine_names

    def _filter_supplier_dropdown(self, typed_text):
        typed = typed_text.lower()
        self.cmbSupplier["values"] = (
            self._supplier_names if not typed
            else [n for n in self._supplier_names if typed in n.lower()]
        )

    def _filter_medicine_dropdown(self, typed_text):
        typed = typed_text.lower()
        self.cmbMedicine["values"] = (
            self._medicine_names if not typed
            else [n for n in self._medicine_names if typed in n.lower()]
        )

    def fetch_medicine_info(self, event=None):
        """Also used as bind_search_combo()'s on_confirm for cmbMedicine -
        returns True (advance to Quantity) only when the name matches a
        real medicine with a batch on file."""
        med = self.medicine_name.get().strip()
        if not med:
            return False
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT batch FROM medicine_master WHERE name=?", (med,))
        row = cur.fetchone()
        con.close()
        if row:
            self.batch_no.set(row[0] or "")
            return True
        return False

    def load_returns(self):
        self.returnTable.delete(*self.returnTable.get_children())
        ensure_expiry_returns_table(DB_NAME)
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute("SELECT id, credit_note, supplier, medicine, batch, qty, return_date FROM expiry_returns ORDER BY id DESC")
            for r in cur.fetchall():
                # clean_row() so a NULL column shows blank instead of
                # the literal text "None" - see ui_style.clean_row().
                self.returnTable.insert("", "end", values=ui_style.clean_row(r))
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", str(e))
        finally:
            con.close()

    def process_return(self):
        sup = self.supplier_name.get().strip()
        med = self.medicine_name.get().strip()
        bat = self.batch_no.get().strip()
        q = self.qty.get()
        cn = self.credit_note.get().strip()

        try:
            record_expiry_return(cn, sup, med, bat, q, DB_NAME)
        except ValueError as e:
            ui_popups.show_error(self.frame, "Error", str(e))
            return

        ui_popups.show_info(self.frame, "Success", f"Expired medicines returned to {sup}!\nCredit Note No: {cn} generated.")
        self.qty.set(1)
        self.credit_note.set(f"CN-{datetime.now().strftime('%Y%m%d%H%M')}")
        self.load_returns()
        self.load_medicines()
        # ERP-wide keyboard-nav pass (Aug 2026): return focus to Medicine
        # for continuous entry (e.g. returning several expired items from
        # the same supplier one after another).
        self.cmbMedicine.focus_set()
        self.cmbMedicine.select_range(0, tk.END)