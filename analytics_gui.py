import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from app_paths import DB_NAME

class AnalyticsDashboard(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.create_ui()
        self.load_report_data()

    def create_ui(self):
        title = tk.Label(self, text="PROFIT ANALYTICS", bg="#1565C0", fg="white", font=("Segoe UI", 18, "bold"), pady=10)
        title.pack(fill="x")

        # டேட்டாவைக் காட்ட Treeview அட்டவணை
        cols = ("Date", "Invoices", "Revenue", "Profit")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=15)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=150, anchor="center")
            
        # .pack() லூப்புக்கு வெளியே சரியாக மாற்றப்பட்டுள்ளது
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

    def load_report_data(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("""
            SELECT date(sale_date) as day, COUNT(*), SUM(grand_total), SUM(total_profit)
            FROM sales
            GROUP BY date(sale_date)
            ORDER BY day DESC
        """)
        rows = cur.fetchall()
        con.close()

        for row in rows:
            self.tree.insert("", tk.END, values=row)