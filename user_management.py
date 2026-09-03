import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from app_paths import DB_NAME
import audit_log
import auth_utils
import ui_popups


class UserManagement:

    def __init__(self, frame):
        self.frame = frame
        self.create_variables()
        self.create_ui()
        self.load_users()

    def create_variables(self):
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.role = tk.StringVar(value="Cashier")

    def create_ui(self):
        title = tk.Label(
            self.frame,
            text="USER ROLES & PERMISSION MANAGEMENT",
            bg="#1565C0",
            fg="white",
            font=("Segoe UI", 18, "bold"),
            pady=10
        )
        title.pack(fill="x")

        # ---------------- Form Frame ----------------
        form_frame = tk.LabelFrame(
            self.frame,
            text="Add / Manage Users",
            font=("Segoe UI", 10, "bold")
        )
        form_frame.pack(fill="x", padx=10, pady=10)

        tk.Label(form_frame, text="Username").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        tk.Entry(form_frame, textvariable=self.username, width=25).grid(row=0, column=1, padx=5, pady=5)

        tk.Label(form_frame, text="Password").grid(row=0, column=2, padx=5, pady=5, sticky="w")
        tk.Entry(form_frame, textvariable=self.password, show="*", width=25).grid(row=0, column=3, padx=5, pady=5)

        tk.Label(form_frame, text="User Role").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.cmbRole = ttk.Combobox(
            form_frame,
            textvariable=self.role,
            values=["Admin", "Cashier"],
            state="readonly",
            width=23
        )
        self.cmbRole.grid(row=1, column=1, padx=5, pady=5)

        tk.Button(
            form_frame,
            text="Save User",
            bg="green",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            width=15,
            command=self.save_user
        ).grid(row=1, column=3, padx=5, pady=10, sticky="e")

        # ---------------- Buttons below the form (moved here from below
        # the table during the Aug 2026 UI-consistency pass - it used to
        # sit under the grid at the very bottom of the screen, out of
        # the usual Save/Delete button-row position every other screen
        # uses) ----------------
        btn_frame = tk.Frame(self.frame)
        btn_frame.pack(fill="x", padx=10, pady=(0, 5))

        tk.Button(
            btn_frame,
            text="Delete Selected User",
            bg="red",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            width=18,
            command=self.delete_user
        ).pack(side="left")

        # ---------------- Table Frame ----------------
        table_frame = tk.Frame(self.frame)
        table_frame.pack(fill="both", expand=True, padx=10, pady=5)

        columns = ("ID", "Username", "Role", "Permissions")
        self.userTable = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=12,
            style="ERP.Treeview"
        )

        for c in columns:
            self.userTable.heading(c, text=c)
            self.userTable.column(c, width=150, anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.userTable.yview)
        self.userTable.configure(yscrollcommand=scrollbar.set)
        self.userTable.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def load_users(self):
        self.userTable.delete(*self.userTable.get_children())
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    password TEXT,
                    role TEXT
                )
            """)
            con.commit()

            cur.execute("SELECT id, username, role FROM users")
            rows = cur.fetchall()
            for r in rows:
                perm = "All Access (Full Control)" if r[2] == "Admin" else "Billing & Sales Return Only"
                self.userTable.insert("", "end", values=(r[0], r[1], r[2], perm))
        except Exception as e:
            ui_popups.show_error(self.frame, "Error", str(e))
        finally:
            con.close()

    def save_user(self):
        uname = self.username.get().strip()
        pwd = self.password.get().strip()
        urole = self.role.get().strip()

        if not uname or not pwd:
            ui_popups.show_error(self.frame, "Error", "Username and Password cannot be empty.")
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute("""
                INSERT INTO users (username, password, role)
                VALUES (?, ?, ?)
            """, (uname, auth_utils.hash_password(pwd), urole))
            con.commit()
            audit_log.log_action("User Management", "Create User", f"Created user '{uname}' with role '{urole}'")
            ui_popups.show_info(self.frame, "Success", f"User '{uname}' added successfully as {urole}.")
            self.username.set("")
            self.password.set("")
            self.load_users()
        except sqlite3.IntegrityError:
            ui_popups.show_error(self.frame, "Error", "Username already exists. Choose a different name.")
        except Exception as e:
            ui_popups.show_error(self.frame, "Database Error", str(e))
        finally:
            con.close()

    def delete_user(self):
        selected = self.userTable.focus()
        if not selected:
            ui_popups.show_error(self.frame, "Error", "Select a user from the table to delete.")
            return

        values = self.userTable.item(selected)["values"]
        user_id = values[0]
        uname = values[1]

        if uname.lower() == "admin":
            ui_popups.show_error(self.frame, "Error", "Default Admin user cannot be deleted.")
            return

        if ui_popups.show_confirmation(self.frame, "Confirm", f"Are you sure you want to delete user '{uname}'?"):
            con = sqlite3.connect(DB_NAME)
            cur = con.cursor()
            try:
                cur.execute("DELETE FROM users WHERE id=?", (user_id,))
                con.commit()
                audit_log.log_action("User Management", "Delete User", f"Deleted user '{uname}' (id={user_id})")
                ui_popups.show_info(self.frame, "Success", "User deleted successfully.")
                self.load_users()
            except Exception as e:
                ui_popups.show_error(self.frame, "Error", str(e))
            finally:
                con.close()