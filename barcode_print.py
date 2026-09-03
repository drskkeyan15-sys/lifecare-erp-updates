import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import os
import tempfile
from datetime import datetime
from app_paths import DB_NAME, app_path
import ui_popups

try:
    import barcode
    from barcode.writer import ImageWriter
    BARCODE_AVAILABLE = True
except ImportError:
    BARCODE_AVAILABLE = False


class BarcodePrint:

    def __init__(self, frame):
        self.frame = frame
        self.create_variables()
        self.create_ui()
        self.load_medicines()

    def create_variables(self):
        self.medicine_name = tk.StringVar()
        self.batch = tk.StringVar()
        self.price = tk.StringVar()
        self.barcode_val = tk.StringVar()
        self.copies = tk.IntVar(value=1)
        self._medicine_names = []

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="BARCODE STICKER PRINTING",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        if not BARCODE_AVAILABLE:
            warn_lbl = tk.Label(
                self.frame,
                text="⚠️ Warning: 'python-barcode' or 'pillow' library is not installed.\nRun 'pip install python-barcode pillow' in terminal to enable barcode generation.",
                fg="red",
                font=("Segoe UI", 11, "bold"),
                pady=10
            )
            warn_lbl.pack()

        # ---------------- Form Frame ----------------
        form_frame = tk.LabelFrame(
            self.frame,
            text="Select Medicine for Barcode",
            font=("Segoe UI", 10, "bold")
        )
        form_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(form_frame, text="Medicine Name").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.cmbMedicine = ttk.Combobox(
            form_frame,
            textvariable=self.medicine_name,
            width=30,
            state="normal"
        )
        self.cmbMedicine.grid(row=0, column=1, padx=5, pady=5)
        self.cmbMedicine.bind("<<ComboboxSelected>>", self.fetch_medicine_details)
        self.cmbMedicine.bind("<KeyRelease>", self.search_medicine)

        tk.Label(form_frame, text="Batch No").grid(row=0, column=2, padx=5, pady=5, sticky="w")
        tk.Entry(form_frame, textvariable=self.batch, width=20).grid(row=0, column=3, padx=5, pady=5)

        tk.Label(form_frame, text="MRP / Price (₹)").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form_frame, textvariable=self.price, width=20).grid(row=1, column=1, padx=5, pady=5, sticky="w")

        tk.Label(form_frame, text="Barcode Code").grid(row=1, column=2, padx=5, pady=5, sticky="w")
        tk.Entry(form_frame, textvariable=self.barcode_val, width=20).grid(row=1, column=3, padx=5, pady=5)

        tk.Label(form_frame, text="No. of Copies").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form_frame, textvariable=self.copies, width=10).grid(row=2, column=1, padx=5, pady=5, sticky="w")

        tk.Button(
            form_frame,
            text="🖨️ Generate & Print Barcode Labels",
            bg="green",
            fg="white",
            font=("Segoe UI", 11, "bold"),
            command=self.print_barcode_labels
        ).grid(row=3, column=1, columnspan=2, pady=15)

    def load_medicines(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute("SELECT name FROM medicine_master ORDER BY name")
            self._medicine_names = [row[0] for row in cur.fetchall()]
            self.cmbMedicine["values"] = self._medicine_names
        except Exception:
            self._medicine_names = []
        con.close()

    def search_medicine(self, event=None):
        text = self.medicine_name.get().strip().lower()
        if not text:
            self.cmbMedicine["values"] = self._medicine_names
            return
        matches = [n for n in self._medicine_names if text in n.lower()]
        self.cmbMedicine["values"] = matches

    def fetch_medicine_details(self, event=None):
        med = self.medicine_name.get().strip()
        if not med:
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT batch, sale, barcode FROM medicine_master WHERE name=?", (med,))
        row = cur.fetchone()
        con.close()

        if row:
            self.batch.set(row[0] or "")
            self.price.set(str(row[1] or "0.00"))
            bc = row[2]
            if not bc or bc == "None" or bc == "0":
                # Generate a default numeric barcode based on ID or timestamp if missing
                bc = f"890{abs(hash(med)) % 1000000000:09d}"
            self.barcode_val.set(bc)

    def print_barcode_labels(self):
        if not BARCODE_AVAILABLE:
            ui_popups.show_error(self.frame, "Error", "Required libraries ('python-barcode', 'pillow') are missing.")
            return

        med = self.medicine_name.get().strip()
        bc = self.barcode_val.get().strip()
        prc = self.price.get().strip()
        bat = self.batch.get().strip()
        num_copies = self.copies.get()

        if not med or not bc:
            ui_popups.show_error(self.frame, "Error", "Please select a medicine and ensure barcode value is present.")
            return

        if num_copies <= 0:
            ui_popups.show_error(self.frame, "Error", "Enter a valid number of copies.")
            return

        try:
            # 1. Generate Barcode Image using Code128
            code128 = barcode.get('code128', bc, writer=ImageWriter())
            filename = code128.save(tempfile.mktemp())

            # 2. Create a simple text summary file or open print command via notepad / default viewer
            label_text = f"Medicine: {med}\nBatch: {bat} | MRP: ₹{prc}\nBarcode: {bc}\nCopies: {num_copies}\n"
            
            # Save temporary label instruction text / preview
            txt_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
            txt_file.write("="*30 + "\n")
            txt_file.write(f"   {med[:24]}\n")
            txt_file.write(f"   MRP: Rs.{prc}  Batch:{bat}\n")
            txt_file.write(f"   Code: {bc}\n")
            txt_file.write("="*30 + "\n")
            txt_file.close()

            # Trigger print or open image
            os.startfile(filename, "print")
            ui_popups.show_info(self.frame, "Success", f"Barcode labels generated and sent to printer successfully!\nTotal Copies: {num_copies}")

        except Exception as e:
            ui_popups.show_error(self.frame, "Printing Error", str(e))