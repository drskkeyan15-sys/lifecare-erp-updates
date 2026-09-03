import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from datetime import datetime, timedelta

# BUG FIX: this used to be a bare "pharmacy.db" literal, resolved against
# whatever the current working directory happened to be when the app was
# launched - fine in development (CWD == script folder) but silently
# wrong once packaged as a PyInstaller .exe launched from a shortcut with
# a different working directory (same class of bug app_paths.py's own
# docstring exists to prevent). Every other screen already imports
# DB_NAME from here; this one just hadn't been switched over yet.
from app_paths import DB_NAME
from pricing_utils import get_pack_multiplier
import ui_style
import theme
import ui_popups


class Reports:
    def __init__(self, frame):
        self.frame = frame

        self.from_date = tk.StringVar(
            value=datetime.now().strftime("%Y-%m-%d")
        )
        self.to_date = tk.StringVar(
            value=datetime.now().strftime("%Y-%m-%d")
        )

        self.total_bills = tk.StringVar(value="0")
        self.total_sales = tk.StringVar(value="0.00")
        self.total_profit = tk.StringVar(value="0.00")

        self.migrate_schema()
        self.create_ui()

    def migrate_schema(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute("ALTER TABLE sales ADD COLUMN discount REAL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        con.commit()
        con.close()

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="REPORTS",
            bg=theme.PRIMARY,
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        # ==========================
        # Search Frame
        # ==========================
        search = tk.LabelFrame(
            self.frame,
            text="Date Filter",
            font=("Segoe UI", 10, "bold")
        )
        search.pack(fill="x", padx=10, pady=10)

        tk.Label(search, text="From").grid(row=0, column=0, padx=5, pady=5)
        tk.Entry(search, textvariable=self.from_date, width=15).grid(row=0, column=1)

        tk.Label(search, text="To").grid(row=0, column=2, padx=5)
        tk.Entry(search, textvariable=self.to_date, width=15).grid(row=0, column=3)

        tk.Button(
            search, text="Search", bg="green", fg="white", width=12,
            command=self.search_sales
        ).grid(row=0, column=4, padx=5)

        tk.Button(
            search, text="Refresh", width=12,
            command=self.load_sales
        ).grid(row=0, column=5, padx=5)

        # ==========================
        # Summary
        # ==========================
        summary = tk.Frame(self.frame)
        summary.pack(fill="x", padx=10, pady=10)

        tk.Label(summary, text="Bills / Count :", font=("Segoe UI", 11, "bold")).pack(side="left", padx=10)
        tk.Label(summary, textvariable=self.total_bills, fg="blue", font=("Segoe UI", 11, "bold")).pack(side="left")

        tk.Label(summary, text="Sales / Total :", font=("Segoe UI", 11, "bold")).pack(side="left", padx=40)
        tk.Label(summary, textvariable=self.total_sales, fg="green", font=("Segoe UI", 11, "bold")).pack(side="left")

        tk.Label(summary, text="Profit :", font=("Segoe UI", 11, "bold")).pack(side="left", padx=40)
        tk.Label(summary, textvariable=self.total_profit, fg="red", font=("Segoe UI", 11, "bold")).pack(side="left")

        # ==========================
        # Report Buttons
        # ==========================
        btn = tk.Frame(self.frame)
        btn.pack(fill="x", padx=10, pady=10)

        tk.Button(btn, text="Daily Sales", width=18, command=self.daily_sales).pack(side="left", padx=5)
        tk.Button(btn, text="Monthly Sales", width=18, command=self.monthly_sales).pack(side="left", padx=5)
        tk.Button(btn, text="Profit", width=18, command=self.profit_report).pack(side="left", padx=5)
        tk.Button(btn, text="Stock", width=18, command=self.stock_report).pack(side="left", padx=5)
        tk.Button(btn, text="Low Stock", width=18, command=self.low_stock).pack(side="left", padx=5)
        tk.Button(btn, text="Expiry", width=18, command=self.expiry_report).pack(side="left", padx=5)
        tk.Button(btn, text="Doctor / Patient", width=18, command=self.doctor_patient_report).pack(side="left", padx=5)
        tk.Button(btn, text="H1 Register", width=18, command=self.h1_register_report).pack(side="left", padx=5)
        tk.Button(btn, text="Slow-Moving Stock", width=18, command=self.slow_moving_report).pack(side="left", padx=5)
        tk.Button(btn, text="Schedule X Register", bg=theme.ACCENT_SCHEDULE_X, fg="white", width=18, command=self.schedule_x_register_report).pack(side="left", padx=5)
        tk.Button(btn, text="Prescription Register", bg=theme.ACCENT_RX_REGISTER, fg="white", width=18, command=self.prescription_register_report).pack(side="left", padx=5)
        tk.Button(btn, text="Cold Chain Stock", bg=theme.ACCENT_COLD_CHAIN, fg="white", width=18, command=self.cold_chain_report).pack(side="left", padx=5)
        # Missing Generic/Composition Finder (Aug 2026) - a data-quality
        # report, not a sales/financial one, added alongside the DDI
        # Safety Checker framework (ddi_checker.py) and Predictive
        # Inventory work this same round. Both of those features silently
        # SKIP any medicine with no resolvable generic/composition - this
        # report is how the pharmacist finds and closes that gap, instead
        # of only discovering it one medicine at a time via a missed DDI
        # warning or substitute suggestion.
        tk.Button(btn, text="Missing Generic", bg=theme.ACCENT_NEUTRAL, fg="white", width=18, command=self.missing_generic_report).pack(side="left", padx=5)

        # ==========================
        # Report Table (Excel-grid look, same as Medicine Master / Stock -
        # ui_style.make_excel_sheet() gives the colored header, cell
        # borders and zebra rows; _SheetTreeAdapter above lets every
        # existing report method below keep using the old
        # .delete(*.get_children()) / .insert("", "end", values=row) calls
        # unchanged)
        # ==========================
        table_container = tk.Frame(self.frame, bg=theme.SURFACE_PAGE)
        table_container.pack(fill="both", expand=True, padx=10, pady=10)

        cols = ("Bill No", "Date", "Customer", "Subtotal", "Discount", "Total")
        # text_columns=cols here so the very first (default Sales) table
        # left-aligns like every other report does once it switches
        # headers via update_table_headers() -> _SheetTreeAdapter's
        # __setitem__ (which left-aligns everything on every header
        # change) - keeps the look consistent instead of the first
        # screen defaulting to make_excel_sheet's right-align-everything
        # fallback.
        # 2026-08-30: switched from make_excel_sheet() (tksheet) to
        # make_plain_sheet() (plain ttk.Treeview) - see medicine_master.py's
        # ui_style.PlainSheet docstring for the full rationale.
        # SheetTreeAdapter below only calls methods PlainSheet answers
        # to identically (set_sheet_data/insert_row/highlight_rows/
        # column_width/headers/align_columns/get_selected_rows), so it
        # keeps working unchanged on top of either grid technology.
        sheet = ui_style.make_plain_sheet(table_container, cols, text_columns=cols)
        # fill="both" (like Medicine Master/Stock) so the table fills the
        # available screen space instead of looking like a small,
        # collapsed strip. SheetTreeAdapter's stretch-last-column logic
        # (see ui_style.py) is what stops that stretch from leaving a
        # blank header-colored block trailing past the last real column -
        # the last column widens to soak up the leftover space instead.
        sheet.pack(side="left", fill="both", expand=True)
        sheet.enable_bindings(*ui_style.READONLY_BINDINGS)
        ui_style.enable_row_highlight_on_select(sheet)
        self.reportTable = ui_style.SheetTreeAdapter(sheet, columns=cols)

        self.load_sales()

    def update_table_headers(self, cols):
        """ஸ்க்ரோல்பார் உடையாமல் டேபிள் ஹெடர்களை மாற்றுவதற்கான முறை"""
        self.reportTable.delete(*self.reportTable.get_children())
        self.reportTable["columns"] = cols

    def load_sales(self):
        cols = ("Bill No", "Date", "Customer", "Subtotal", "Discount", "Total")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT bill_no, bill_date, customer, subtotal, discount, total
            FROM sales
            ORDER BY bill_date DESC
        """)
        rows = cur.fetchall()
        con.close()

        total_sales = 0
        for row in rows:
            self.reportTable.insert("", "end", values=row)
            try:
                total_sales += float(row[5])
            except:
                pass

        self.total_bills.set(str(len(rows)))
        self.total_sales.set(f"₹ {total_sales:,.2f}")
        self.total_profit.set("₹ 0.00")

    def search_sales(self):
        cols = ("Bill No", "Date", "Customer", "Subtotal", "Discount", "Total")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT bill_no, bill_date, customer, subtotal, discount, total
            FROM sales
            WHERE bill_date BETWEEN ? AND ?
            ORDER BY bill_date
        """, (self.from_date.get(), self.to_date.get()))
        rows = cur.fetchall()
        con.close()

        total_sales = 0
        for row in rows:
            self.reportTable.insert("", "end", values=row)
            try:
                total_sales += float(row[5])
            except:
                pass

        self.total_bills.set(str(len(rows)))
        self.total_sales.set(f"₹ {total_sales:,.2f}")
        self.total_profit.set("₹ 0.00")

    def daily_sales(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self.from_date.set(today)
        self.to_date.set(today)

        cols = ("Bill No", "Date", "Customer", "Subtotal", "Discount", "Total")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT bill_no, bill_date, customer, subtotal, discount, total
            FROM sales
            WHERE bill_date=?
            ORDER BY bill_no
        """, (today,))
        rows = cur.fetchall()
        con.close()

        total_sales = 0
        for row in rows:
            self.reportTable.insert("", "end", values=row)
            try:
                total_sales += float(row[5])
            except:
                pass

        self.total_bills.set(str(len(rows)))
        self.total_sales.set(f"₹ {total_sales:,.2f}")
        self.total_profit.set("₹ 0.00")

    def monthly_sales(self):
        month = datetime.now().strftime("%Y-%m")
        cols = ("Bill No", "Date", "Customer", "Subtotal", "Discount", "Total")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT bill_no, bill_date, customer, subtotal, discount, total
            FROM sales
            WHERE substr(bill_date,1,7)=?
            ORDER BY bill_date
        """, (month,))
        rows = cur.fetchall()
        con.close()

        total_sales = 0
        for row in rows:
            self.reportTable.insert("", "end", values=row)
            try:
                total_sales += float(row[5])
            except:
                pass

        self.total_bills.set(str(len(rows)))
        self.total_sales.set(f"₹ {total_sales:,.2f}")
        self.total_profit.set("₹ 0.00")

    def profit_report(self):
        cols = ("Bill No", "Date", "Medicine", "Qty Sold", "Sale Value", "Net Profit")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur2 = con.cursor()

        cur.execute("""
            SELECT s.bill_no, s.bill_date, si.medicine, si.qty, si.sale, si.purchase
            FROM sales s
            JOIN sales_items si ON s.bill_no = si.bill_no
            WHERE s.bill_date BETWEEN ? AND ?
        """, (self.from_date.get(), self.to_date.get()))

        rows = cur.fetchall()

        total_sales = 0
        total_profit_accum = 0

        for row in rows:
            bill, date, medicine, qty, sale_price, purchase_db = row
            qty = float(qty or 0)
            sale_price = float(sale_price or 0)
            purchase_price = float(purchase_db or 0)

            if purchase_price == 0:
                cur2.execute("SELECT purchase FROM medicine_master WHERE name=?", (medicine,))
                master_row = cur2.fetchone()
                if master_row:
                    purchase_price = float(master_row[0] or 0)

            sale_total = qty * sale_price
            profit = sale_total - (qty * purchase_price)

            total_sales += sale_total
            total_profit_accum += profit

            self.reportTable.insert(
                "", "end",
                values=(bill, date, medicine, int(qty), f"₹{sale_total:.2f}", f"₹{profit:.2f}")
            )

        self.total_bills.set(str(len(rows)))
        self.total_sales.set(f"₹ {total_sales:,.2f}")
        self.total_profit.set(f"₹ {total_profit_accum:,.2f}")
        con.close()

    def stock_report(self):
        cols = ("ID", "Medicine", "Company", "Batch", "Expiry", "Rack", "Stock")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT id, name, company, batch, expiry, rack, stock FROM medicine_master ORDER BY name")
        rows = cur.fetchall()
        con.close()

        for row in rows:
            self.reportTable.insert("", "end", values=row)

        self.total_bills.set(str(len(rows)))
        self.total_sales.set("₹ 0.00")
        self.total_profit.set("₹ 0.00")

    def low_stock(self):
        cols = ("ID", "Medicine", "Company", "Batch", "Expiry", "Stock")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT id, name, company, batch, expiry, stock FROM medicine_master WHERE stock < 10 ORDER BY stock ASC")
        rows = cur.fetchall()
        con.close()

        for row in rows:
            self.reportTable.insert("", "end", values=row)

        self.total_bills.set(str(len(rows)))
        self.total_sales.set("₹ 0.00")
        self.total_profit.set("₹ 0.00")

    def doctor_patient_report(self):
        """
        Groups sales in the current date range by (doctor, patient) - lets
        the pharmacist see which doctors are sending the most business
        and which patients are the most regular, in one view (Marg ERP's
        "doctor and patient-wise sales reports" feature). `doctor` only
        gets captured going forward from billing.py's new Doctor field -
        older bills predating that change show up under "No Doctor".
        """
        cols = ("Doctor", "Patient", "Bills", "Total Sales", "Last Bill Date")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT
                COALESCE(NULLIF(TRIM(doctor), ''), 'No Doctor')  AS doc,
                COALESCE(NULLIF(TRIM(customer), ''), 'Walk-in')  AS cust,
                COUNT(*), SUM(total), MAX(bill_date)
            FROM sales
            WHERE bill_date BETWEEN ? AND ?
            GROUP BY doc, cust
            ORDER BY doc, SUM(total) DESC
        """, (self.from_date.get(), self.to_date.get()))
        rows = cur.fetchall()
        con.close()

        total_sales = 0
        total_bills = 0
        for doc, cust, bill_count, sales_total, last_date in rows:
            sales_total = sales_total or 0
            total_sales += sales_total
            total_bills += bill_count
            self.reportTable.insert(
                "", "end",
                values=(doc, cust, bill_count, f"₹{sales_total:.2f}", last_date)
            )

        self.total_bills.set(str(total_bills))
        self.total_sales.set(f"₹ {total_sales:,.2f}")
        self.total_profit.set("₹ 0.00")

    def h1_register_report(self):
        """
        Every sale of a Schedule H1 / habit-forming medicine in the date
        range, one row per medicine sold (a bill with 2 such medicines
        produces 2 rows) - the record Schedule H1 rules require pharmacies
        to keep (drug, quantity, date, prescriber, patient, address) for
        inspection. Relies on composition_master.habit_forming (set up
        earlier this project) via medicine_master.composition_id -
        medicines never linked to a Composition Master entry can't be
        flagged here, same limitation as the Composition Master /
        Category Breakdown features elsewhere in the project.

        Manufacturer column matches the pharmacy's own physical
        "Prescription Register [Rule 65(3)]" pad, which has a "For
        Schedule 'C'" sub-section (Manufacturer's Name, Batch No., Date
        of expiry) for biologicals/sera/vaccines traceability -
        medicine_master.company is the same field Medicine Master calls
        "Company".
        """
        cols = ("Bill No", "Date", "Doctor", "Patient", "Address", "Medicine", "Manufacturer", "Batch", "Qty")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT s.bill_no, s.bill_date,
                   COALESCE(NULLIF(TRIM(s.doctor), ''), 'No Doctor')  AS doc,
                   COALESCE(NULLIF(TRIM(s.customer), ''), 'Walk-in')  AS cust,
                   COALESCE(NULLIF(TRIM(s.address), ''), '-')  AS addr,
                   si.medicine, mm.company, si.batch, si.qty
            FROM sales_items si
            JOIN sales s ON s.bill_no = si.bill_no
            JOIN medicine_master mm ON mm.name = si.medicine AND mm.batch = si.batch
            JOIN composition_master cm ON cm.composition_id = mm.composition_id
            WHERE cm.habit_forming = 1
              AND s.bill_date BETWEEN ? AND ?
            ORDER BY s.bill_date DESC, s.bill_no
        """, (self.from_date.get(), self.to_date.get()))
        rows = cur.fetchall()
        con.close()

        no_doctor_count = 0
        for bill_no, bill_date, doc, cust, addr, medicine, company, batch, qty in rows:
            if doc == "No Doctor":
                no_doctor_count += 1
            self.reportTable.insert(
                "", "end",
                values=(bill_no, bill_date, doc, cust, addr, medicine, company or "", batch, qty)
            )

        self.total_bills.set(str(len(rows)))
        self.total_sales.set(f"{no_doctor_count} missing Doctor")
        self.total_profit.set("₹ 0.00")

        if no_doctor_count:
            ui_popups.show_warning(self.frame, 
                "Missing Prescriber",
                f"{no_doctor_count} Schedule H1 sale(s) in this range have no "
                "Doctor recorded (sold before the prescription check was added, "
                "or the field was left blank at the time)."
            )

    def schedule_x_register_report(self):
        """
        Same shape as h1_register_report() above, but filtered to
        composition_master.schedule_x=1 instead of habit_forming=1 - the
        narrower NDPS/narcotic subset that legally needs its own separate
        register (double-lock storage, stricter record-keeping) rather
        than the ordinary Schedule H1 prescription register. Reuses the
        exact same Doctor/Patient/Address capture already added to
        Billing for H1 - no separate data-entry step needed.
        """
        cols = ("Bill No", "Date", "Doctor", "Patient", "Address", "Medicine", "Manufacturer", "Batch", "Qty")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT s.bill_no, s.bill_date,
                   COALESCE(NULLIF(TRIM(s.doctor), ''), 'No Doctor')  AS doc,
                   COALESCE(NULLIF(TRIM(s.customer), ''), 'Walk-in')  AS cust,
                   COALESCE(NULLIF(TRIM(s.address), ''), '-')  AS addr,
                   si.medicine, mm.company, si.batch, si.qty
            FROM sales_items si
            JOIN sales s ON s.bill_no = si.bill_no
            JOIN medicine_master mm ON mm.name = si.medicine AND mm.batch = si.batch
            JOIN composition_master cm ON cm.composition_id = mm.composition_id
            WHERE cm.schedule_x = 1
              AND s.bill_date BETWEEN ? AND ?
            ORDER BY s.bill_date DESC, s.bill_no
        """, (self.from_date.get(), self.to_date.get()))
        rows = cur.fetchall()
        con.close()

        no_doctor_count = 0
        for bill_no, bill_date, doc, cust, addr, medicine, company, batch, qty in rows:
            if doc == "No Doctor":
                no_doctor_count += 1
            self.reportTable.insert(
                "", "end",
                values=(bill_no, bill_date, doc, cust, addr, medicine, company or "", batch, qty)
            )

        self.total_bills.set(str(len(rows)))
        self.total_sales.set(f"{no_doctor_count} missing Doctor")
        self.total_profit.set("₹ 0.00")

        if no_doctor_count:
            ui_popups.show_warning(self.frame, 
                "Missing Prescriber",
                f"{no_doctor_count} Schedule X sale(s) in this range have no "
                "Doctor recorded - this is a narcotic/NDPS-controlled "
                "register, double-check these entries manually."
            )

    def prescription_register_report(self):
        """
        The general Rule 65(3) "Prescription Register" - broader than
        H1 Register / Schedule X Register above, which only cover
        medicines flagged habit_forming/schedule_x in Composition Master.
        Rule 65(3) actually requires a record for the supply of ANY drug
        (other than Schedule X, which has its own NDPS-style register)
        sold against a doctor's prescription - not just the narrower
        habit-forming/narcotic subset.

        Deliberately NOT driven by a composition-level "is this Schedule
        H" classification - unlike habit_forming/schedule_x (a fairly
        short, well-known keyword list of specific substances), "Schedule
        H" covers most branded prescription medicines in India, so a
        keyword-based auto-classifier here would be guessing at a huge
        surface area with real compliance consequences either way
        (wrongly-included OR wrongly-omitted entries). Instead this uses
        the actual real-world trigger already captured in Billing: ANY
        sale where the cashier filled in a Doctor name qualifies for this
        register, exactly matching how the pharmacy's own physical
        register pad gets used in practice. This makes it a superset of
        H1 Register / Schedule X Register (every H1/X sale already
        requires a Doctor too), not a replacement for them - those two
        stay useful as pre-filtered views of the higher-risk categories.
        """
        cols = ("Bill No", "Date", "Prescriber", "Patient", "Address", "Medicine", "Manufacturer", "Batch", "Qty")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT s.bill_no, s.bill_date, TRIM(s.doctor) AS doc,
                   COALESCE(NULLIF(TRIM(s.customer), ''), 'Walk-in')  AS cust,
                   COALESCE(NULLIF(TRIM(s.address), ''), '-')  AS addr,
                   si.medicine, mm.company, si.batch, si.qty
            FROM sales_items si
            JOIN sales s ON s.bill_no = si.bill_no
            LEFT JOIN medicine_master mm ON mm.name = si.medicine AND mm.batch = si.batch
            WHERE TRIM(COALESCE(s.doctor, '')) <> ''
              AND s.bill_date BETWEEN ? AND ?
            ORDER BY s.bill_date DESC, s.bill_no
        """, (self.from_date.get(), self.to_date.get()))
        rows = cur.fetchall()
        con.close()

        for bill_no, bill_date, doc, cust, addr, medicine, company, batch, qty in rows:
            self.reportTable.insert(
                "", "end",
                values=(bill_no, bill_date, doc, cust, addr, medicine, company or "", batch or "", qty)
            )

        self.total_bills.set(str(len(rows)))
        self.total_sales.set("Rule 65(3) - all prescription sales")
        self.total_profit.set("₹ 0.00")

    def cold_chain_report(self):
        """
        Current stock of medicines flagged needs_refrigeration=1
        (insulin, vaccines, some biologics) - a physical-check list, not
        a sales ledger like H1/Schedule X above, since what matters here
        is "what's in the fridge right now and when does it expire", not
        who bought it.
        """
        cols = ("Medicine", "Company", "Batch", "Stock", "Expiry", "Rack")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT name, company, batch, stock, expiry, rack
            FROM medicine_master
            WHERE needs_refrigeration = 1 AND stock > 0
            ORDER BY expiry
        """)
        rows = cur.fetchall()
        con.close()

        for name, company, batch, stock, expiry, rack in rows:
            self.reportTable.insert(
                "", "end",
                values=(name, company or "", batch or "", stock, expiry or "", rack or "")
            )

        self.total_bills.set(str(len(rows)))
        self.total_sales.set("₹ 0.00")
        self.total_profit.set("₹ 0.00")

    def expiry_report(self):
        cols = ("ID", "Medicine", "Company", "Batch", "Expiry", "Stock")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT id, name, company, batch, expiry, stock FROM medicine_master WHERE expiry <> '' AND stock > 0")
        rows = cur.fetchall()
        con.close()

        cutoff = (datetime.now() + timedelta(days=90)).replace(day=1)
        expired_count = 0
        for row in rows:
            try:
                exp_dt = datetime.strptime(row[4], "%m/%y").replace(day=1)
                if exp_dt <= cutoff:
                    self.reportTable.insert("", "end", values=row)
                    expired_count += 1
            except Exception:
                continue

        self.total_bills.set(str(expired_count))
        self.total_sales.set("₹ 0.00")
        self.total_profit.set("₹ 0.00")

    def slow_moving_report(self):
        """
        Medicines currently in stock that either have NEVER been sold,
        or haven't sold in SLOW_MOVING_DAYS - surfaces where capital is
        tied up in stock that isn't turning over, which none of the
        other reports show directly (Stock just lists everything;
        Profit/Daily/Monthly Sales only look at what DID sell).

        sales.bill_date is stored as free-text "YYYY-MM-DD" (billing.py's
        save_bill() hardcodes datetime.now().strftime("%Y-%m-%d") on
        INSERT, regardless of what self.bill_date's DD-MM-YYYY display
        StringVar shows - confirmed against live data; the earlier version
        of this comment/parse wrongly assumed "DD-MM-YYYY" here, which
        made every strptime() below fail silently and every medicine
        show up as "Never Sold" even when it had recent sales - fixed).
        ISO-format text does sort/compare correctly as a string, but this
        still parses by hand rather than trusting SQL MAX()/ORDER BY,
        matching expiry_report() above and staying robust if the stored
        format ever changes again.
        """
        SLOW_MOVING_DAYS = 90

        cols = ("Medicine", "Company", "Stock", "Stock Value", "Last Sale", "Days Since Sale")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute(
            "SELECT name, company, stock, purchase, gst, pack_size "
            "FROM medicine_master WHERE stock > 0"
        )
        medicines = cur.fetchall()

        cur.execute("""
            SELECT si.medicine, s.bill_date
            FROM sales_items si
            JOIN sales s ON s.bill_no = si.bill_no
        """)
        sale_rows = cur.fetchall()
        con.close()

        last_sale_date = {}
        for medicine, bill_date in sale_rows:
            try:
                dt = datetime.strptime(bill_date, "%Y-%m-%d")
            except Exception:
                continue
            if medicine not in last_sale_date or dt > last_sale_date[medicine]:
                last_sale_date[medicine] = dt

        today = datetime.now()
        results = []
        for name, company, stock, purchase, gst, pack_size in medicines:
            try:
                pack_mult = get_pack_multiplier(str(pack_size or "1")) or 1
            except Exception:
                pack_mult = 1
            unit_price = ((purchase or 0) + (purchase or 0) * ((gst or 0) / 100)) / pack_mult
            stock_value = unit_price * (stock or 0)

            last_dt = last_sale_date.get(name)
            if last_dt is None:
                days_since = None  # "Never Sold" - always included regardless of threshold
                last_str = "Never Sold"
            else:
                days_since = (today - last_dt).days
                last_str = last_dt.strftime("%d-%m-%Y")
                if days_since < SLOW_MOVING_DAYS:
                    continue  # sold recently enough - not slow-moving

            # Sort key: "Never Sold" (None) ranks as worst/oldest, ahead
            # of any finite days_since - a medicine that's NEVER sold is
            # at least as much a concern as one that merely hasn't sold
            # in a while.
            sort_key = days_since if days_since is not None else 10**9
            results.append((sort_key, name, company or "", stock, stock_value, last_str, days_since))

        results.sort(key=lambda r: r[0], reverse=True)

        total_value = 0.0
        for _, name, company, stock, stock_value, last_str, days_since in results:
            total_value += stock_value
            days_display = "-" if days_since is None else str(days_since)
            self.reportTable.insert(
                "", "end",
                values=(name, company, stock, f"₹ {stock_value:,.2f}", last_str, days_display)
            )

    def missing_generic_report(self):
        """
        Data-quality report (Aug 2026) - which medicines have NO way to
        resolve a generic/composition at all: no medicine_master.
        composition_id link into composition_master, AND no free-text
        medicine_master.generic value either. Added alongside the DDI
        Safety Checker (ddi_checker.py) and Predictive Inventory work
        this same round, because both of those silently SKIP checking
        any medicine that falls in this gap - ddi_checker.
        _medicine_generics()'s own docstring calls this out explicitly
        ("a medicine with neither is simply omitted"). Rather than the
        pharmacist discovering this one medicine at a time (a DDI
        warning that should have fired but didn't, a substitute search
        that comes back empty), this report lists every gap at once so
        it can be closed from Medicine Master's own Generic field.

        A medicine name only counts as "missing" here if EVERY batch row
        under that name lacks both signals - medicine_master is
        batch-level (one name, several purchase/batch rows), and if even
        one row has a resolvable generic, ddi_checker's own
        `WHERE name = ? LIMIT 1` lookup has a chance of finding it (which
        row SQLite returns first isn't guaranteed, but at least one
        exists to find) - a name is only a genuine, unconditional gap
        when NONE of its rows have anything set.

        Sorted by total stock on hand (descending) - a medicine actually
        sitting on the shelf is more worth a pharmacist's time to fix
        than one buried at zero stock.
        """
        cols = ("Medicine", "Company", "Total Stock")
        self.update_table_headers(cols)
        self.reportTable.delete(*self.reportTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT name, MAX(company), SUM(stock)
            FROM medicine_master
            WHERE name NOT IN (
                SELECT DISTINCT mm2.name
                FROM medicine_master mm2
                JOIN composition_master cm ON cm.composition_id = mm2.composition_id
                WHERE cm.composition_name IS NOT NULL AND TRIM(cm.composition_name) <> ''
            )
            AND name NOT IN (
                SELECT DISTINCT name FROM medicine_master
                WHERE generic IS NOT NULL AND TRIM(generic) <> ''
            )
            GROUP BY name
            ORDER BY SUM(stock) DESC, name ASC
        """)
        rows = cur.fetchall()
        con.close()

        for name, company, total_stock in rows:
            self.reportTable.insert(
                "", "end",
                values=(name, company or "", int(total_stock or 0))
            )

        self.total_bills.set(str(len(rows)))
        self.total_sales.set("₹ 0.00")
        self.total_profit.set("₹ 0.00")

        self.total_bills.set(str(len(results)))
        self.total_sales.set(f"₹ {total_value:,.2f}")
        self.total_profit.set("₹ 0.00")