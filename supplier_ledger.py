import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
# Aug 2026 repository-layer pass: all direct sqlite3 access has since
# moved into supplier_ledger_repository.py (see that module's
# docstring) - DB_NAME itself is no longer imported here, only by the
# repository.
import supplier_ledger_repository as repo
import ui_style
import ui_popups


class SupplierLedger:

    def __init__(self, frame):
        self.frame = frame
        self.create_variables()
        self.create_ui()
        self.load_suppliers()

    def load_suppliers(self):
        try:
            self._supplier_names = repo.list_supplier_names_dynamic()
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", f"சப்ளையர் பெயர்களை எடுப்பதில் பிழை:\n{e}")
            self._supplier_names = []
        self.cmbSupplier["values"] = self._supplier_names

    def _filter_supplier_dropdown(self, typed_text):
        typed = typed_text.lower()
        self.cmbSupplier["values"] = (
            self._supplier_names if not typed
            else [n for n in self._supplier_names if typed in n.lower()]
        )

    def create_variables(self):
        self.supplier_name = tk.StringVar()
        self.pay_amount = tk.DoubleVar(value=0.0)
        self.pay_mode = tk.StringVar(value="Cash")
        self.total_purchase = tk.StringVar(value="₹ 0.00")
        self.total_paid = tk.StringVar(value="₹ 0.00")
        self.balance_due = tk.StringVar(value="₹ 0.00")
        self.total_gst_itc = tk.StringVar(value="₹ 0.00")

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="SUPPLIER PAYMENT & GST LEDGER TRACKING",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        # ---------------- Selection & Payment Frame ----------------
        form_frame = tk.LabelFrame(
            self.frame,
            text="Supplier Selection & Payment",
            font=("Segoe UI", 10, "bold")
        )
        form_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(form_frame, text="Supplier Name").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.cmbSupplier = ttk.Combobox(
            form_frame,
            textvariable=self.supplier_name,
            width=22,
            state="normal"
        )
        self.cmbSupplier.grid(row=0, column=1, padx=5, pady=5)
        # ERP-wide keyboard-nav pass (Aug 2026): previously this box had
        # NO binding at all - selecting from the dropdown did nothing
        # until "Load Ledger" was clicked. Now typing narrows the list
        # live, and Enter/Tab-away/a mouse click all load the ledger the
        # same as clicking that button.
        ui_style.bind_search_combo(
            self.cmbSupplier,
            on_filter=self._filter_supplier_dropdown,
            on_confirm=self._on_supplier_confirm,
        )

        tk.Button(
            form_frame,
            text="Load Ledger",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            width=14,
            command=self.load_ledger,
            cursor="hand2"
        ).grid(row=0, column=2, padx=10, pady=5)

        tk.Label(form_frame, text="Pay Amount (₹)").grid(row=0, column=3, padx=5, pady=5, sticky="w")
        tk.Entry(form_frame, textvariable=self.pay_amount, width=15).grid(row=0, column=4, padx=5, pady=5)

        tk.Label(form_frame, text="Mode").grid(row=0, column=5, padx=5, pady=5, sticky="w")
        ttk.Combobox(
            form_frame, textvariable=self.pay_mode, width=10, state="readonly",
            values=["Cash", "Bank", "UPI", "Cheque"]
        ).grid(row=0, column=6, padx=5, pady=5)

        tk.Button(
            form_frame,
            text="Make Payment",
            bg="green",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            width=14,
            command=self.make_payment,
            cursor="hand2"
        ).grid(row=0, column=7, padx=10, pady=5)

        # ---------------- Summary Totals Frame ----------------
        summary_frame = tk.Frame(self.frame)
        summary_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(summary_frame, text="Total Purchase:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=5)
        tk.Label(summary_frame, textvariable=self.total_purchase, fg="blue", font=("Segoe UI", 10, "bold")).pack(side="left", padx=5)

        tk.Label(summary_frame, text="Total GST (ITC):", font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)
        tk.Label(summary_frame, textvariable=self.total_gst_itc, fg="purple", font=("Segoe UI", 10, "bold")).pack(side="left", padx=5)

        tk.Label(summary_frame, text="Total Paid:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)
        tk.Label(summary_frame, textvariable=self.total_paid, fg="green", font=("Segoe UI", 10, "bold")).pack(side="left", padx=5)

        tk.Label(summary_frame, text="Balance Due:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)
        tk.Label(summary_frame, textvariable=self.balance_due, fg="red", font=("Segoe UI", 10, "bold")).pack(side="left", padx=5)

        # ---------------- Table Frame ----------------
        table_frame = tk.Frame(self.frame)
        table_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 2026-09-03: added "Supp Inv No" - the supplier's OWN bill
        # number (typed into Purchase Entry's "Supp. Inv. No" field, see
        # purchase.py) had nowhere to be seen again after that one
        # invoice was saved/printed - a pharmacist reconciling against
        # the physical paper bill, or checking whether a rate changed
        # since a specific supplier invoice, had no way to find it here.
        # get_purchase_like_rows() now returns it as a 4th column
        # (None when the matched table/row doesn't have one - see that
        # function's own docstring), rendered as "-" same as every
        # other blank cell on this table.
        columns = ("Date", "Bill No / Ref", "Supp Inv No", "Type", "Purchase Amount", "CGST", "SGST", "Paid Amount")
        self.ledgerTable = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=14,
            style="ERP.Treeview"
        )

        for c in columns:
            self.ledgerTable.heading(c, text=c)
            self.ledgerTable.column(c, width=110 if c == "Supp Inv No" else 130, anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.ledgerTable.yview)
        self.ledgerTable.configure(yscrollcommand=scrollbar.set)
        self.ledgerTable.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ---------------- Invoice-wise Due/Overdue Table ----------------
        # Separate from ledgerTable above (which is a flat, transaction-
        # by-transaction log built from whatever purchase-like table it
        # auto-detects). This one is deliberately narrow-scoped: it reads
        # the KNOWN `purchase`/`supplier_payments` schema directly,
        # groups by bill_no into one row per real invoice, and computes
        # Paid/Balance/Due-Overdue status via FIFO payment allocation
        # (see compute_invoice_status()).
        invoice_frame = tk.LabelFrame(
            self.frame,
            text="Invoice-wise Due / Overdue Status",
            font=("Segoe UI", 10, "bold")
        )
        invoice_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self._invoice_cols = ("Bill No", "Date", "Amount", "Paid", "Balance", "Due Date", "Status")
        # 2026-08-30: switched from make_excel_sheet() (tksheet) to
        # make_plain_sheet() (plain ttk.Treeview) - see medicine_master.py's
        # ui_style.PlainSheet docstring for the full rationale. Every
        # other call below (set_sheet_data/highlight_rows/column_width/
        # etc) is unchanged, PlainSheet answers to the same method names.
        self.invoiceTable = ui_style.make_plain_sheet(
            invoice_frame, self._invoice_cols, {},
            text_columns=("Bill No", "Date", "Due Date", "Status"),
        )
        self.invoiceTable.pack(fill="both", expand=True, padx=5, pady=5)
        self.invoiceTable.enable_bindings(*ui_style.READONLY_BINDINGS)
        ui_style.enable_row_highlight_on_select(self.invoiceTable)

        # Last-column-stretch fix (same pattern as medicine_master.py /
        # purchase.py / stock.py / brand_master_gui.py) - make_excel_
        # sheet() sizes columns to fixed pixel widths regardless of the
        # container's actual packed width, leaving a blank strip past
        # "Status" otherwise. Bound to the ROOT window's <Configure> (not
        # the Sheet's own, which fires during normal scrolling too) with
        # winfo_exists()/TclError guards since the root's <Configure>
        # keeps firing after this screen's widgets are destroyed on
        # navigation. NOTE: this only fixes invoiceTable (tksheet) -
        # ledgerTable above is a separate ttk.Treeview, out of scope for
        # this fix (Treeview has no grid-line/stretch concept the same way).
        self._invoice_last_col_width = None

        def _stretch_invoice_last_column(event=None):
            try:
                if not self.invoiceTable.winfo_exists():
                    return
                self.invoiceTable.update_idletasks()
                widget_width = self.invoiceTable.winfo_width()
            except tk.TclError:
                return
            if widget_width <= 1:
                return
            fixed = sum(
                120 + ui_style.CENTER_PAD_WIDTH
                for _c in self._invoice_cols[:-1]
            )
            new_width = max(
                120 + ui_style.CENTER_PAD_WIDTH,
                widget_width - fixed - ui_style._SCROLLBAR_ALLOWANCE
            )
            if new_width == self._invoice_last_col_width:
                return
            self._invoice_last_col_width = new_width
            try:
                self.invoiceTable.column_width(column=len(self._invoice_cols) - 1, width=new_width)
            except tk.TclError:
                pass

        self.invoiceTable.after(200, _stretch_invoice_last_column)
        self.frame.winfo_toplevel().bind("<Configure>", _stretch_invoice_last_column, add=True)

    def _on_supplier_confirm(self, event=None):
        """bind_search_combo's on_confirm - fires on Enter, a dropdown
        pick, AND every plain FocusOut (cursor merely leaving the
        Supplier Name box, for any reason at all - clicking the Load
        Ledger button included, since a button click always blurs
        whatever field had focus first).

        2026-09-01 real bug report (screenshots): "Please select or
        enter a supplier name" kept popping up seemingly at random. Root
        cause - this used to call load_ledger() unconditionally, and
        load_ledger() pops that exact error whenever the box is empty.
        Every screen visit rebuilds Supplier Ledger from scratch (see
        dashboard.py's open_module() note on why screens aren't cached),
        so the box is ALWAYS empty right after navigating back to this
        screen - a completely harmless FocusOut (tabbing past it,
        clicking Load Ledger itself, clicking any other field) was
        enough to trigger the popup with no real "load" intent behind
        it at all.

        Fix: only auto-load here when a supplier is actually present.
        The explicit "Load Ledger" button still calls load_ledger()
        directly (unchanged) and still correctly shows the error for a
        genuinely blank, deliberate load attempt."""
        if self.supplier_name.get().strip():
            self.load_ledger()

    def load_ledger(self):
        sup = self.supplier_name.get().strip()
        if not sup:
            ui_popups.show_error(self.frame, "Error", "Please select or enter a supplier name.")
            return

        self.ledgerTable.delete(*self.ledgerTable.get_children())

        try:
            purchases = repo.get_purchase_like_rows(sup)

            tot_purchase = 0.0
            tot_paid = 0.0
            tot_gst = 0.0

            for p in purchases:
                b_no, b_date, p_total, supp_inv_no = p
                amt = float(p_total or 0.0)
                tot_purchase += amt

                taxable = amt / 1.12
                gst_amt = amt - taxable
                cgst = gst_amt / 2
                sgst = gst_amt / 2
                tot_gst += gst_amt

                self.ledgerTable.insert("", "end", values=(
                    b_date, b_no, supp_inv_no or "-", "Purchase", f"₹ {amt:.2f}",
                    f"₹ {cgst:.2f}", f"₹ {sgst:.2f}", "-"
                ))

            repo.ensure_supplier_payments_schema()
            payments = repo.get_supplier_payments_like(sup)

            for pay in payments:
                p_date, p_amt = pay
                paid = float(p_amt or 0.0)
                tot_paid += paid

                self.ledgerTable.insert("", "end", values=(
                    p_date, "PAYMENT", "-", "Payment", "-", "-", "-", f"₹ {paid:.2f}"
                ))

            balance = tot_purchase - tot_paid

            self.total_purchase.set(f"₹ {tot_purchase:,.2f}")
            self.total_gst_itc.set(f"₹ {tot_gst:,.2f}")
            self.total_paid.set(f"₹ {tot_paid:,.2f}")
            self.balance_due.set(f"₹ {balance:,.2f}")

        except Exception as e:
            ui_popups.show_error(self.frame, "Error", f"லெட்ஜர் லோட் செய்வதில் பிழை:\n{e}")

        # Independent of the flexible-scan block above - reads the
        # actual `purchase` table by exact name, so an exact supplier
        # match (not the LIKE-based fuzzy match above) is used here.
        self.render_invoice_status(sup)

    # ==========================================
    # INVOICE-WISE DUE / OVERDUE STATUS
    # ==========================================

    def compute_invoice_status(self, supplier_name):
        """
        Groups `purchase` rows into one entry per real invoice (by
        bill_no), then allocates the supplier's total payments against
        those invoices oldest-first (FIFO) to work out how much of each
        invoice is still outstanding.

        Deliberately recomputed from scratch every call instead of
        persisting a payment-to-invoice mapping: with only a running
        payment pool spent oldest-invoice-first, editing/deleting a
        payment or adding a late purchase entry can never leave a stale
        allocation behind - the numbers are always consistent with
        whatever's in `purchase`/`supplier_payments` right now.

        Returns a list of (bill_no, bill_date, amount, paid, balance,
        due_date, status) tuples, oldest invoice first. status is one of
        "Paid", "Due", "Overdue".
        """
        invoice_rows = repo.get_invoice_rows(supplier_name)
        payment_pool = repo.get_total_payments(supplier_name)

        def _parse_ddmmyyyy(text):
            # bill_date/due_date are both stored "DD-MM-YYYY" (see
            # purchase.py) - plain text sort on that format is WRONG
            # (year is last), so every date used for ordering or
            # due-vs-today comparison here is parsed first. Unparseable/
            # missing dates sort last rather than raising, so one bad
            # row can't break the whole ledger view.
            try:
                return datetime.strptime((text or "").strip(), "%d-%m-%Y")
            except Exception:
                return None

        invoice_rows.sort(key=lambda r: _parse_ddmmyyyy(r[1]) or datetime.max)

        today = datetime.today()
        results = []
        for bill_no, bill_date, due_date, amount in invoice_rows:
            amount = round(float(amount or 0), 2)
            paid = round(min(amount, payment_pool), 2)
            payment_pool = round(payment_pool - paid, 2)
            balance = round(amount - paid, 2)

            if balance <= 0.01:
                status = "Paid"
            else:
                due_dt = _parse_ddmmyyyy(due_date)
                # No due_date at all means this invoice predates the
                # due-date migration, or the supplier had no credit
                # period recorded at the time - "Due" (not "Overdue",
                # which would be an unverified guess) until that's fixed.
                status = "Overdue" if (due_dt and due_dt < today) else "Due"

            results.append((bill_no, bill_date, amount, paid, balance, due_date or "-", status))

        return results

    def render_invoice_status(self, supplier_name):
        invoices = self.compute_invoice_status(supplier_name)

        data = []
        overdue_rows, due_rows, paid_rows = [], [], []
        for i, (bill_no, bill_date, amount, paid, balance, due_date, status) in enumerate(invoices):
            data.append([bill_no, bill_date, f"₹ {amount:,.2f}", f"₹ {paid:,.2f}", f"₹ {balance:,.2f}", due_date, status])
            if status == "Overdue":
                overdue_rows.append(i)
            elif status == "Paid":
                paid_rows.append(i)
            else:
                due_rows.append(i)

        self.invoiceTable.set_sheet_data(data, reset_col_positions=False, reset_row_positions=True, reset_highlights=True)
        if overdue_rows:
            self.invoiceTable.highlight_rows(rows=overdue_rows, bg="#FFCDD2", fg="#B71C1C")
        if due_rows:
            self.invoiceTable.highlight_rows(rows=due_rows, bg="#FFF3CD", fg="black")
        if paid_rows:
            self.invoiceTable.highlight_rows(rows=paid_rows, bg="#C8E6C9", fg="#1B5E20")

    def make_payment(self):
        sup = self.supplier_name.get().strip()
        amt = self.pay_amount.get()
        p_date = datetime.now().strftime("%Y-%m-%d")

        if not sup:
            ui_popups.show_error(self.frame, "Error", "Please select a supplier.")
            return

        if amt <= 0:
            ui_popups.show_error(self.frame, "Error", "Enter a valid payment amount greater than zero.")
            return

        try:
            repo.insert_supplier_payment(sup, amt, p_date, self.pay_mode.get())
            ui_popups.show_info(self.frame, "Success", f"Payment of ₹ {amt:.2f} ({self.pay_mode.get()}) recorded for {sup} successfully!")
            self.pay_amount.set(0.0)
            self.pay_mode.set("Cash")
            self.load_ledger()
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", str(e))