import tkinter as tk
from tkinter import ttk, messagebox

import clinic_repository as repo
import session
import theme
import ui_popups


class ClinicPatients:
    """Patient Master for Clinic Ledger - search-first "dedup before you
    create" screen, per CLINIC_LEDGER_WORKFLOW.md's patient entry rules.
    Deliberately its own small table, NOT the billing `customers` table -
    see clinic_repository.py's module docstring / database.py's
    clinic_patients comment for why."""

    def __init__(self, frame, on_close=None):
        self.frame = frame
        self.on_close = on_close
        self.patient_id = None
        self.create_variables()
        self.create_ui()
        self.load_patients()

    def create_variables(self):
        self.name = tk.StringVar()
        self.age = tk.StringVar()
        self.gender = tk.StringVar(value="Male")
        self.phone = tk.StringVar()
        self.address = tk.StringVar()
        self.search = tk.StringVar()

    def create_ui(self):
        title = tk.Label(
            self.frame, text="CLINIC PATIENT MASTER",
            bg=theme.PRIMARY, fg="white", font=("Segoe UI", 18, "bold"), pady=10
        )
        title.pack(fill="x")

        form = tk.LabelFrame(self.frame, text="Patient Details", font=("Segoe UI", 10, "bold"))
        form.pack(fill="x", padx=10, pady=10)

        tk.Label(form, text="Name *").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.name, width=30).grid(row=0, column=1)

        tk.Label(form, text="Age").grid(row=0, column=2, padx=5)
        tk.Entry(form, textvariable=self.age, width=8).grid(row=0, column=3)

        tk.Label(form, text="Gender").grid(row=0, column=4, padx=5)
        ttk.Combobox(form, textvariable=self.gender, values=["Male", "Female", "Other"],
                     state="readonly", width=10).grid(row=0, column=5)

        tk.Label(form, text="Phone").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form, textvariable=self.phone, width=20).grid(row=1, column=1, sticky="w")

        tk.Label(form, text="Address").grid(row=1, column=2, padx=5)
        tk.Entry(form, textvariable=self.address, width=45).grid(row=1, column=3, columnspan=3, sticky="we")

        btn = tk.Frame(self.frame)
        btn.pack(fill="x", padx=10, pady=10)
        tk.Button(btn, text="Save New", bg=theme.STATUS_SUCCESS, fg="white", width=12,
                  command=self.save_patient).pack(side="left", padx=5)
        tk.Button(btn, text="Update", bg=theme.PRIMARY, fg="white", width=12,
                  command=self.update_patient).pack(side="left", padx=5)
        tk.Button(btn, text="Clear", bg=theme.ACCENT_NEUTRAL, fg="white", width=12,
                  command=self.clear_fields).pack(side="left", padx=5)
        if self.on_close:
            tk.Button(btn, text="Close", bg=theme.STATUS_DANGER, fg="white", width=12,
                      command=self.on_close).pack(side="right", padx=5)

        tk.Label(btn, text="Search (name or phone)").pack(side="left", padx=(40, 5))
        search = tk.Entry(btn, textvariable=self.search, width=30)
        search.pack(side="left")
        self.search.trace_add("write", lambda *a: self.load_patients())

        table = tk.Frame(self.frame)
        table.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("ID", "Code", "Name", "Age", "Gender", "Phone", "Address")
        self.patientTable = ttk.Treeview(table, columns=cols, show="headings", height=16, style="ERP.Treeview")
        for c in cols:
            self.patientTable.heading(c, text=c)
            self.patientTable.column(c, width=220 if c == "Address" else 110, anchor="center")
        self.patientTable.pack(fill="both", expand=True)
        self.patientTable.bind("<<TreeviewSelect>>", self.select_patient)

        footer = tk.Frame(self.frame)
        footer.pack(fill="x", padx=10, pady=(0, 10))
        self.lblCount = tk.Label(footer, text="Total Patients : 0", font=("Segoe UI", 10, "bold"), fg=theme.PRIMARY)
        self.lblCount.pack(side="left")

    def load_patients(self):
        self.patientTable.delete(*self.patientTable.get_children())
        rows = repo.search_patients(self.search.get())
        for row in rows:
            self.patientTable.insert("", "end", values=row)
        label = f"Showing {len(rows)} matching patient(s)" if self.search.get().strip() else f"Total Patients : {len(rows)}"
        self.lblCount.config(text=label)

    def select_patient(self, event=None):
        selected = self.patientTable.focus()
        if not selected:
            return
        values = self.patientTable.item(selected)["values"]
        self.patient_id = values[0]
        self.name.set(values[2] or "")
        self.age.set(values[3] or "")
        self.gender.set(values[4] or "Male")
        self.phone.set(values[5] or "")
        self.address.set(values[6] or "")

    def _validate(self):
        if not self.name.get().strip():
            ui_popups.show_error(self.frame, "Error", "Patient Name Required")
            return False
        if self.age.get().strip() and not self.age.get().strip().isdigit():
            ui_popups.show_error(self.frame, "Error", "Age must be a number")
            return False
        return True

    def save_patient(self):
        if not self._validate():
            return
        try:
            patient_id, code = repo.create_patient(
                self.name.get().strip(),
                int(self.age.get()) if self.age.get().strip() else None,
                self.gender.get(),
                self.phone.get().strip(),
                self.address.get().strip(),
                created_by=session.get_current_user(),
            )
            ui_popups.show_info(self.frame, "Success", f"Patient Saved (Code: {code})")
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))
        finally:
            self.clear_fields()
            self.load_patients()

    def update_patient(self):
        if self.patient_id is None:
            ui_popups.show_error(self.frame, "Error", "Select a patient from the table first")
            return
        if not self._validate():
            return
        try:
            repo.update_patient(
                self.patient_id, self.name.get().strip(),
                int(self.age.get()) if self.age.get().strip() else None,
                self.gender.get(), self.phone.get().strip(), self.address.get().strip()
            )
            ui_popups.show_info(self.frame, "Success", "Patient Updated")
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))
        finally:
            self.clear_fields()
            self.load_patients()

    def clear_fields(self):
        self.patient_id = None
        self.name.set("")
        self.age.set("")
        self.gender.set("Male")
        self.phone.set("")
        self.address.set("")
