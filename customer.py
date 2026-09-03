import tkinter as tk
from tkinter import ttk, messagebox

# Data access moved to customer_repository.py (Aug 2026 repository-layer
# pilot slice - see that module's docstring for the full rationale).
# This screen no longer opens its own sqlite3 connections at all - every
# customers-table read/write goes through these functions instead, so
# the SQL can be unit-tested independently of this Tkinter screen and
# has a single seam to redirect later (e.g. Multi-Shop/Cloud Sync)
# without touching every button handler here again.
import customer_repository as repo
import ui_style
import ui_popups


class Customer:

    def __init__(self, frame, on_close=None):
        self.frame = frame
        self.on_close = on_close
        self.create_variables()
        self.create_table()
        self.create_ui()
        self.load_customers()

    # ======================================
    # VARIABLES
    # ======================================

    def create_variables(self):
        self.customer_id = None
        self.name = tk.StringVar()
        self.mobile = tk.StringVar()
        self.address = tk.StringVar()
        self.doctor = tk.StringVar()
        self.gstin = tk.StringVar()
        self.search = tk.StringVar()
        # Loyalty discount % for this customer - Billing auto-suggests
        # this whenever the customer is typed/selected there, but stays
        # editable per-bill (see billing.py's _autofill_discount()).
        self.discount_percent = tk.DoubleVar(value=0.0)
        # Credit limit (₹) - 0 means "not set / unrestricted", same
        # convention as supplier.credit_period_days and medicine_master.
        # reorder_level elsewhere in this app. Billing warns (doesn't
        # hard-block - this is a business judgment call for the
        # pharmacist, not a legal requirement like the Schedule H1
        # check) when a bill would push this customer's outstanding
        # past this amount.
        self.credit_limit = tk.DoubleVar(value=0.0)

    # ======================================
    # TABLE MIGRATION
    # ======================================

    def create_table(self):
        repo.ensure_schema()

    # ======================================
    # USER INTERFACE
    # ======================================

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="CUSTOMER MASTER",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        # ===============================
        # FORM
        # ===============================
        form = tk.LabelFrame(
            self.frame,
            text="Customer Details",
            font=("Segoe UI", 10, "bold")
        )
        form.pack(fill="x", padx=10, pady=10)

        tk.Label(form, text="Customer Name").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.name, width=35).grid(row=0, column=1)

        tk.Label(form, text="Mobile").grid(row=0, column=2, padx=5)
        tk.Entry(form, textvariable=self.mobile, width=25).grid(row=0, column=3)

        tk.Label(form, text="Doctor").grid(row=1, column=0, padx=5, pady=5)
        tk.Entry(form, textvariable=self.doctor, width=35).grid(row=1, column=1)

        tk.Label(form, text="GSTIN").grid(row=1, column=2)
        tk.Entry(form, textvariable=self.gstin, width=25).grid(row=1, column=3)

        tk.Label(form, text="Discount %").grid(row=1, column=4, padx=5)
        tk.Entry(form, textvariable=self.discount_percent, width=10).grid(row=1, column=5, padx=5)

        tk.Label(form, text="Credit Limit (₹)").grid(row=1, column=6, padx=5)
        tk.Entry(form, textvariable=self.credit_limit, width=12).grid(row=1, column=7, padx=5)

        tk.Label(form, text="Address").grid(row=2, column=0, padx=5, pady=5, sticky="nw")
        tk.Entry(form, textvariable=self.address, width=75).grid(row=2, column=1, columnspan=3, sticky="we")

        # ===============================
        # BUTTONS
        # ===============================
        btn = tk.Frame(self.frame)
        btn.pack(fill="x", padx=10, pady=10)

        tk.Button(btn, text="Save", bg="#2E7D32", fg="white", width=12, command=self.save_customer).pack(side="left", padx=5)
        tk.Button(btn, text="Update", bg="#1565C0", fg="white", width=12, command=self.update_customer).pack(side="left", padx=5)
        tk.Button(btn, text="Delete", bg="#C62828", fg="white", width=12, command=self.delete_customer).pack(side="left", padx=5)
        tk.Button(btn, text="Clear", bg="#EF6C00", fg="white", width=12, command=self.clear_fields).pack(side="left", padx=5)

        tk.Label(btn, text="Search").pack(side="left", padx=(40, 5))
        search = tk.Entry(btn, textvariable=self.search, width=30)
        search.pack(side="left")
        self.search.trace_add("write", self.search_customer)
        self._search_entry = search

        # ===============================
        # TABLE (UPDATED COLUMNS WITH ADDRESS)
        # ===============================
        table = tk.Frame(self.frame)
        table.pack(fill="both", expand=True, padx=10, pady=10)

        # முகவரியையும் டேபிளில் காட்டும் வகையில் பத்திகள் சீரமைக்கப்பட்டுள்ளன
        cols = (
            "ID",
            "Customer",
            "Mobile",
            "Address",
            "Doctor",
            "GSTIN",
            "Discount %",
            "Credit Limit"
        )

        self.customerTable = ttk.Treeview(
            table,
            columns=cols,
            show="headings",
            height=16,
            style="ERP.Treeview"
        )

        for c in cols:
            self.customerTable.heading(c, text=c)
            # முகவரிப் பத்திக்கு மட்டும் கூடுதல் அகலம் (width) ஒதுக்கப்பட்டுள்ளது
            w_size = 250 if c == "Address" else 130
            self.customerTable.column(c, width=w_size, anchor="center")

        self.customerTable.pack(fill="both", expand=True)
        self.customerTable.bind("<<TreeviewSelect>>", self.select_customer)

        # ERP-wide keyboard-nav pass (Aug 2026): Down/Enter in the search
        # box jumps into the table and selects/loads its first result -
        # see ui_style.bind_search_to_grid()'s docstring. No row_count_fn
        # needed - a ttk.Treeview is never padded with blank rows.
        ui_style.bind_search_to_grid(self._search_entry, self.customerTable)

        # Summary footer (Aug 2026) - every other master/ledger screen
        # touched in this pass (Stock, Purchase) already ends in a plain-
        # language count instead of just leaving the table to trail off
        # into blank space below the last row. Customer Master had
        # nothing here at all before this - same fix, same place.
        footer = tk.Frame(self.frame)
        footer.pack(fill="x", padx=10, pady=(0, 10))
        self.lblCustomerCount = tk.Label(
            footer, text="Total Customers : 0",
            font=("Segoe UI", 10, "bold"), fg="#1565C0"
        )
        self.lblCustomerCount.pack(side="left")

    # ======================================
    # SAVE CUSTOMER
    # ======================================

    def save_customer(self):
        if self.name.get().strip() == "":
            ui_popups.show_error(self.frame, "Error", "Customer Name Required")
            return

        if not self.validate_mobile():
            return

        if not self.validate_discount():
            return

        try:
            repo.insert_customer(
                self.name.get().strip(),
                self.mobile.get().strip(),
                self.address.get().strip(),
                self.doctor.get().strip(),
                self.gstin.get().strip(),
                self.discount_percent.get() or 0,
                self.credit_limit.get() or 0
            )
            ui_popups.show_info(self.frame, "Success", "Customer Saved Successfully")
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))
        finally:
            self.clear_fields()
            self.load_customers()

    # ======================================
    # LOAD CUSTOMERS (FIXED ALIGNMENT)
    # ======================================

    def load_customers(self):
        self.customerTable.delete(*self.customerTable.get_children())

        rows = repo.list_customers()

        for row in rows:
            self.customerTable.insert("", "end", values=row)
        self.lblCustomerCount.config(text=f"Total Customers : {len(rows)}")

    # ======================================
    # SEARCH CUSTOMER (FIXED ALIGNMENT)
    # ======================================

    def search_customer(self, *args):
        self.customerTable.delete(*self.customerTable.get_children())

        rows = repo.search_customers(self.search.get())

        for row in rows:
            self.customerTable.insert("", "end", values=row)
        label = f"Showing {len(rows)} matching customer(s)" if self.search.get().strip() else f"Total Customers : {len(rows)}"
        self.lblCustomerCount.config(text=label)

    # ======================================
    # SELECT CUSTOMER
    # ======================================

    def select_customer(self, event=None):
        selected = self.customerTable.focus()
        if not selected:
            return

        values = self.customerTable.item(selected)["values"]
        self.customer_id = values[0]

        row = repo.get_customer(self.customer_id)

        if row:
            self.name.set(row[0] or "")
            self.mobile.set(row[1] or "")
            self.address.set(row[2] or "")
            self.doctor.set(row[3] or "")
            self.gstin.set(row[4] or "")
            self.discount_percent.set(row[5] or 0)
            self.credit_limit.set(row[6] or 0)

    # ======================================
    # UPDATE CUSTOMER
    # ======================================

    def update_customer(self):
        if self.customer_id is None:
            ui_popups.show_error(self.frame, "Error", "Select Customer First")
            return

        if not self.validate_mobile():
            return

        if not self.validate_discount():
            return

        try:
            repo.update_customer(
                self.customer_id,
                self.name.get().strip(),
                self.mobile.get().strip(),
                self.address.get().strip(),
                self.doctor.get().strip(),
                self.gstin.get().strip(),
                self.discount_percent.get() or 0,
                self.credit_limit.get() or 0
            )
            ui_popups.show_info(self.frame, "Success", "Customer Updated Successfully")
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))
        finally:
            self.load_customers()
            self.clear_fields()

    # ======================================
    # DELETE CUSTOMER
    # ======================================

    def delete_customer(self):
        if self.customer_id is None:
            ui_popups.show_error(self.frame, "Error", "Select Customer First")
            return

        if not ui_popups.show_confirmation(self.frame, "Confirm", "Delete Selected Customer?"):
            return

        repo.delete_customer(self.customer_id)

        ui_popups.show_info(self.frame, "Deleted", "Customer Deleted Successfully")
        self.load_customers()
        self.clear_fields()

    # ======================================
    # CLEAR
    # ======================================

    def clear_fields(self):
        self.customer_id = None
        self.name.set("")
        self.mobile.set("")
        self.address.set("")
        self.doctor.set("")
        self.gstin.set("")
        self.discount_percent.set(0.0)
        self.credit_limit.set(0.0)
        self.search.set("")

    # ======================================
    # VALIDATION
    # ======================================

    def validate_mobile(self):
        mobile = self.mobile.get().strip()
        if mobile == "":
            return True

        if not mobile.isdigit():
            ui_popups.show_error(self.frame, "Invalid Mobile", "Mobile Number must contain only digits.")
            return False

        if len(mobile) != 10:
            ui_popups.show_error(self.frame, "Invalid Mobile", "Mobile Number must be 10 digits.")
            return False

        return True

    def validate_discount(self):
        try:
            pct = float(self.discount_percent.get() or 0)
        except (ValueError, tk.TclError):
            ui_popups.show_error(self.frame, "Invalid Discount", "Discount % must be a number.")
            return False

        if pct < 0 or pct > 100:
            ui_popups.show_error(self.frame, "Invalid Discount", "Discount % must be between 0 and 100.")
            return False

        return True

    # ======================================
    # REFRESH
    # ======================================

    def refresh(self):
        self.clear_fields()
        self.load_customers()

    # ======================================
    # CUSTOMER COUNT
    # ======================================

    def customer_count(self):
        return repo.count_customers()

    # ======================================
    # EXPORT DATA
    # ======================================

    def export_customers(self):
        ui_popups.show_info(self.frame, "Customer", "Export Feature Coming Soon.")

    # ======================================
    # PRINT CUSTOMER
    # ======================================

    def print_customer(self):
        ui_popups.show_info(self.frame, "Customer", "Print Feature Coming Soon.")

    # ======================================
    # CLOSE
    # ======================================

    def close(self):
        self.frame.destroy()
        if self.on_close:
            self.on_close()