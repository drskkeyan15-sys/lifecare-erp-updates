import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from datetime import datetime, timedelta

from app_paths import DB_NAME
from expiry_return import record_expiry_return
import ui_style
import theme
import ui_popups


class SmartAlertsDashboard(tk.Frame):
    """
    Reusable Smart Alerts panel: Low Stock + Expiry (expired / expiring soon).
    Usage (matches MainDashboard.open_smart_alerts):
        alert_ui = SmartAlertsDashboard(self.content_frame)
        alert_ui.pack(fill="both", expand=True)
    """

    LOW_STOCK_THRESHOLD = 10
    EXPIRY_WARNING_DAYS = 90

    # Predictive Inventory / Smart Reorder (Aug 2026) - see the "Reorder
    # Predictions" tab below. REORDER_WINDOW_DAYS is how far back sales
    # history is looked at to compute an average daily usage;
    # REORDER_LEAD_DAYS is the default "alert if stock will run out
    # within this many days" threshold (editable in the tab itself, same
    # pattern as the existing Expiry window Spinbox above).
    REORDER_WINDOW_DAYS = 30
    REORDER_LEAD_DAYS = 15

    def __init__(self, parent, on_create_po=None):
        super().__init__(parent, bg="white")
        # ROOT CAUSE OF THE BLANK-SCREEN BUG: this class IS the Frame
        # (extends tk.Frame directly), unlike every other module screen
        # (MedicineMaster, Stock, etc.) which creates its OWN internal
        # self.frame and packs that. This class's docstring documents the
        # OLD calling convention - the caller was expected to do
        # `alert_ui = SmartAlertsDashboard(parent); alert_ui.pack(...)`.
        # When open_smart_alerts() was switched to go through the shared
        # open_module() (to fix the earlier "rows inserted before the
        # widget was mapped" bug), open_module() constructs the module
        # and discards the return value - it never calls .pack() on it,
        # because every OTHER module already packs its own internal frame
        # inside __init__. So this entire widget tree (title, cards,
        # notebook, tables - everything) was being built correctly in
        # memory but never placed on screen: no exception, no terminal
        # output, just permanently invisible. Packing self here fixes it
        # and matches the convention every other module already follows.
        self.pack(fill="both", expand=True)
        self.expiry_days = tk.IntVar(value=self.EXPIRY_WARNING_DAYS)

        # Optional hand-off into the Purchase Order screen (Aug 2026) -
        # dashboard.py's open_module() only passes this kwarg through if
        # it sees "on_create_po" in this __init__'s signature (same
        # optional-callback convention as on_close/on_open_medicine_master
        # elsewhere in the app), so this class still works fine when
        # constructed directly without it (falls back to a message - see
        # _hand_off_to_purchase_order below).
        self.on_create_po = on_create_po

        self.create_ui()
        self.refresh()

    # ==========================================
    # UI
    # ==========================================

    def create_ui(self):
        title = tk.Label(
            self,
            text="SMART ALERTS",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        # ---------------- Summary cards ----------------
        cards = tk.Frame(self, bg="white")
        cards.pack(fill="x", padx=10, pady=10)

        self.lowStockCard = self._make_card(cards, "Low Stock Items", "#f39c12", 0)
        self.expiredCard = self._make_card(cards, "Expired Items", "#e74c3c", 1)
        self.expiringCard = self._make_card(cards, "Expiring Soon", "#e67e22", 2)

        # ---------------- Controls ----------------
        controls = tk.LabelFrame(self, text="Alert Settings", font=("Segoe UI", 10, "bold"), bg="white")
        controls.pack(fill="x", padx=10, pady=(0, 5))

        tk.Label(controls, text="Expiry window (days):", bg="white").pack(side="left", padx=(0, 5))
        spin = tk.Spinbox(controls, from_=7, to=365, width=6, textvariable=self.expiry_days,
                           command=self.refresh)
        spin.pack(side="left")
        spin.bind("<Return>", lambda e: self.refresh())

        tk.Button(
            controls, text="Refresh", bg="#1565C0", fg="white", width=12,
            command=self.refresh
        ).pack(side="left", padx=10)

        tk.Button(
            controls, text="Copy Reorder List", bg="#EF6C00", fg="white", width=18,
            command=self.copy_reorder_list
        ).pack(side="left", padx=5)

        # ---------------- Tabs ----------------
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        low_tab = tk.Frame(notebook, bg="white")
        expiry_tab = tk.Frame(notebook, bg="white")
        return_tab = tk.Frame(notebook, bg="white")
        prediction_tab = tk.Frame(notebook, bg="white")
        notebook.add(low_tab, text="Low Stock")
        notebook.add(expiry_tab, text="Expiry")
        notebook.add(return_tab, text="Distributor Return")
        notebook.add(prediction_tab, text="Reorder Predictions")

        self.lowStockTable = self._make_table(
            low_tab,
            ("Medicine", "Company", "Batch", "Rack", "Stock", "Reorder Level", "Suggested Qty", "Last Supplier")
        )
        self.expiryTable = self._make_table(
            expiry_tab, ("Medicine", "Company", "Batch", "Expiry", "Days Left", "Stock", "Status")
        )
        self.expiryTable.tag_configure("expired", background="#e74c3c", foreground="white")
        self.expiryTable.tag_configure("soon", background="#FFF3CD")

        # ---------------- Distributor Return tab ----------------
        return_controls = tk.Frame(return_tab, bg="white")
        return_controls.pack(fill="x", pady=(0, 5))
        tk.Label(
            return_controls,
            text="Same near-expiry window as the Expiry tab, grouped by the supplier "
                 "each batch was last purchased from - so you know who to send it back to.",
            bg="white", fg="#555555", font=("Segoe UI", 9)
        ).pack(side="left", padx=5)

        return_table_frame = tk.Frame(return_tab, bg="white")
        return_table_frame.pack(fill="both", expand=True)
        self.returnTable = self._make_table(
            return_table_frame,
            ("Supplier", "Medicine", "Company", "Batch", "Expiry", "Days Left", "Stock", "Status")
        )
        self.returnTable.tag_configure("expired", background="#e74c3c", foreground="white")
        self.returnTable.tag_configure("soon", background="#FFF3CD")

        return_btns = tk.Frame(return_tab, bg="white")
        return_btns.pack(fill="x", pady=5)
        tk.Button(
            return_btns, text="Create Return for Selected", bg="#C62828", fg="white", width=22,
            command=self.create_return_for_selected
        ).pack(side="left", padx=5)
        tk.Button(
            return_btns, text="Copy Distributor Return List", bg="#EF6C00", fg="white", width=24,
            command=self.copy_distributor_return_list
        ).pack(side="left", padx=5)

        # ---------------- Reorder Predictions tab (Aug 2026) ----------------
        # Unlike the Low Stock tab above (a flat "stock <= threshold,
        # refill to threshold" rule - see load_low_stock()'s own comment),
        # this tab is demand-based: it looks at how fast each medicine has
        # actually been selling over the last REORDER_WINDOW_DAYS days and
        # predicts when it will run out, so a fast-moving medicine that's
        # still numerically "above threshold" but selling out fast can
        # still surface here before it becomes a Low Stock emergency.
        pred_controls = tk.LabelFrame(
            prediction_tab, text="Prediction Settings", font=("Segoe UI", 10, "bold"), bg="white"
        )
        pred_controls.pack(fill="x", pady=(0, 5))

        tk.Label(
            pred_controls, text="Alert if stock runs out within (days):", bg="white"
        ).pack(side="left", padx=(5, 5))
        self.reorder_lead_days = tk.IntVar(value=self.REORDER_LEAD_DAYS)
        spin2 = tk.Spinbox(
            pred_controls, from_=1, to=90, width=6, textvariable=self.reorder_lead_days,
            command=self.load_reorder_predictions
        )
        spin2.pack(side="left")
        spin2.bind("<Return>", lambda e: self.load_reorder_predictions())

        tk.Button(
            pred_controls, text="Refresh", bg="#1565C0", fg="white", width=12,
            command=self.load_reorder_predictions
        ).pack(side="left", padx=10)
        tk.Button(
            pred_controls, text="Copy List", bg="#EF6C00", fg="white", width=12,
            command=self.copy_prediction_list
        ).pack(side="left", padx=5)
        tk.Button(
            pred_controls, text="Create PO for Selected", bg="#2E7D32", fg="white", width=20,
            command=self.create_po_for_selected_predictions
        ).pack(side="left", padx=5)
        tk.Button(
            pred_controls, text="Create PO for All", bg="#2E7D32", fg="white", width=16,
            command=self.create_po_for_all_predictions
        ).pack(side="left", padx=5)

        tk.Label(
            prediction_tab,
            text="Suggestion only, based on average daily usage over the last "
                 f"{self.REORDER_WINDOW_DAYS} days of sales - adjust quantities before saving the "
                 "Purchase Order. A medicine with no recent sales won't show here (not enough data to predict from).",
            bg="white", fg="#555555", font=("Segoe UI", 9), wraplength=900, justify="left"
        ).pack(fill="x", padx=5, pady=(0, 5), anchor="w")

        pred_table_frame = tk.Frame(prediction_tab, bg="white")
        pred_table_frame.pack(fill="both", expand=True)
        self.predictionTable = self._make_table(
            pred_table_frame,
            ("Medicine", "Avg Daily Usage", "Current Stock", "Days Remaining", "Suggested Reorder Qty", "Last Supplier")
        )

    def _make_card(self, parent, title, color, col):
        frame = tk.Frame(parent, bg=color, width=220, height=90)
        frame.grid(row=0, column=col, padx=10, sticky="w")
        frame.grid_propagate(False)

        tk.Label(frame, text=title, bg=color, fg="white", font=("Segoe UI", 11, "bold")).pack(pady=(10, 0))
        value = tk.Label(frame, text="0", bg=color, fg="white", font=("Segoe UI", 22, "bold"))
        value.pack()
        return value

    # Text (left-align) vs numeric (right-align, tksheet's default for
    # anything not listed) columns across all four tables this screen
    # builds - kept as one shared set rather than per-table lists since
    # the same column name always means the same kind of data everywhere
    # it appears (e.g. "Batch" is always text, "Stock" always a number).
    _TEXT_COLUMNS = {
        "Medicine", "Company", "Batch", "Rack", "Last Supplier",
        "Expiry", "Status", "Supplier",
    }

    def _make_table(self, parent, cols):
        # Excel-grid look (colored header, zebra rows, visible cell
        # borders) matching Medicine Master/Stock/Reports. This was
        # rolled back to plain ttk.Treeview earlier while chasing the
        # Smart-Alerts-blank-screen bug, but that bug's real cause was
        # SmartAlertsDashboard never calling self.pack() on itself (fixed
        # in __init__ above) - the table widget was never the problem, so
        # it's safe to bring the Excel-grid version back now.
        col_widths = {c: (220 if c == "Medicine" else 140) for c in cols}
        text_columns = tuple(c for c in cols if c in self._TEXT_COLUMNS)
        # 2026-08-30: switched from make_excel_sheet() (tksheet) to
        # make_plain_sheet() (plain ttk.Treeview) - see medicine_master.py's
        # ui_style.PlainSheet docstring for the full rationale.
        # SheetTreeAdapter below only calls methods PlainSheet answers
        # to identically, so it keeps working unchanged on top of
        # either grid technology.
        sheet = ui_style.make_plain_sheet(parent, cols, col_widths, text_columns=text_columns)
        sheet.enable_bindings(*ui_style.READONLY_BINDINGS)
        sheet.pack(side="left", fill="both", expand=True)
        return ui_style.SheetTreeAdapter(sheet, columns=cols, col_widths=col_widths, stretch=True)

    # ==========================================
    # DATA
    # ==========================================

    def refresh(self):
        self.load_low_stock()
        self.load_expiry_alerts()
        self.load_distributor_returns()
        self.load_reorder_predictions()

    def load_low_stock(self):
        self.lowStockTable.delete(*self.lowStockTable.get_children())

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        # Each medicine can set its own reorder_level in Medicine Master
        # (Reorder Level field) - a row that hasn't set one (0/NULL)
        # falls back to the fixed LOW_STOCK_THRESHOLD, same as this
        # screen's behaviour before reorder_level existed.
        cur.execute("""
            SELECT name, company, batch, rack, stock,
                   CASE WHEN reorder_level > 0 THEN reorder_level ELSE ? END AS effective_threshold
            FROM medicine_master
            WHERE stock <= CASE WHEN reorder_level > 0 THEN reorder_level ELSE ? END
            ORDER BY stock ASC
        """, (self.LOW_STOCK_THRESHOLD, self.LOW_STOCK_THRESHOLD))
        rows = cur.fetchall()

        # Last supplier used for each medicine name - looked up from
        # purchase history, most recent by id (purchase rows are
        # inserted in real submission order, unlike bill_date which is
        # free-text "DD-MM-YYYY" and sorts wrong as plain text).
        last_supplier = {}
        names = sorted({r[0] for r in rows})
        if names:
            placeholders = ",".join("?" * len(names))
            cur.execute(f"""
                SELECT medicine, supplier FROM purchase
                WHERE medicine IN ({placeholders})
                AND id IN (SELECT MAX(id) FROM purchase WHERE medicine IN ({placeholders}) GROUP BY medicine)
            """, names + names)
            for medicine, supplier in cur.fetchall():
                last_supplier[medicine] = supplier

        con.close()

        # Cached for copy_reorder_list() so it doesn't have to re-query -
        # it copies exactly what's currently on screen.
        self._reorder_rows = []

        for name, company, batch, rack, stock, threshold in rows:
            # Simple, transparent heuristic: order enough to bring stock
            # back up to the threshold - NOT demand forecasting (that
            # would need sales-velocity history), just a starting number
            # the pharmacist can adjust in Purchase Entry. (Demand-based
            # forecasting now lives in the separate "Reorder Predictions"
            # tab below - see load_reorder_predictions().)
            suggested_qty = max(threshold - stock, 1)
            supplier = last_supplier.get(name) or "-"
            self._reorder_rows.append((name, company, batch, rack, stock, threshold, suggested_qty, supplier))
            self.lowStockTable.insert(
                "", "end",
                values=(name, company, batch, rack, stock, threshold, suggested_qty, supplier)
            )

        self.lowStockCard.config(text=str(len(rows)))

    def copy_reorder_list(self):
        """Copies the current Low Stock list to the clipboard as plain
        text, grouped by last-known supplier, so the pharmacist can
        paste it straight into WhatsApp/SMS/email to place the order -
        no distributor marketplace integration here, just a fast way to
        turn "what's short" into "what to type to the supplier"."""
        if not getattr(self, "_reorder_rows", None):
            ui_popups.show_info(self, "Nothing to Copy", "No low-stock items to copy right now.")
            return

        by_supplier = {}
        for name, company, batch, rack, stock, threshold, suggested_qty, supplier in self._reorder_rows:
            by_supplier.setdefault(supplier, []).append((name, suggested_qty))

        lines = ["REORDER LIST", "=" * 30]
        for supplier in sorted(by_supplier):
            lines.append(f"\n{supplier}:")
            for name, qty in by_supplier[supplier]:
                lines.append(f"  - {name}  x {qty}")

        text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(text)
        ui_popups.show_info(self, "Copied", f"Reorder list copied to clipboard ({len(self._reorder_rows)} item(s)).")

    def _compute_expiry_rows(self, window_days):
        """
        Shared near-expiry query used by both the Expiry tab and the
        Distributor Return tab, so the two never drift apart on what
        counts as "expired" / "expiring soon". Returns a list of
        (exp_dt, name, company, batch, expiry, days_left, stock, status)
        tuples, earliest expiry first.
        """
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT name, company, batch, expiry, stock
            FROM medicine_master
            WHERE stock > 0 AND expiry <> ''
        """)
        rows = cur.fetchall()
        con.close()

        today = datetime.now().replace(day=1)
        cutoff = (datetime.now() + timedelta(days=window_days)).replace(day=1)

        results = []
        for name, company, batch, expiry, stock in rows:
            try:
                exp_dt = datetime.strptime(expiry, "%m/%y").replace(day=1)
            except Exception:
                continue  # unparsable expiry, skip rather than guess

            if exp_dt > cutoff:
                continue  # outside the alert window, not shown

            days_left = (exp_dt - today).days
            status = "EXPIRED" if exp_dt < today else "EXPIRING SOON"
            results.append((exp_dt, name, company, batch, expiry, days_left, stock, status))

        results.sort(key=lambda r: r[0])
        return results

    def load_expiry_alerts(self):
        self.expiryTable.delete(*self.expiryTable.get_children())

        try:
            window_days = int(self.expiry_days.get())
        except Exception:
            window_days = self.EXPIRY_WARNING_DAYS

        results = self._compute_expiry_rows(window_days)
        expired_count = sum(1 for r in results if r[7] == "EXPIRED")
        expiring_count = len(results) - expired_count

        for exp_dt, name, company, batch, expiry, days_left, stock, status in results:
            tag = "expired" if status == "EXPIRED" else "soon"
            self.expiryTable.insert(
                "", "end",
                values=(name, company, batch, expiry, days_left, stock, status),
                tags=(tag,)
            )

        self.expiredCard.config(text=str(expired_count))
        self.expiringCard.config(text=str(expiring_count))

    # ==========================================
    # DISTRIBUTOR RETURN TAB
    # ==========================================

    def load_distributor_returns(self):
        self.returnTable.delete(*self.returnTable.get_children())

        try:
            window_days = int(self.expiry_days.get())
        except Exception:
            window_days = self.EXPIRY_WARNING_DAYS

        results = self._compute_expiry_rows(window_days)
        if not results:
            self._return_rows = []
            return

        # Supplier per (medicine, batch) - most recent matching purchase
        # row. Falls back to medicine-only (ignoring batch) if no exact
        # batch match exists, since older stock may predate batch-level
        # purchase records; "Unknown" if neither matches (e.g. medicine
        # was entered directly in Medicine Master, never through Purchase).
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        names = sorted({r[1] for r in results})
        placeholders = ",".join("?" * len(names))

        cur.execute(f"""
            SELECT medicine, batch, supplier FROM purchase
            WHERE medicine IN ({placeholders})
            AND id IN (SELECT MAX(id) FROM purchase WHERE medicine IN ({placeholders}) GROUP BY medicine, batch)
        """, names + names)
        supplier_by_medicine_batch = {(m, b): s for m, b, s in cur.fetchall()}

        cur.execute(f"""
            SELECT medicine, supplier FROM purchase
            WHERE medicine IN ({placeholders})
            AND id IN (SELECT MAX(id) FROM purchase WHERE medicine IN ({placeholders}) GROUP BY medicine)
        """, names + names)
        supplier_by_medicine = {m: s for m, s in cur.fetchall()}
        con.close()

        # Group by supplier (alphabetical), earliest expiry first within
        # each supplier - mirrors how the pharmacist would work through
        # this list one distributor call/WhatsApp message at a time.
        self._return_rows = []
        for exp_dt, name, company, batch, expiry, days_left, stock, status in results:
            supplier = supplier_by_medicine_batch.get((name, batch)) or supplier_by_medicine.get(name) or "Unknown"
            self._return_rows.append((supplier, name, company, batch, expiry, days_left, stock, status))

        self._return_rows.sort(key=lambda r: (r[0], r[4]))

        for supplier, name, company, batch, expiry, days_left, stock, status in self._return_rows:
            tag = "expired" if status == "EXPIRED" else "soon"
            self.returnTable.insert(
                "", "end",
                values=(supplier, name, company, batch, expiry, days_left, stock, status),
                tags=(tag,)
            )

    def copy_distributor_return_list(self):
        """Same idea as Copy Reorder List - a fast, no-integration way to
        turn "what's expiring" into a message you can paste straight into
        WhatsApp/SMS to each distributor, grouped so one message per
        supplier covers everything they need to take back."""
        if not getattr(self, "_return_rows", None):
            ui_popups.show_info(self, "Nothing to Copy", "No near-expiry items to copy right now.")
            return

        by_supplier = {}
        for supplier, name, company, batch, expiry, days_left, stock, status in self._return_rows:
            by_supplier.setdefault(supplier, []).append((name, batch, expiry, stock, status))

        lines = ["DISTRIBUTOR RETURN LIST (Near-Expiry / Expired Stock)", "=" * 50]
        for supplier in sorted(by_supplier):
            lines.append(f"\n{supplier}:")
            for name, batch, expiry, stock, status in by_supplier[supplier]:
                lines.append(f"  - {name} (Batch {batch or '-'}, Exp {expiry}) x {stock}  [{status}]")

        text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(text)
        ui_popups.show_info(self, "Copied", f"Distributor return list copied to clipboard ({len(self._return_rows)} item(s)).")

    def create_return_for_selected(self):
        selection = self.returnTable.selection()
        if not selection:
            ui_popups.show_info(self, "Select a Row", "Select an item from the Distributor Return list first.")
            return

        supplier, medicine, company, batch, expiry, days_left, stock, status = self.returnTable.item(selection[0])["values"]

        win = tk.Toplevel(self)
        win.title("Create Distributor Return")
        win.resizable(False, False)
        win.grab_set()

        def _close():
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _close)

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring).
        outer = ui_style.popup_header(win, "Confirm Return to Supplier", icon="↩")
        body = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=15, pady=12)
        body.pack(fill="both", expand=True)

        info = tk.Frame(body, bg=theme.SURFACE_WHITE)
        info.pack(fill="x")
        for label, value in (
            ("Supplier", supplier), ("Medicine", medicine),
            ("Batch", batch or "-"), ("Expiry", expiry), ("Status", status),
        ):
            row = tk.Frame(info, bg=theme.SURFACE_WHITE)
            row.pack(fill="x", pady=2)
            tk.Label(
                row, text=f"{label}:", bg=theme.SURFACE_WHITE, width=12, anchor="w",
                font=("Segoe UI", 10, "bold"),
            ).pack(side="left")
            tk.Label(row, text=str(value), bg=theme.SURFACE_WHITE, anchor="w").pack(side="left")

        qty_var = tk.IntVar(value=int(stock))
        qty_row = tk.Frame(info, bg=theme.SURFACE_WHITE)
        qty_row.pack(fill="x", pady=(8, 2))
        tk.Label(
            qty_row, text="Return Qty:", bg=theme.SURFACE_WHITE, width=12, anchor="w",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")
        tk.Entry(
            qty_row, textvariable=qty_var, width=10, font=("Segoe UI", 10),
            bg=theme.SURFACE_FIELD, relief="flat", highlightthickness=1,
            highlightbackground=theme.BORDER_DEFAULT, highlightcolor=theme.BORDER_FOCUS,
        ).pack(side="left", ipady=3)
        tk.Label(
            body, text=f"(current stock: {stock} - defaults to returning all of it)",
            bg=theme.SURFACE_WHITE, fg=theme.TEXT_MUTED, font=("Segoe UI", 8)
        ).pack(anchor="w", pady=(4, 0))

        credit_note = f"CN-{datetime.now().strftime('%Y%m%d%H%M')}"

        def _confirm():
            try:
                q = int(qty_var.get())
            except Exception:
                ui_popups.show_error(win, "Error", "Enter a valid quantity.")
                return
            try:
                record_expiry_return(credit_note, supplier, medicine, batch, q, DB_NAME)
            except ValueError as e:
                ui_popups.show_error(win, "Error", str(e))
                return
            ui_popups.show_info(win, 
                "Return Recorded",
                f"{q} x {medicine} returned to {supplier}.\nCredit Note No: {credit_note}\n\n"
                "See the 'Expiry Return' screen for the full return history."
            )
            _close()
            self.refresh()

        btns = tk.Frame(body, bg=theme.SURFACE_WHITE)
        btns.pack(fill="x", pady=(15, 0))
        ui_style.flat_button(btns, "Confirm Return", theme.STATUS_DANGER, _confirm).pack(side="left")
        ui_style.flat_button(btns, "Cancel", theme.ACCENT_NEUTRAL, _close).pack(side="left", padx=(8, 0))

        # No explicit width/height (was a fixed 400x300 guess) - see
        # ui_style.center_window()'s own docstring for why sizing to
        # real packed content after building it is safer.
        ui_style.center_window(win, parent=self.winfo_toplevel())

    # ==========================================
    # REORDER PREDICTIONS TAB (Predictive Inventory / Smart Reorder,
    # Aug 2026 - see advanced_features_plan.md's Section 1 for the full
    # design writeup this implements)
    # ==========================================

    def load_reorder_predictions(self):
        """Demand-based reorder alert: how fast has each medicine
        actually been selling over the last REORDER_WINDOW_DAYS days, and
        at that rate, will it run out within the pharmacist-chosen lead
        time? Deliberately separate from load_low_stock()'s flat
        threshold rule above - a fast mover can show up here before it
        ever dips under its numeric reorder_level, and a slow mover
        sitting just under threshold (e.g. one unit left of something
        that sells once a month) will correctly NOT show up here even
        though it's in the Low Stock tab."""
        self.predictionTable.delete(*self.predictionTable.get_children())

        try:
            lead_days = int(self.reorder_lead_days.get())
        except Exception:
            lead_days = self.REORDER_LEAD_DAYS

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()

        # Current total stock per medicine, summed across every batch row
        # - medicine_master is batch-level (one medicine name can have
        # several rows, one per purchase/batch), so a per-medicine
        # prediction needs the combined stock across all of them, not
        # just whichever single row happens to be looked at.
        cur.execute("SELECT name, SUM(stock) FROM medicine_master GROUP BY name")
        stock_by_name = dict(cur.fetchall())

        # Raw sales history for usage-rate calculation. sales.bill_date is
        # stored as free-text "YYYY-MM-DD" (billing.py's save_bill()
        # hardcodes datetime.now().strftime("%Y-%m-%d") on INSERT,
        # regardless of what Billing's own self.bill_date DD-MM-YYYY
        # DISPLAY StringVar shows - confirmed against live data by
        # billing.py's own load_quick_picks() docstring, and by
        # reports.py's slow_moving_report() docstring, which documents
        # this exact DD-MM-YYYY assumption being wrong and fixed there
        # once already). Still parsed by hand rather than trusted via SQL
        # date functions/string sort order (matches both of those
        # methods' own approach), in case the stored format ever changes
        # again - just with the CORRECT format string this time.
        cur.execute("""
            SELECT s.bill_date, si.medicine, si.qty
            FROM sales_items si
            JOIN sales s ON si.bill_no = s.bill_no
        """)
        sale_rows = cur.fetchall()

        # Last supplier per medicine - identical lookup to load_low_stock()
        # above (most recent purchase.id, not bill_date - see that
        # method's own comment for why).
        names_with_stock = sorted(stock_by_name.keys())
        last_supplier = {}
        if names_with_stock:
            placeholders = ",".join("?" * len(names_with_stock))
            cur.execute(f"""
                SELECT medicine, supplier FROM purchase
                WHERE medicine IN ({placeholders})
                AND id IN (SELECT MAX(id) FROM purchase WHERE medicine IN ({placeholders}) GROUP BY medicine)
            """, names_with_stock + names_with_stock)
            for medicine, supplier in cur.fetchall():
                last_supplier[medicine] = supplier

        con.close()

        cutoff = datetime.now() - timedelta(days=self.REORDER_WINDOW_DAYS)
        usage_qty = {}
        for bill_date_str, medicine, qty in sale_rows:
            try:
                d = datetime.strptime((bill_date_str or "").strip(), "%Y-%m-%d")
            except (ValueError, TypeError):
                continue  # blank/unparsable date - skip rather than guess
            if d >= cutoff:
                usage_qty[medicine] = usage_qty.get(medicine, 0) + (qty or 0)

        self._prediction_rows = []
        for name, stock in stock_by_name.items():
            total_used = usage_qty.get(name, 0)
            if total_used <= 0:
                continue  # no recent sales - not enough data to predict from
            avg_daily = total_used / self.REORDER_WINDOW_DAYS
            days_remaining = stock / avg_daily
            if days_remaining > lead_days:
                continue  # comfortably stocked for the chosen lead time
            # Suggested qty: enough to cover `lead_days` of usage from
            # zero, minus what's already on hand (never below 1) - this
            # is the demand-based counterpart to load_low_stock()'s flat
            # "refill to threshold" suggestion, not a replacement for it.
            suggested_qty = max(int(round(avg_daily * lead_days)) - int(stock), 1)
            supplier = last_supplier.get(name) or "-"
            self._prediction_rows.append(
                (name, round(avg_daily, 2), int(stock), round(days_remaining, 1), suggested_qty, supplier)
            )

        self._prediction_rows.sort(key=lambda r: r[3])  # soonest stock-out first

        for row in self._prediction_rows:
            self.predictionTable.insert("", "end", values=row)

    def copy_prediction_list(self):
        if not getattr(self, "_prediction_rows", None):
            ui_popups.show_info(self, "Nothing to Copy", "No reorder predictions right now.")
            return

        by_supplier = {}
        for name, avg_daily, stock, days_remaining, suggested_qty, supplier in self._prediction_rows:
            by_supplier.setdefault(supplier, []).append((name, suggested_qty, days_remaining))

        lines = [f"PREDICTED REORDER LIST (based on last {self.REORDER_WINDOW_DAYS} days' sales)", "=" * 30]
        for supplier in sorted(by_supplier):
            lines.append(f"\n{supplier}:")
            for name, qty, days_remaining in by_supplier[supplier]:
                lines.append(f"  - {name}  x {qty}  (~{days_remaining} days stock left)")

        text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(text)
        ui_popups.show_info(self, "Copied", f"Reorder prediction list copied to clipboard ({len(self._prediction_rows)} item(s)).")

    def _selected_prediction_items(self):
        selection = self.predictionTable.selection()
        if not selection:
            ui_popups.show_info(self, 
                "Select Rows",
                "Select one or more rows from the Reorder Predictions list first "
                "(or use 'Create PO for All' to take every row shown)."
            )
            return None
        items = []
        for iid in selection:
            values = self.predictionTable.item(iid)["values"]
            name, avg_daily, stock, days_remaining, suggested_qty, supplier = values
            items.append((name, int(suggested_qty)))
        return items

    def create_po_for_selected_predictions(self):
        items = self._selected_prediction_items()
        if not items:
            return
        self._hand_off_to_purchase_order(items)

    def create_po_for_all_predictions(self):
        if not getattr(self, "_prediction_rows", None):
            ui_popups.show_info(self, "Nothing to Order", "No reorder predictions right now.")
            return
        items = [(name, qty) for name, avg_daily, stock, days_remaining, qty, supplier in self._prediction_rows]
        self._hand_off_to_purchase_order(items)

    def _hand_off_to_purchase_order(self, items):
        """Sends suggested (medicine, qty) items to the Purchase Order
        screen via the on_create_po callback dashboard.py wires up (see
        open_purchase_order_with_items() there, and purchase_order.py's
        own pending_items= parameter on the receiving end). Falls back to
        a plain message if this class was constructed without that
        callback (e.g. directly, outside the normal sidebar navigation)
        rather than silently doing nothing."""
        if not callable(self.on_create_po):
            ui_popups.show_info(self, 
                "Purchase Order",
                "Open Smart Alerts from the sidebar (Inventory -> Smart Alerts) to use "
                "'Create PO' - it hands items off to the Purchase Order screen, which "
                "isn't available in this context."
            )
            return
        self.on_create_po(items)
