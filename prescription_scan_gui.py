"""
prescription_scan_gui.py
LifeCare Pharmacy ERP - Prescription Photo -> Bill review dialog (Aug 2026)

Opened from Billing's "Scan Prescription" button. Lets the pharmacist
pick a photographed prescription, runs prescription_ocr.py's OCR
pipeline on a background thread (same threading pattern as
bulk_import.py's run_ocr()/_ocr_worker()/_poll_ocr_queue(), for the
same reason: Tesseract can take a while and Tkinter can't repaint while
its main thread is busy), then shows a review grid where the pharmacist
must EXPLICITLY tick "Include" on each row before it's added to the
bill - nothing here is ever added automatically. See prescription_ocr.py's
module docstring for why handwriting-OCR confidence is shown, not used
to silently filter rows.

Deliberately hands back plain (medicine_name, qty) tuples to a caller-
supplied callback rather than writing to the bill/database itself -
Billing's own add_item() already has the correct FIFO batch-splitting,
stock validation, and Schedule H1 prescription-warning logic; this
dialog's only job is turning a photo into candidates for a human to
confirm, then letting that existing, unmodified logic do the real work.
"""

import os
import queue
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import medicine_matcher
import prescription_ocr
from app_paths import DB_NAME
import ui_popups

# Looser than bulk_import.py's invoice-OCR default (0.55) - handwritten
# prescriptions read noisier than printed invoice text, and this is
# only ever used to SUGGEST a starting dropdown value; the pharmacist
# can always type over it, and nothing here is trusted without an
# explicit "Include" tick (see module docstring).
MATCH_MIN_SCORE = 0.45


class PrescriptionScanDialog:
    """
    Usage (see billing.py's Scan Prescription button):
        PrescriptionScanDialog(parent_window, on_add_items=callback)
    `on_add_items` is called with a list of (medicine_name, qty) tuples
    - one per row the pharmacist ticked - when they click "Add Checked
    Items to Bill".
    """

    def __init__(self, parent, on_add_items):
        self.parent = parent
        self.on_add_items = on_add_items
        self._rows = []
        self._result_queue = queue.Queue()
        self._all_medicine_names = self._load_all_medicine_names()

        self.win = tk.Toplevel(parent)
        self.win.title("Scan Prescription")
        self.win.geometry("920x600")
        self.win.grab_set()

        self._build_ui()

    # ==========================================
    # SETUP
    # ==========================================

    @staticmethod
    def _load_all_medicine_names():
        try:
            con = sqlite3.connect(DB_NAME)
            cur = con.cursor()
            cur.execute("SELECT DISTINCT name FROM medicine_master ORDER BY name")
            names = [r[0] for r in cur.fetchall() if r[0]]
            con.close()
            return names
        except sqlite3.Error:
            return []

    def _build_ui(self):
        tk.Label(
            self.win, text="SCAN PRESCRIPTION", bg="#1565C0", fg="white",
            font=("Segoe UI", 14, "bold"), pady=8
        ).pack(fill="x")

        tk.Label(
            self.win,
            text="OCR reading may not be 100% correct, especially for handwritten prescriptions. "
                 "Verify each medicine and tick the Include box only when correct, then click Add — "
                 "nothing is added to the bill automatically.",
            fg="#C62828", font=("Segoe UI", 9, "bold"), wraplength=880,
            justify="left", anchor="w"
        ).pack(fill="x", padx=10, pady=(8, 4))

        pathbar = tk.Frame(self.win)
        pathbar.pack(fill="x", padx=10, pady=5)
        self.path_var = tk.StringVar()
        tk.Entry(pathbar, textvariable=self.path_var, width=60, state="readonly").pack(side="left", padx=(0, 5))
        tk.Button(pathbar, text="Choose Photo...", command=self._choose_photo).pack(side="left")
        self.btn_run = tk.Button(
            pathbar, text="Run OCR", bg="#1565C0", fg="white",
            command=self._run_ocr, state="disabled"
        )
        self.btn_run.pack(side="left", padx=(10, 0))
        self.status_label = tk.Label(pathbar, text="", fg="#1565C0")
        self.status_label.pack(side="left", padx=10)

        # ---- Review grid ----
        grid_frame = tk.Frame(self.win)
        grid_frame.pack(fill="both", expand=True, padx=10, pady=10)

        header = tk.Frame(grid_frame)
        header.pack(fill="x")
        for text, width in (
            ("Include", 8), ("OCR Text", 26), ("Matched Medicine (edit/search freely)", 34),
            ("Qty", 6), ("Confidence", 10),
        ):
            tk.Label(header, text=text, font=("Segoe UI", 9, "bold"), width=width, anchor="w").pack(side="left", padx=2)

        canvas = tk.Canvas(grid_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(grid_frame, orient="vertical", command=canvas.yview)
        self.rows_frame = tk.Frame(canvas)
        self.rows_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        # Deliberately no <MouseWheel> binding here - see dashboard.py's
        # own comment on scroll_sidebar()/_on_sidebar_enter() for why
        # that was tried and removed app-wide (touchpad-drag caused a
        # runaway scroll bug). The scrollbar alone is enough here.
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ---- Buttons ----
        btns = tk.Frame(self.win)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(
            btns, text="Add Checked Items to Bill", bg="#2E7D32", fg="white",
            font=("Segoe UI", 10, "bold"), command=self._add_checked_to_bill
        ).pack(side="left", padx=(0, 5))
        tk.Button(btns, text="Close", command=self._close).pack(side="left")
        self.win.protocol("WM_DELETE_WINDOW", self._close)

    # ==========================================
    # OCR RUN (background thread - see bulk_import.py's run_ocr() for
    # the same freeze-avoidance reasoning)
    # ==========================================

    def _choose_photo(self):
        path = filedialog.askopenfilename(filetypes=[("Image files", "*.jpg *.jpeg *.png")])
        if not path:
            return
        self.path_var.set(path)
        self.btn_run.config(state="normal")

    def _run_ocr(self):
        path = self.path_var.get()
        if not path or not os.path.exists(path):
            return
        if self.btn_run["state"] == "disabled":
            return  # already running - ignore a double-click
        self.btn_run.config(state="disabled")
        self.status_label.config(text="படிக்கிறோம்... photo பெரிசா இருந்தா konjam time ஆகும்.")
        threading.Thread(target=self._ocr_worker, args=(path,), daemon=True).start()
        self.win.after(100, self._poll_queue)

    def _ocr_worker(self, path):
        """Runs off the main thread - must never touch a Tkinter widget
        directly, only put a plain-data result on self._result_queue."""
        try:
            rows = prescription_ocr.extract_prescription_lines(path)
            self._result_queue.put(("ok", rows))
        except Exception as e:
            self._result_queue.put(("error", str(e)))

    def _poll_queue(self):
        try:
            status, payload = self._result_queue.get_nowait()
        except queue.Empty:
            if self.win.winfo_exists():
                self.win.after(100, self._poll_queue)
            return

        if not self.win.winfo_exists():
            return  # dialog closed while OCR was still running

        self.btn_run.config(state="normal")
        if status == "error":
            self.status_label.config(text="")
            ui_popups.show_error(self.win, 
                "OCR Error",
                f"{payload}\n\nTesseract-OCR install pannirukkeengala nu check pannunga."
            )
            return

        self.status_label.config(text=f"{len(payload)} line(s) found - ovvondrayum sariyaa paarunga.")
        self._populate_rows(payload)

    # ==========================================
    # REVIEW GRID
    # ==========================================

    def _populate_rows(self, ocr_rows):
        for child in self.rows_frame.winfo_children():
            child.destroy()
        self._rows = []

        if not ocr_rows:
            tk.Label(
                self.rows_frame, text="No lines detected by OCR. Try a clearer/different photo.",
                fg="#616161"
            ).pack(anchor="w", padx=4, pady=10)
            return

        for entry in ocr_rows:
            matches = medicine_matcher.find_medicine_matches(
                entry["name"], top_n=5, min_score=MATCH_MIN_SCORE
            )
            self._add_row(entry, matches)

    def _add_row(self, entry, matches):
        row_frame = tk.Frame(self.rows_frame)
        row_frame.pack(fill="x", pady=1)

        include_var = tk.BooleanVar(value=False)
        tk.Checkbutton(row_frame, variable=include_var, width=6).pack(side="left", padx=2)

        tk.Label(row_frame, text=entry["name"], width=26, anchor="w", font=("Segoe UI", 9)).pack(side="left", padx=2)

        medicine_var = tk.StringVar(value=matches[0][0] if matches else "")
        candidate_names = [m[0] for m in matches]
        combo = ttk.Combobox(row_frame, textvariable=medicine_var, values=candidate_names, width=34)
        combo.pack(side="left", padx=2)
        if not matches:
            combo.configure(foreground="#C62828")

        # Free-text search over EVERY medicine in stock, not just this
        # row's top-5 OCR guesses - same live-filter pattern as
        # prescription_archive.py's own customer combobox
        # (_filter_customer_dropdown), so the pharmacist can always
        # override a wrong/no OCR match by typing the real name.
        def _filter(event, combo=combo, medicine_var=medicine_var):
            if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
                return
            typed = medicine_var.get().lower()
            combo["values"] = (
                candidate_names if not typed
                else [n for n in self._all_medicine_names if typed in n.lower()]
            )
        combo.bind("<KeyRelease>", _filter)

        qty_var = tk.IntVar(value=max(int(entry.get("qty", 1) or 1), 1))
        tk.Entry(row_frame, textvariable=qty_var, width=6).pack(side="left", padx=2)

        conf = entry.get("confidence")
        conf_text = f"{conf:.0f}%" if conf is not None else "-"
        conf_val = conf or 0
        conf_color = "#2E7D32" if conf_val >= 60 else ("#E65100" if conf_val >= 30 else "#C62828")
        tk.Label(row_frame, text=conf_text, width=10, fg=conf_color, font=("Segoe UI", 9, "bold")).pack(side="left", padx=2)

        self._rows.append({
            "include_var": include_var,
            "medicine_var": medicine_var,
            "qty_var": qty_var,
            "ocr_text": entry["name"],
        })

    # ==========================================
    # ADD TO BILL
    # ==========================================

    def _add_checked_to_bill(self):
        to_add = []
        skipped = 0
        for row in self._rows:
            if not row["include_var"].get():
                continue
            name = row["medicine_var"].get().strip()
            if not name:
                skipped += 1
                continue
            try:
                qty = int(row["qty_var"].get())
            except (tk.TclError, ValueError):
                qty = 0
            if qty <= 0:
                skipped += 1
                continue
            to_add.append((name, qty))

        if not to_add:
            ui_popups.show_info(self.win, 
                "No Items Selected",
                "Include checkbox tick pannitu, oru medicine venum select pannunga."
            )
            return

        if skipped:
            ui_popups.show_warning(self.win, 
                "Some Rows Skipped",
                f"{skipped} checked row(s) had no medicine selected or invalid qty - skipped."
            )

        self.on_add_items(to_add)
        self._close()

    def _close(self):
        try:
            self.win.grab_release()
        except Exception:
            pass
        self.win.destroy()
