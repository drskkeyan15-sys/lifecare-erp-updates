"""
customer_ledger_repository.py
Data-access layer for the Customer Credit & Ledger (Khata) screen -
seventh file in the Aug 2026 repository-layer pass (see
customer_repository.py's docstring for the full rationale; same
pattern - plain functions, no classes, each opening/closing its own
connection per call).
"""

import sqlite3

from app_paths import DB_NAME


def ensure_schema():
    """customer_payments doesn't have its own screen that always runs
    first (database.py also creates it proactively, but this call is
    kept as a harmless no-op safety net, matching the original inline
    CREATE TABLE IF NOT EXISTS this screen always ran before reading or
    writing that table)."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer TEXT,
            amount REAL,
            pay_date TEXT
        )
    """)
    con.commit()
    con.close()


def list_customer_names_from_sales():
    """Every distinct non-blank customer name that has ever appeared on
    a bill - feeds the Customer combobox. Returns [] on any failure
    (matches the original's own try/except-swallow-everything)."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("SELECT DISTINCT customer FROM sales WHERE customer IS NOT NULL AND customer <> ''")
        rows = [r[0] for r in cur.fetchall()]
    except Exception:
        rows = []
    con.close()
    return rows


def get_ledger_rows(customer):
    """(bill_date, bill_no, 'Credit Bill', total, 0.0) for every sale +
    (pay_date, 'PAYMENT', 'Cash Payment', 0.0, amount) for every payment
    this customer has on file - load_customer_ledger() merges and sorts
    these by date itself, same as the original inline queries."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        SELECT bill_date, bill_no, 'Credit Bill', total, 0.0
        FROM sales
        WHERE customer=?
    """, (customer,))
    sales_rows = cur.fetchall()

    cur.execute("""
        SELECT pay_date, 'PAYMENT', 'Cash Payment', 0.0, amount
        FROM customer_payments
        WHERE customer=?
    """, (customer,))
    payment_rows = cur.fetchall()
    con.close()
    return sales_rows, payment_rows


def record_payment(customer, amount, pay_date):
    """Ensures customer_payments exists, then inserts one payment row -
    same two-step (CREATE TABLE IF NOT EXISTS then INSERT) the original
    record_payment() always did in one connection."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS customer_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer TEXT,
                amount REAL,
                pay_date TEXT
            )
        """)
        cur.execute("""
            INSERT INTO customer_payments (customer, amount, pay_date)
            VALUES (?, ?, ?)
        """, (customer, amount, pay_date))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
