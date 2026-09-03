"""
stock_adjustment.py
LifeCare Pharmacy ERP - Stock Adjustment / Write-off.

For stock changes that are NOT a Purchase, a Sale, a Purchase Return, a
Sales Return, or an Expiry Return (those already have their own
workflows/tables) - damage, theft/pilferage, breakage, or a physical
stock-count correction. A signed qty_change on one row covers both
"stock went up" (found more than recorded) and "stock went down"
(found less than recorded, or genuinely lost) cases.

Every save here also calls audit_log.log_action() - a stock write-off is
exactly the kind of change an owner wants an accountability trail for
(who adjusted what, when, and why), on top of the adjustment's own
domain-specific log (stock_adjustments) used for reporting "how much
damage/theft this month".
"""

import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from datetime import datetime

from app_paths import DB_NAME
import audit_log
import session
import ui_style
import ui_popups

REASON_OPTIONS = [
    "Damage", "Theft / Pilferage", "Breakage",
    "Physical Count Correction", "Other",
]


class StockAdjustment:

    def __init__(self, frame):
        self.frame = frame
        self._medicine_names = []
        self._current_stock = 0

        self.create_variables()
        self.create_ui()
        self.load_medicines()
        self.load_history()

    def create_variables(self):
        self.adj_date = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.medicine = tk.StringVar()
        self.batch = tk.StringVar()
        self.stock_label_var = tk.StringVar(value="Current Stock: -")
        self.adj_type = tk.StringVar(value="Remove Stock")
        self.qty = tk.IntVar(value=0)
        self.reason = tk.StringVar(value=REASON_OPTIONS[0])
        self.note = tk.StringVar()

    def create_ui(self):
        tk.Label(
            self.frame, text="STOCK ADJUSTMENT / WRITE-OFF",
            bg="#1565C0", fg="white", font=("Segoe UI", 18, "bold"), pady=10
        ).pack(fill="x")

        form = tk.LabelFrame(self.frame, text="Adjust Stock", font=("Segoe UI", 11, "bold"))
        form.pack(fill="x", padx=10, pady=10)

        tk.Label(form, text="Date").grid(row=0, column=0, padx=5, pady=6, sticky="w")
        tk.Entry(form, textvariable=self.adj_date, width=15).grid(row=0, column=1, padx=5, pady=6, sticky="w")

        tk.Label(form, text="Medicine").grid(row=0, column=2, padx=5, pady=6, sticky="w")
        self.cmbMedicine = ttk.Combobox(form, textvariable=self.medicine, width=28)
        self.cmbMedicine.grid(row=0, column=3, padx=5, pady=6, sticky="w")

        tk.Label(form, text="Batch").grid(row=0, column=4, padx=5, pady=6, sticky="w")
        self.cmbBatch = ttk.Combobox(form, textvariable=self.batch, width=15, state="readonly")
        self.cmbBatch.grid(row=0, column=5, padx=5, pady=6, sticky="w")
        self.cmbBatch.bind("<<ComboboxSelected>>", lambda e: self._on_batch_change())

        tk.Label(form, textvariable=self.stock_label_var, fg="#0D47A1", font=("Segoe UI", 10, "bold")).grid(
            row=1, column=0, columnspan=2, padx=5, pady=(0, 6), sticky="w"
        )

        tk.Label(form, text="Type").grid(row=2, column=0, padx=5, pady=6, sticky="w")
        ttk.Combobox(
            form, textvariable=self.adj_type, width=13, state="readonly",
            values=["Add Stock", "Remove Stock"]
        ).grid(row=2, column=1, padx=5, pady=6, sticky="w")

        tk.Label(form, text="Quantity").grid(row=2, column=2, padx=5, pady=6, sticky="w")
        self.txtQty = tk.Entry(form, textvariable=self.qty, width=10)
        self.txtQty.grid(row=2, column=3, padx=5, pady=6, sticky="w")
        self.txtQty.bind("<Return>", lambda e: self.save_adjustment())

        # ERP-wide keyboard-nav pass (Aug 2026): typing/mouse-click/Enter
        # on Medicine all now resolve through _on_medicine_change() (made
        # to report True/False below) and advance straight to Quantity -
        # Batch is skipped since _on_medicine_change() already auto-picks
        # the earliest batch, matching this screen's existing fast-entry
        # intent; the pharmacist can still Tab/click into Batch to
        # override it. Previously only mouse-click (via <<ComboboxSelected>>)
        # or Tab-away worked at all - there was no Enter-to-confirm and no
        # focus-advance of any kind on this screen.
        ui_style.bind_search_combo(
            self.cmbMedicine,
            on_filter=self._filter_medicine_dropdown,
            on_confirm=self._on_medicine_change,
            next_widget=self.txtQty,
        )

        tk.Label(form, text="Reason").grid(row=2, column=4, padx=5, pady=6, sticky="w")
        ttk.Combobox(
            form, textvariable=self.reason, width=22, state="readonly",
            values=REASON_OPTIONS
        ).grid(row=2, column=5, padx=5, pady=6, sticky="w")

        tk.Label(form, text="Note").grid(row=3, column=0, padx=5, pady=6, sticky="w")
        tk.Entry(form, textvariable=self.note, width=60).grid(
            row=3, column=1, columnspan=4, padx=5, pady=6, sticky="w"
        )

        tk.Button(
            form, text="Save Adjustment", bg="green", fg="white", width=16,
            command=self.save_adjustment
        ).grid(row=3, column=5, padx=5, pady=6, sticky="w")

        # ---- History ----
        hist_frame = tk.LabelFrame(self.frame, text="Recent Adjustments", font=("Segoe UI", 10, "bold"))
        hist_frame.pack(fill="both", expand=True, padx=10, pady=10)

        cols = ("Date", "Medicine", "Batch", "Qty Change", "Reason", "Note", "Adjusted By")
        self.historyTable = ttk.Treeview(hist_frame, columns=cols, show="headings", height=14, style="ERP.Treeview")
        widths = {"Date": 90, "Medicine": 180, "Batch": 90, "Qty Change": 90, "Reason": 160, "Note": 220, "Adjusted By": 110}
        for c in cols:
            self.historyTable.heading(c, text=c)
            self.historyTable.column(c, width=widths[c], anchor="w")

        vscroll = ttk.Scrollbar(hist_frame, orient="vertical", command=self.historyTable.yview)
        self.historyTable.configure(yscrollcommand=vscroll.set)
        self.historyTable.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

    # ---------------- DATA ----------------

    def load_medicines(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT DISTINCT name FROM medicine_master ORDER BY name")
        self._medicine_names = [r[0] for r in cur.fetchall()]
        con.close()
        self.cmbMedicine["values"] = self._medicine_names

    def _filter_medicine_dropdown(self, typed_text):
        # Nav/confirm keys (Up/Down/Return/Escape/Tab/...) are already
        # filtered out upstream by ui_style.bind_search_combo() before
        # this is even called - no need to re-check event.keysym here.
        typed = typed_text.lower()
        self.cmbMedicine["values"] = (
            self._medicine_names if not typed
            else [n for n in self._medicine_names if typed in n.lower()]
        )

    def _on_medicine_change(self, event=None):
        """bind_search_combo()'s on_confirm for cmbMedicine - also used
        directly by <FocusOut> as before. Returns True (and auto-picks
        the earliest batch) only when the medicine has at least one real
        batch/stock row to adjust - a name with none (never purchased,
        or a stray typo) cannot advance to Quantity, since there is
        nothing on record to adjust."""
        name = self.medicine.get().strip()
        if not name:
            return False
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT batch, stock FROM medicine_master WHERE name=? ORDER BY expiry", (name,))
        rows = cur.fetchall()
        con.close()

        self._batch_stock = {b: s for b, s in rows}
        self.cmbBatch["values"] = list(self._batch_stock.keys())
        if rows:
            self.batch.set(rows[0][0])
            self._on_batch_change()
            return True
        else:
            self.batch.set("")
            self._current_stock = 0
            self.stock_label_var.set("Current Stock: -")
            return False

    def _on_batch_change(self):
        self._current_stock = int(self._batch_stock.get(self.batch.get(), 0) or 0)
        self.stock_label_var.set(f"Current Stock: {self._current_stock}")

    def save_adjustment(self):
        name = self.medicine.get().strip()
        batch = self.batch.get().strip()
        if not name or not batch:
            ui_popups.show_error(self.frame, "Error", "Select a Medicine and Batch.")
            return

        try:
            qty = int(self.qty.get())
        except (tk.TclError, ValueError):
            qty = 0
        if qty <= 0:
            ui_popups.show_error(self.frame, "Error", "Enter a quantity greater than zero.")
            return

        reason = self.reason.get()
        note = self.note.get().strip()
        date = self.adj_date.get().strip()
        if not date:
            ui_popups.show_error(self.frame, "Error", "Enter a date.")
            return

        if self.adj_type.get() == "Remove Stock":
            if qty > self._current_stock:
                ui_popups.show_error(self.frame, 
                    "Error",
                    f"Cannot remove {qty} unit(s) - only {self._current_stock} available in this batch."
                )
                return
            qty_change = -qty
        else:
            qty_change = qty

        if not ui_popups.show_confirmation(self.frame, 
            "Confirm Adjustment",
            f'{"Add" if qty_change > 0 else "Remove"} {abs(qty_change)} unit(s) of "{name}" '
            f'(Batch: {batch})\nReason: {reason}\n\nProceed?'
        ):
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute(
                "UPDATE medicine_master SET stock = stock + ? WHERE name=? AND batch=?",
                (qty_change, name, batch)
            )
            cur.execute(
                "INSERT INTO stock_adjustments(adj_date, medicine, batch, qty_change, reason, note, adjusted_by) "
                "VALUES (?,?,?,?,?,?,?)",
                (date, name, batch, qty_change, reason, note, session.get_current_user())
            )
            con.commit()
        except Exception as e:
            con.rollback()
            con.close()
            ui_popups.show_error(self.frame, "Database Error", str(e))
            return
        con.close()

        audit_log.log_action(
            "Stock Adjustment", "Adjust",
            f'{name} (Batch {batch}): {qty_change:+d} units, reason="{reason}"' + (f', note="{note}"' if note else "")
        )

        ui_popups.show_info(self.frame, "Saved", "Stock adjustment recorded.")
        self.qty.set(0)
        self.note.set("")
        self._on_medicine_change()  # refresh current-stock display
        self.load_history()
        # ERP-wide keyboard-nav pass (Aug 2026): return focus to the
        # Medicine box (this screen's "main search box") for continuous
        # entry, text pre-selected so typing a new name immediately
        # replaces it - medicine/batch are deliberately NOT cleared
        # above (existing behaviour - lets the same item be adjusted
        # again right away), so this only moves the cursor, not the data.
        self.cmbMedicine.focus_set()
        self.cmbMedicine.select_range(0, tk.END)

    def load_history(self):
        self.historyTable.delete(*self.historyTable.get_children())
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute(
            "SELECT adj_date, medicine, batch, qty_change, reason, note, adjusted_by "
            "FROM stock_adjustments ORDER BY id DESC LIMIT 200"
        )
        rows = cur.fetchall()
        con.close()
        for adj_date, medicine, batch, qty_change, reason, note, adjusted_by in rows:
            self.historyTable.insert(
                "", "end",
                values=(adj_date, medicine, batch, f"{qty_change:+d}", reason, note or "", adjusted_by or "")
            )
