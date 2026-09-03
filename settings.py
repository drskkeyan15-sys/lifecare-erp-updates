import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sqlite3

from app_paths import DB_NAME
import audit_log
import ui_popups
import ui_style
import theme

class Settings:

    def __init__(self, frame, on_close=None):
        self.frame = frame
        self.on_close = on_close

        self.shop = tk.StringVar()
        self.address = tk.StringVar()
        self.city = tk.StringVar()
        self.phone = tk.StringVar()
        self.email = tk.StringVar()
        self.gstin = tk.StringVar()
        self.dl20 = tk.StringVar()
        self.dl21 = tk.StringVar()
        self.fssai = tk.StringVar()
        self.footer = tk.StringVar()
        # Whether the printed customer receipt shows Cash Received/Change
        # for cash sales - business preference, not a fixed rule (see
        # billing.py's payment-mode feature discussion). Default ON since
        # showing it is standard retail practice and helps avoid change
        # disputes at the counter.
        # Default OFF - per explicit feedback, Received/Change/Due on
        # the printed customer bill isn't wanted here. Only affects a
        # BRAND NEW settings row (fresh install) - an existing saved
        # row already has its own explicit 0/1 value in the database,
        # which this default never overrides (see load_data()'s "NULL
        # defaults to..." comment for the one case this DOES matter:
        # a pre-migration row that predates this column entirely).
        self.show_payment_on_receipt = tk.BooleanVar(value=False)

        # License expiry dates (YYYY-MM-DD) - checked once at app
        # startup (see license_reminders.py + dashboard.py) so a missed
        # renewal shows up before it becomes a compliance problem,
        # instead of only being visible if someone happens to open
        # Settings and notice. Blank = not tracked yet, no reminder.
        self.dl20_expiry = tk.StringVar()
        self.dl21_expiry = tk.StringVar()
        self.fssai_expiry = tk.StringVar()

        # Default/consulting doctor name pre-printed on this shop's paper
        # bill pad (real-world reference: the user's own "Cash Bill" pad
        # has "Prescribed by Dr. ..." fixed-printed on every bill). Used
        # by billing.py as a FALLBACK only - a customer's own doctor-on-
        # file (Customer Master) or anything the cashier types by hand
        # always takes priority; see billing.py's _autofill_doctor().
        self.default_doctor = tk.StringVar()

        # Optional SECOND backup destination - a folder on another drive
        # (USB, network share, or a Google Drive/OneDrive Desktop sync
        # folder mounted locally). Chosen over a real Google Drive API
        # integration deliberately: no OAuth/service-account credential
        # setup needed, works with ANY cloud-sync client the pharmacist
        # already has installed, and degrades safely (a disconnected USB/
        # network drive just skips the mirror copy - see backup_manager.
        # mirror_to_secondary()) instead of failing the whole backup.
        # Blank = feature off.
        self.secondary_backup_folder = tk.StringVar()

        # UPI ID used to generate the payment QR code printed on the A4
        # PDF invoice (billing.py's generate_invoice()) - NOT on the
        # thermal receipt, which prints plain text via Notepad and can't
        # embed an image. Blank = QR is skipped entirely (see
        # generate_invoice()'s own guard).
        self.upi_id = tk.StringVar()

        # Thermal receipt "Plan B" (Aug 2026 chat) - a real store logo
        # image and the exact Windows printer to send the graphic
        # receipt to (billing.py's print_thermal_bill() /
        # _print_thermal_graphic()). Both optional: blank logo path =
        # no logo pasted; blank printer name = use whatever Windows'
        # own default printer is at print time. This only takes effect
        # once the `pywin32` package is installed - until then the
        # thermal receipt keeps printing as plain text exactly as
        # before, unaffected by whatever is typed into these two boxes.
        self.receipt_logo_path = tk.StringVar()
        self.thermal_printer_name = tk.StringVar()

        self.create_table()
        self.create_ui()
        self.load_data()

    # ======================================
    # TABLE MIGRATION
    # ======================================

    def create_table(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            shop_name TEXT,
            address TEXT,
            city TEXT,
            phone TEXT,
            email TEXT,
            gstin TEXT,
            dl20 TEXT,
            dl21 TEXT,
            fssai TEXT,
            footer TEXT
        )
        """)

        for column_sql in (
            "ALTER TABLE settings ADD COLUMN city TEXT",
            "ALTER TABLE settings ADD COLUMN email TEXT",
            "ALTER TABLE settings ADD COLUMN gstin TEXT",
            "ALTER TABLE settings ADD COLUMN dl20 TEXT",
            "ALTER TABLE settings ADD COLUMN dl21 TEXT",
            "ALTER TABLE settings ADD COLUMN fssai TEXT",
            "ALTER TABLE settings ADD COLUMN footer TEXT",
            "ALTER TABLE settings ADD COLUMN show_payment_on_receipt INTEGER DEFAULT 1",
            "ALTER TABLE settings ADD COLUMN dl20_expiry TEXT",
            "ALTER TABLE settings ADD COLUMN dl21_expiry TEXT",
            "ALTER TABLE settings ADD COLUMN fssai_expiry TEXT",
            "ALTER TABLE settings ADD COLUMN secondary_backup_folder TEXT",
            "ALTER TABLE settings ADD COLUMN default_doctor TEXT",
            # Dark mode toggle preference (Phase 4, dashboard.py) - no
            # form field on this screen for it (toggled from the header
            # moon/sun icon instead), but the column still needs to exist
            # here too so save_settings() below can read-and-preserve it
            # without an OperationalError on a DB that predates this ALTER.
            "ALTER TABLE settings ADD COLUMN dark_mode_enabled INTEGER DEFAULT 0",
            "ALTER TABLE settings ADD COLUMN upi_id TEXT",
            # Thermal receipt "Plan B" (Aug 2026) - see the __init__
            # comment on self.receipt_logo_path / self.thermal_printer_name.
            "ALTER TABLE settings ADD COLUMN receipt_logo_path TEXT",
            "ALTER TABLE settings ADD COLUMN thermal_printer_name TEXT",
        ):
            try:
                cur.execute(column_sql)
            except sqlite3.OperationalError:
                pass  # column already exists

        con.commit()
        con.close()

    # ======================================
    # USER INTERFACE
    # ======================================

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="SETTINGS",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        form = tk.LabelFrame(
            self.frame,
            text="Pharmacy Information",
            font=("Segoe UI", 10, "bold")
        )
        form.pack(fill="x", padx=10, pady=10)

        # ---------------- Row 1 ----------------
        tk.Label(form, text="Shop Name").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.shop, width=40).grid(row=0, column=1)

        tk.Label(form, text="Phone").grid(row=0, column=2, padx=5)
        tk.Entry(form, textvariable=self.phone, width=25).grid(row=0, column=3)

        # ---------------- Row 2 ----------------
        tk.Label(form, text="Address").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.address, width=40).grid(row=1, column=1)

        tk.Label(form, text="City").grid(row=1, column=2, padx=5)
        tk.Entry(form, textvariable=self.city, width=25).grid(row=1, column=3)

        # ---------------- Row 3 ----------------
        tk.Label(form, text="GSTIN").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.gstin, width=40).grid(row=2, column=1)

        tk.Label(form, text="Email").grid(row=2, column=2, padx=5)
        tk.Entry(form, textvariable=self.email, width=25).grid(row=2, column=3)

        # ---------------- Row 4 ----------------
        tk.Label(form, text="DL 20").grid(row=3, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.dl20, width=40).grid(row=3, column=1)

        tk.Label(form, text="DL 21").grid(row=3, column=2, padx=5)
        tk.Entry(form, textvariable=self.dl21, width=25).grid(row=3, column=3)

        tk.Label(form, text="UPI ID (for QR on A4 invoice)").grid(row=3, column=4, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.upi_id, width=25).grid(row=3, column=5, padx=5, pady=5, sticky="w")

        # ---------------- Row 4b (license expiry dates) ----------------
        tk.Label(form, text="DL 20 Expiry (YYYY-MM-DD)").grid(row=4, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.dl20_expiry, width=40).grid(row=4, column=1)

        tk.Label(form, text="DL 21 Expiry (YYYY-MM-DD)").grid(row=4, column=2, padx=5)
        tk.Entry(form, textvariable=self.dl21_expiry, width=25).grid(row=4, column=3)

        # ---------------- Row 5 ----------------
        tk.Label(form, text="FSSAI").grid(row=5, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.fssai, width=40).grid(row=5, column=1)

        tk.Label(form, text="FSSAI Expiry (YYYY-MM-DD)").grid(row=5, column=2, padx=5)
        tk.Entry(form, textvariable=self.fssai_expiry, width=25).grid(row=5, column=3)

        tk.Label(form, text="Footer").grid(row=6, column=0, padx=5, pady=5, sticky="nw")
        tk.Entry(form, textvariable=self.footer, width=80).grid(row=6, column=1, columnspan=3, sticky="we")

        tk.Label(form, text="Default Doctor").grid(row=6, column=4, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.default_doctor, width=25).grid(row=6, column=5, padx=5, pady=5, sticky="w")

        # ---------------- Row 7 ----------------
        tk.Checkbutton(
            form,
            text="Show Cash Received / Change on printed receipt (Cash bills only)",
            variable=self.show_payment_on_receipt
        ).grid(row=7, column=0, columnspan=4, padx=5, pady=(10, 5), sticky="w")

        # ---------------- Row 8 - Secondary Backup Folder ----------------
        tk.Label(form, text="2nd Backup Folder (optional)").grid(row=8, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.secondary_backup_folder, width=45).grid(row=8, column=1, columnspan=2, padx=5, pady=5, sticky="w")
        tk.Button(form, text="Browse...", command=self._browse_secondary_backup_folder).grid(row=8, column=3, padx=5, pady=5, sticky="w")
        tk.Label(
            form,
            text="USB / network drive / a Google Drive or OneDrive Desktop sync folder - every backup also copies here.",
            fg="#616161", font=("Segoe UI", 8, "italic")
        ).grid(row=9, column=0, columnspan=4, padx=5, sticky="w")

        # ---------------- Row 10 - Thermal Receipt Plan B ----------------
        # Both fields are optional and only matter once `pywin32` is
        # installed on this machine (see billing.py's
        # _print_thermal_graphic() docstring) - until then the thermal
        # receipt keeps printing as plain text exactly as before,
        # whatever is typed here.
        tk.Label(form, text="Receipt Logo Image (optional)").grid(row=10, column=0, padx=5, pady=(15, 5), sticky="w")
        tk.Entry(form, textvariable=self.receipt_logo_path, width=45).grid(row=10, column=1, columnspan=2, padx=5, pady=(15, 5), sticky="w")
        tk.Button(form, text="Browse...", command=self._browse_receipt_logo).grid(row=10, column=3, padx=5, pady=(15, 5), sticky="w")

        tk.Label(form, text="Thermal Printer Name (blank = Windows default)").grid(row=11, column=0, padx=5, pady=5, sticky="w")
        self.thermal_printer_combo = ttk.Combobox(
            form, textvariable=self.thermal_printer_name, width=42,
            values=self._detect_windows_printers(),
        )
        self.thermal_printer_combo.grid(row=11, column=1, columnspan=2, padx=5, pady=5, sticky="w")
        tk.Label(
            form,
            text="Plug in your USB thermal printer, install its Windows driver, then pick its exact name here (or leave blank to use the Windows default printer).",
            fg="#616161", font=("Segoe UI", 8, "italic")
        ).grid(row=12, column=0, columnspan=4, padx=5, sticky="w")

        # ===============================
        # BUTTONS
        # ===============================
        btn = tk.Frame(self.frame)
        btn.pack(fill="x", padx=10, pady=10)

        tk.Button(btn, text="Save Settings", bg="#2E7D32", fg="white", width=18, command=self.save).pack(side="left", padx=5)
        tk.Button(btn, text="Reload", width=15, command=self.load_data).pack(side="left", padx=5)
        tk.Button(btn, text="Clear", width=15, command=self.clear_data).pack(side="left", padx=5)
        tk.Button(btn, text="Manual Backup", width=15, command=self.manual_backup).pack(side="left", padx=5)
        tk.Button(btn, text="Restore from Backup", bg="#C62828", fg="white", width=18, command=self.open_restore_dialog).pack(side="left", padx=5)

    def _browse_secondary_backup_folder(self):
        chosen = filedialog.askdirectory(title="Select a Second Backup Folder")
        if chosen:
            self.secondary_backup_folder.set(chosen)

    def _browse_receipt_logo(self):
        chosen = filedialog.askopenfilename(
            title="Select a Store Logo Image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All files", "*.*")],
        )
        if chosen:
            self.receipt_logo_path.set(chosen)

    @staticmethod
    def _detect_windows_printers():
        """Best-effort list of printers Windows already knows about, for
        the Thermal Printer Name dropdown above - just a convenience so
        the user can pick from a list instead of typing the exact name
        by hand. Needs `pywin32`; returns an empty list (dropdown still
        works as a plain typed box) if that isn't installed yet, or on
        any other error - never blocks Settings from opening."""
        try:
            import win32print
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            return [p[2] for p in win32print.EnumPrinters(flags)]
        except Exception:
            return []

    # ======================================
    # SAVE SETTINGS ACTION
    # ======================================

    def save_settings(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()

        try:
            # This screen has no form field for dark_mode_enabled (it's
            # toggled from the header moon/sun icon in dashboard.py, not
            # from here) - but the INSERT below always follows a DELETE
            # FROM settings that wipes the whole row, so the existing
            # value has to be read first and carried forward explicitly.
            # Otherwise saving anything on THIS screen (even just editing
            # the shop phone number) would silently reset the user's dark
            # mode preference back to its column default every time.
            try:
                cur.execute("SELECT dark_mode_enabled FROM settings LIMIT 1")
                row = cur.fetchone()
                dark_mode_enabled = row[0] if row and row[0] is not None else 0
            except sqlite3.OperationalError:
                dark_mode_enabled = 0  # pre-migration DB, column not created yet

            cur.execute("DELETE FROM settings")

            cur.execute("""
            INSERT INTO settings
            (
                shop_name, address, city, phone, email, gstin, dl20, dl21, fssai, footer,
                show_payment_on_receipt, dl20_expiry, dl21_expiry, fssai_expiry,
                secondary_backup_folder, default_doctor, dark_mode_enabled, upi_id,
                receipt_logo_path, thermal_printer_name
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                self.shop.get().strip(),
                self.address.get().strip(),
                self.city.get().strip(),
                self.phone.get().strip(),
                self.email.get().strip(),
                self.gstin.get().strip(),
                self.dl20.get().strip(),
                self.dl21.get().strip(),
                self.fssai.get().strip(),
                self.footer.get().strip(),
                1 if self.show_payment_on_receipt.get() else 0,
                self.dl20_expiry.get().strip(),
                self.dl21_expiry.get().strip(),
                self.fssai_expiry.get().strip(),
                self.secondary_backup_folder.get().strip(),
                self.default_doctor.get().strip(),
                dark_mode_enabled,
                self.upi_id.get().strip(),
                self.receipt_logo_path.get().strip(),
                self.thermal_printer_name.get().strip()
            ))

            con.commit()
            ui_popups.show_info(self.frame, "Success", "Settings Saved Successfully")

        except Exception as e:
            con.rollback()
            ui_popups.show_error(self.frame, "Database Error", str(e))
        finally:
            con.close()

    # ======================================
    # LOAD SETTINGS
    # ======================================

    def load_data(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
        SELECT shop_name, address, city, phone, email, gstin, dl20, dl21, fssai, footer,
               show_payment_on_receipt, dl20_expiry, dl21_expiry, fssai_expiry,
               secondary_backup_folder, default_doctor, upi_id,
               receipt_logo_path, thermal_printer_name
        FROM settings LIMIT 1
        """)
        row = cur.fetchone()
        con.close()

        if row:
            self.shop.set(row[0] or "")
            self.address.set(row[1] or "")
            self.city.set(row[2] or "")
            self.phone.set(row[3] or "")
            self.email.set(row[4] or "")
            self.gstin.set(row[5] or "")
            self.dl20.set(row[6] or "")
            self.dl21.set(row[7] or "")
            self.fssai.set(row[8] or "")
            self.footer.set(row[9] or "")
            # NULL (pre-migration row) defaults to True, matching the
            # column's own DEFAULT 1 for any row inserted after this.
            self.show_payment_on_receipt.set(bool(row[10]) if row[10] is not None else True)
            self.dl20_expiry.set(row[11] or "")
            self.dl21_expiry.set(row[12] or "")
            self.fssai_expiry.set(row[13] or "")
            self.secondary_backup_folder.set(row[14] or "")
            self.default_doctor.set(row[15] or "")
            self.upi_id.set(row[16] or "")
            self.receipt_logo_path.set(row[17] if len(row) > 17 and row[17] else "")
            self.thermal_printer_name.set(row[18] if len(row) > 18 and row[18] else "")

    # ======================================
    # CLEAR SETTINGS
    # ======================================

    def clear_data(self):
        self.shop.set("")
        self.address.set("")
        self.city.set("")
        self.phone.set("")
        self.email.set("")
        self.gstin.set("")
        self.dl20.set("")
        self.dl21.set("")
        self.fssai.set("")
        self.footer.set("")
        self.show_payment_on_receipt.set(True)
        self.dl20_expiry.set("")
        self.dl21_expiry.set("")
        self.fssai_expiry.set("")
        self.secondary_backup_folder.set("")
        self.default_doctor.set("")
        self.upi_id.set("")
        self.receipt_logo_path.set("")
        self.thermal_printer_name.set("")

    # ======================================
    # VALIDATION (UPDATED WITH 10 DIGIT RULE)
    # ======================================

    def validate(self):
        if self.shop.get().strip() == "":
            ui_popups.show_error(self.frame, "Error", "Enter Shop Name")
            return False

        phone_val = self.phone.get().strip()
        if phone_val != "":
            if not phone_val.isdigit():
                ui_popups.show_error(self.frame, "Error", "Phone Number must contain digits only.")
                return False
            # சப்ளையர்/கஸ்டமர் மாஸ்டர் உடன் ஒத்திசைக்க 10 இலக்கச் சரிபார்ப்பு
            if len(phone_val) != 10:
                ui_popups.show_error(self.frame, "Error", "Phone Number must be exactly 10 digits.")
                return False

        return True

    # ======================================
    # SAVE BUTTON BRIDGE
    # ======================================

    def save(self):
        if not self.validate():
            return
        self.save_settings()

    # ======================================
    # REFRESH
    # ======================================

    def refresh(self):
        self.clear_data()
        self.load_data()

    # ======================================
    # CLOSE
    # ======================================

    def close(self):
        self.frame.destroy()
        if self.on_close:
            self.on_close()

    def manual_backup(self):
        from backup_manager import backup_now
        path, created = backup_now()
        if created:
            ui_popups.show_info(self.frame, "Backup", f"Backup created:\n{path}")
        else:
            ui_popups.show_info(self.frame, "Backup", "Today's backup already exists.")

    def open_restore_dialog(self):
        """
        Lets the pharmacist pick one of backup_manager.list_backups()
        and restore the live database from it. Was previously only
        possible by manually copying files around outside the app -
        this is the in-app version, with its own confirmation + a
        pre-restore safety copy (see backup_manager.restore_backup()).
        """
        from backup_manager import list_backups, restore_backup

        backups = list_backups()
        if not backups:
            ui_popups.show_info(self.frame, "No Backups Found", "No backup files found yet. Use \"Manual Backup\" first, or wait for tomorrow's automatic one.")
            return

        win = tk.Toplevel(self.frame)
        win.title("Restore from Backup")
        win.resizable(False, False)
        win.grab_set()

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring) - danger-red header,
        # same as ui_popups.py's own "error" kind coloring, since this
        # is a destructive/irreversible-from-the-UI action.
        outer = ui_style.popup_header(win, "RESTORE FROM BACKUP", bg=theme.STATUS_DANGER, icon="⚠")
        body = tk.Frame(outer, bg=theme.SURFACE_WHITE, padx=10, pady=10)
        body.pack(fill="both", expand=True)

        tk.Label(
            body, bg=theme.SURFACE_WHITE,
            text="Choose a backup below. Restoring REPLACES all current data\n"
                 "with that backup's data - anything added or changed since\n"
                 "then will be lost from the live database (a safety copy of\n"
                 "the current data is taken automatically before restoring).",
            justify="left", fg=theme.STATUS_DANGER, wraplength=380
        ).pack(pady=(0, 10))

        listbox = tk.Listbox(
            body, width=45, height=12, font=("Segoe UI", 10),
            bg=theme.SURFACE_FIELD, relief="flat", highlightthickness=1,
            highlightbackground=theme.BORDER_DEFAULT, highlightcolor=theme.BORDER_FOCUS,
        )
        for fname in backups:
            listbox.insert(tk.END, fname)
        listbox.pack(pady=(0, 10))

        def do_restore():
            selection = listbox.curselection()
            if not selection:
                ui_popups.show_warning(win, "Select a Backup", "Select a backup file from the list first.")
                return
            filename = backups[selection[0]]

            if not ui_popups.show_confirmation(win, 
                "Confirm Restore",
                f'Restore the database from:\n\n"{filename}"\n\n'
                "This cannot be undone from within the app (though a safety "
                "copy of the current data is kept). Continue?"
            ):
                return

            try:
                safety_path = restore_backup(filename)
            except Exception as e:
                ui_popups.show_error(win, "Restore Failed", str(e))
                return

            # Written AFTER restore_backup() has already swapped
            # pharmacy.db, so this lands in the newly-restored database
            # itself - the best available record, since anything logged
            # before the swap would be lost along with the rest of the
            # pre-restore data (that's exactly what the safety copy is for).
            audit_log.log_action(
                "Settings", "Restore Backup",
                f'Restored database from "{filename}" (pre-restore safety copy: {safety_path})'
            )

            ui_popups.show_info(win, 
                "Restore Complete",
                f'Database restored from "{filename}".\n\n'
                f"A safety copy of your previous data was saved to:\n{safety_path}\n\n"
                "Please CLOSE and REOPEN the app now so every screen "
                "reloads the restored data."
            )
            win.grab_release()
            win.destroy()

        def _close():
            win.grab_release()
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _close)

        btn_row = tk.Frame(body, bg=theme.SURFACE_WHITE)
        btn_row.pack(fill="x", pady=(0, 0))
        ui_style.flat_button(
            btn_row, "Restore Selected Backup", theme.STATUS_DANGER, do_restore, width=20,
        ).pack(pady=(0, 6))
        ui_style.flat_button(btn_row, "Cancel", theme.ACCENT_NEUTRAL, _close, width=20).pack()

        # No explicit width/height (was a fixed 420x420 guess) - see
        # ui_style.center_window()'s own docstring for why sizing to
        # real packed content is safer.
        ui_style.center_window(win, parent=self.frame.winfo_toplevel())