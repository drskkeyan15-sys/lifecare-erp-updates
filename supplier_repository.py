"""
supplier_repository.py
Data-access layer for Supplier Master - second file in the Aug 2026
repository/service-layer pilot slice (see customer_repository.py's
docstring for the full rationale; this file follows the exact same
pattern, extracted from supplier.py without changing any behavior).

Note on credit_period_days: it is NOT created here - it was added to
the `supplier` table via an ALTER TABLE migration in database.py (run
once from main.py at app startup), not by supplier.py itself. This
module's ensure_schema() only recreates what supplier.py's own
create_ui() used to create inline (the base table); adding the ALTER
here too would be a behavior change beyond a pure extraction, not just
a refactor.
"""

import sqlite3
from app_paths import DB_NAME


def ensure_schema():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS supplier(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        contact TEXT,
        mobile TEXT,
        gstin TEXT,
        dlno TEXT,
        city TEXT,
        address TEXT,
        email TEXT
    )""")
    con.commit()
    con.close()


def insert_supplier(name, contact, mobile, gstin, dlno, city, address, email, credit_days):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("""
        INSERT INTO supplier (name, contact, mobile, gstin, dlno, city, address, email, credit_period_days)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, contact, mobile, gstin, dlno, city, address, email, credit_days))
        con.commit()
        return cur.lastrowid
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def update_supplier(supplier_id, name, contact, mobile, gstin, dlno, city, address, email, credit_days):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("""
        UPDATE supplier SET name=?, contact=?, mobile=?, gstin=?, dlno=?, city=?, address=?, email=?, credit_period_days=?
        WHERE id=?
        """, (name, contact, mobile, gstin, dlno, city, address, email, credit_days, supplier_id))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def delete_supplier(supplier_id):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("DELETE FROM supplier WHERE id=?", (supplier_id,))
    con.commit()
    con.close()


def get_supplier(supplier_id):
    """Returns the full row tuple (SELECT *) - same as get_cursor()'s
    original query in supplier.py. credit_period_days lands wherever
    it was ALTER-added (last column) - callers index from row[-1] for
    it, same convention supplier.py's own comment already documents."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT * FROM supplier WHERE id=?", (supplier_id,))
    row = cur.fetchone()
    con.close()
    return row


def list_suppliers():
    """Returns (id, name, contact, mobile, gstin, city,
    credit_period_days) ordered by name - COALESCE'd to 0 for any
    pre-migration row that never got a credit_period_days value."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute(
        "SELECT id, name, contact, mobile, gstin, city, COALESCE(credit_period_days, 0) FROM supplier ORDER BY name"
    )
    rows = cur.fetchall()
    con.close()
    return rows


def search_suppliers(text):
    """Same shape as list_suppliers(), filtered to name OR city
    containing `text` - identical query to search_supplier()'s
    original inline SQL."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        SELECT id, name, contact, mobile, gstin, city, COALESCE(credit_period_days, 0) FROM supplier
        WHERE name LIKE ? OR city LIKE ? ORDER BY name
    """, ("%" + text + "%", "%" + text + "%"))
    rows = cur.fetchall()
    con.close()
    return rows


def count_suppliers():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM supplier")
    total = cur.fetchone()[0]
    con.close()
    return total
