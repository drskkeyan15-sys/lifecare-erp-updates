import tkinter as tk
from tkinter import ttk, messagebox

import ui_style

# Data access moved to supplier_repository.py (Aug 2026 repository-layer
# pilot slice, same pattern as customer.py/customer_repository.py - see
# that module's docstring for the rationale). This screen no longer
# opens its own sqlite3 connections at all.
import supplier_repository as repo
import ui_popups


class Supplier:

    def __init__(self, frame):
        self.frame = frame
        self.selected_id = None  # தேர்வு செய்யப்படும் சப்ளையர் ஐடியை சேமிக்க

        self.create_variables()
        self.create_ui()
        self.load_suppliers()

    def create_variables(self):
        self.name = tk.StringVar()
        self.contact = tk.StringVar()
        self.mobile = tk.StringVar()
        self.gst = tk.StringVar()
        self.dlno = tk.StringVar()
        self.address = tk.StringVar()
        self.city = tk.StringVar()
        self.email = tk.StringVar()
        self.credit_period = tk.StringVar(value="0")
        self.search = tk.StringVar()

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="SUPPLIER MASTER",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        # ======================================
        # SUPPLIER DETAILS FORM
        # ======================================
        entry = tk.LabelFrame(
            self.frame,
            text="Supplier Details",
            font=("Segoe UI", 10, "bold")
        )
        entry.pack(fill="x", padx=10, pady=10)

        # Row 1
        tk.Label(entry, text="Supplier Name").grid(row=0, column=0, padx=5, pady=5)
        tk.Entry(entry, textvariable=self.name, width=30).grid(row=0, column=1)

        tk.Label(entry, text="Contact Person").grid(row=0, column=2)
        tk.Entry(entry, textvariable=self.contact, width=25).grid(row=0, column=3)

        tk.Label(entry, text="Mobile").grid(row=0, column=4)
        tk.Entry(entry, textvariable=self.mobile, width=20).grid(row=0, column=5)

        # Row 2
        tk.Label(entry, text="GSTIN").grid(row=1, column=0, padx=5, pady=5)
        tk.Entry(entry, textvariable=self.gst, width=30).grid(row=1, column=1)

        tk.Label(entry, text="DL No").grid(row=1, column=2)
        tk.Entry(entry, textvariable=self.dlno, width=25).grid(row=1, column=3)

        tk.Label(entry, text="City").grid(row=1, column=4)
        tk.Entry(entry, textvariable=self.city, width=20).grid(row=1, column=5)

        tk.Label(entry, text="Credit Period (Days)").grid(row=1, column=6, padx=5)
        tk.Entry(entry, textvariable=self.credit_period, width=8).grid(row=1, column=7)

        # Row 3
        tk.Label(entry, text="Address").grid(row=2, column=0, padx=5, pady=5)
        tk.Entry(entry, textvariable=self.address, width=60).grid(row=2, column=1, columnspan=3, sticky="w")

        tk.Label(entry, text="Email").grid(row=2, column=4)
        tk.Entry(entry, textvariable=self.email, width=25).grid(row=2, column=5)

        # ======================================
        # BUTTONS
        # ======================================
        # Moved below the form (was above it before the Aug 2026 UI-
        # consistency pass) - every other screen (Customer Master,
        # Medicine Master, etc.) places Save/Update/Delete/Clear AFTER
        # the fields they act on, not before.
        btnFrame = tk.Frame(self.frame)
        btnFrame.pack(fill="x", padx=10, pady=5)

        tk.Button(btnFrame, text="SAVE", bg="#2E7D32", fg="white", width=15, command=self.save_supplier).pack(side="left", padx=5)
        tk.Button(btnFrame, text="UPDATE", bg="#1565C0", fg="white", width=15, command=self.update_supplier).pack(side="left", padx=5)
        tk.Button(btnFrame, text="DELETE", bg="#C62828", fg="white", width=15, command=self.delete_supplier).pack(side="left", padx=5)
        tk.Button(btnFrame, text="CLEAR", bg="#EF6C00", fg="white", width=15, command=self.clear_fields).pack(side="left", padx=5)

        # Search Bar
        searchFrame = tk.Frame(self.frame)
        searchFrame.pack(fill="x", padx=10, pady=10)
        tk.Label(searchFrame, text="Search Supplier:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=5)
        search_ent = tk.Entry(searchFrame, textvariable=self.search, width=30)
        search_ent.pack(side="left", padx=5)
        self.search.trace_add("write", lambda *args: self.search_supplier())
        self._search_entry = search_ent

        # ======================================
        # TABLE (TREEVIEW)
        # ======================================
        tableFrame = tk.Frame(self.frame)
        tableFrame.pack(fill="both", expand=True, padx=10, pady=10)

        scrollY = ttk.Scrollbar(tableFrame)
        cols = ("ID", "Supplier Name", "Contact Person", "Mobile", "GSTIN", "City", "Credit Days")

        self.supplierTable = ttk.Treeview(
            tableFrame,
            columns=cols,
            show="headings",
            yscrollcommand=scrollY.set,
            style="ERP.Treeview"
        )
        scrollY.config(command=self.supplierTable.yview)
        scrollY.pack(side="right", fill="y")
        self.supplierTable.pack(fill="both", expand=True)

        for c in cols:
            self.supplierTable.heading(c, text=c)
            w_size = 200 if c == "Supplier Name" else 130
            self.supplierTable.column(c, width=w_size, anchor="center")

        # ─── திருத்தம் 2: தரவுகளை எடிட் செய்ய டேபிள் கிளிக் பைண்டிங் இணைக்கப்பட்டுள்ளது ───
        self.supplierTable.bind("<<TreeviewSelect>>", self.get_cursor)

        # ERP-wide keyboard-nav pass (Aug 2026): Down/Enter in the search
        # box jumps into the table and selects/loads its first result -
        # see ui_style.bind_search_to_grid()'s docstring.
        ui_style.bind_search_to_grid(self._search_entry, self.supplierTable)

        # Summary footer (Aug 2026) - same fix as Customer Master/Stock/
        # Purchase: the table used to just trail off into blank space
        # below the last row with no plain-language count anywhere on
        # screen.
        footer = tk.Frame(self.frame)
        footer.pack(fill="x", padx=10, pady=(0, 10))
        self.lblSupplierCount = tk.Label(
            footer, text="Total Suppliers : 0",
            font=("Segoe UI", 10, "bold"), fg="#1565C0"
        )
        self.lblSupplierCount.pack(side="left")

        # தரவுத்தள அட்டவணை வடிவமைப்பு ஒத்திசைவு
        repo.ensure_schema()

    # ======================================
    # FUNCTIONS (CRUD OPERATIONS)
    # ======================================

    def save_supplier(self):
        if self.name.get().strip() == "":
            ui_popups.show_error(self.frame, "Error", "Supplier Name Required")
            return

        try:
            credit_days = int(self.credit_period.get().strip() or 0)
        except ValueError:
            ui_popups.show_error(self.frame, "Error", "Credit Period must be a whole number of days.")
            return

        try:
            repo.insert_supplier(
                self.name.get().strip(),
                self.contact.get().strip(),
                self.mobile.get().strip(),
                self.gst.get().strip(),
                self.dlno.get().strip(),
                self.city.get().strip(),
                self.address.get().strip(),
                self.email.get().strip(),
                credit_days
            )
            ui_popups.show_info(self.frame, "Success", "Supplier Saved Successfully")
            self.clear_fields()
            self.load_suppliers()
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", f"Error due to: {str(e)}")

    def get_cursor(self, event=None):
        """டேபிளில் கிளிக் செய்யும் போது விபரங்களை படிவத்திற்கு கொண்டு வரும்"""
        selected = self.supplierTable.focus()
        if not selected:
            return

        values = self.supplierTable.item(selected)["values"]
        self.selected_id = values[0]

        row = repo.get_supplier(self.selected_id)

        if row:
            self.name.set(row[1] or "")
            self.contact.set(row[2] or "")
            self.mobile.set(row[3] or "")
            self.gst.set(row[4] or "")
            self.dlno.set(row[5] or "")
            self.city.set(row[6] or "")
            self.address.set(row[7] or "")
            self.email.set(row[8] or "")
            # credit_period_days was added via ALTER TABLE, so it's
            # always the LAST column regardless of the original CREATE
            # TABLE order - SELECT * appends ALTER-added columns at the
            # end of the row tuple. Older rows (pre-migration) read back
            # as NULL -> default to 0 (Cash/immediate).
            self.credit_period.set(str(row[-1]) if row[-1] is not None else "0")

    def update_supplier(self):
        if self.selected_id is None:
            ui_popups.show_warning(self.frame, "Warning", "Select a supplier from the table first")
            return

        if self.name.get().strip() == "":
            ui_popups.show_error(self.frame, "Error", "Supplier Name Required")
            return

        try:
            credit_days = int(self.credit_period.get().strip() or 0)
        except ValueError:
            ui_popups.show_error(self.frame, "Error", "Credit Period must be a whole number of days.")
            return

        try:
            repo.update_supplier(
                self.selected_id,
                self.name.get().strip(),
                self.contact.get().strip(),
                self.mobile.get().strip(),
                self.gst.get().strip(),
                self.dlno.get().strip(),
                self.city.get().strip(),
                self.address.get().strip(),
                self.email.get().strip(),
                credit_days
            )
            ui_popups.show_info(self.frame, "Success", "Supplier Details Updated Successfully")
            self.clear_fields()
            self.load_suppliers()
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", f"Error due to: {str(e)}")

    def delete_supplier(self):
        if self.selected_id is None:
            ui_popups.show_warning(self.frame, "Warning", "Select a supplier from the table first")
            return

        if not ui_popups.show_confirmation(self.frame, "Confirm", "Are you sure you want to delete this supplier?"):
            return

        try:
            repo.delete_supplier(self.selected_id)
            ui_popups.show_info(self.frame, "Success", "Supplier Deleted Successfully")
            self.clear_fields()
            self.load_suppliers()
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", f"Error due to: {str(e)}")

    def clear_fields(self):
        self.selected_id = None
        self.name.set("")
        self.contact.set("")
        self.mobile.set("")
        self.gst.set("")
        self.dlno.set("")
        self.address.set("")
        self.city.set("")
        self.email.set("")
        self.credit_period.set("0")
        self.search.set("")

    def load_suppliers(self):
        self.supplierTable.delete(*self.supplierTable.get_children())

        rows = repo.list_suppliers()

        for row in rows:
            # clean_row() so an optional field a supplier was saved
            # without (Contact Person/Mobile/City are all nullable, per
            # supplier_repository.py's schema) shows as blank instead of
            # the literal text "None" - see ui_style.clean_row()'s own
            # docstring.
            self.supplierTable.insert("", "end", values=ui_style.clean_row(row))
        self.lblSupplierCount.config(text=f"Total Suppliers : {len(rows)}")

    def search_supplier(self):
        self.supplierTable.delete(*self.supplierTable.get_children())

        rows = repo.search_suppliers(self.search.get().strip())

        for row in rows:
            # clean_row() so an optional field a supplier was saved
            # without (Contact Person/Mobile/City are all nullable, per
            # supplier_repository.py's schema) shows as blank instead of
            # the literal text "None" - see ui_style.clean_row()'s own
            # docstring.
            self.supplierTable.insert("", "end", values=ui_style.clean_row(row))
        label = f"Showing {len(rows)} matching supplier(s)" if self.search.get().strip() else f"Total Suppliers : {len(rows)}"
        self.lblSupplierCount.config(text=label)