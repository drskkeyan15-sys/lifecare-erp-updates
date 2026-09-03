"""
customer_repository.py
Data-access layer for Customer Master - the first slice of the Aug 2026
repository/service-layer pass (see purchase.py's DB_NAME comment history
for the kind of "silent wrong file" bug a scattered sqlite3.connect()
pattern invites, and this session's FK-trigger migration for a related
"nobody guards the schema centrally" gap).

Before this file, customer.py opened its own sqlite3 connection and
wrote its own SQL inline in each button-handler method (save_customer,
update_customer, delete_customer, load_customers, search_customer,
select_customer). That's fine for a single screen, but it means:
  1. The SQL can only be tested by driving the whole Tkinter screen -
     there is no way to unit-test "does search find a partial name
     match" without a live GUI event loop.
  2. If a future Multi-Shop/Cloud Sync feature ever needs customer data
     to come from a different source (a network API instead of the
     local sqlite file), every screen that touches customers.py's table
     would need to be found and edited individually.

This module is the single seam both problems get fixed through: all
`customers` table SQL lives here, customer.py only calls these
functions. Behavior is unchanged from what customer.py did before -
this is a pure extraction, not a redesign. See test_customer_repository.
py for the tests this now makes possible.

Chosen as the FIRST screen to move to this pattern specifically because
it's simple CRUD with no FIFO/GST/stock-adjustment logic entangled in
it (unlike Stock/Purchase/Billing) - low risk to prove the pattern on,
not because it's the most valuable one to convert.
"""

import sqlite3
from app_paths import DB_NAME


def ensure_schema():
    """Creates the customers table if missing, and adds any columns a
    later version of this app introduced - identical migration SQL to
    what customer.py's own create_table() used to run directly."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT,
        phone TEXT,
        address TEXT,
        doctor TEXT,
        gstin TEXT
    )
    """)

    for column_sql in (
        "ALTER TABLE customers ADD COLUMN address TEXT",
        "ALTER TABLE customers ADD COLUMN doctor TEXT",
        "ALTER TABLE customers ADD COLUMN gstin TEXT",
        "ALTER TABLE customers ADD COLUMN discount_percent REAL DEFAULT 0",
        "ALTER TABLE customers ADD COLUMN credit_limit REAL DEFAULT 0",
    ):
        try:
            cur.execute(column_sql)
        except sqlite3.OperationalError:
            pass  # column already exists

    con.commit()
    con.close()


def insert_customer(name, phone, address, doctor, gstin, discount_percent, credit_limit):
    """Returns the new row's id. Raises on failure - caller (customer.py)
    is responsible for the try/except + messagebox, same division of
    responsibility as before (this module never touches Tkinter)."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("""
        INSERT INTO customers (customer_name, phone, address, doctor, gstin, discount_percent, credit_limit)
        VALUES (?,?,?,?,?,?,?)
        """, (name, phone, address, doctor, gstin, discount_percent, credit_limit))
        con.commit()
        return cur.lastrowid
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def update_customer(customer_id, name, phone, address, doctor, gstin, discount_percent, credit_limit):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("""
        UPDATE customers SET customer_name=?, phone=?, address=?, doctor=?, gstin=?, discount_percent=?, credit_limit=? WHERE id=?
        """, (name, phone, address, doctor, gstin, discount_percent, credit_limit, customer_id))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def delete_customer(customer_id):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("DELETE FROM customers WHERE id=?", (customer_id,))
    con.commit()
    con.close()


def get_customer(customer_id):
    """Returns (customer_name, phone, address, doctor, gstin,
    discount_percent, credit_limit) or None - same column order/shape
    select_customer() in customer.py already expected."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
    SELECT customer_name, phone, address, doctor, gstin, discount_percent, credit_limit FROM customers WHERE id=?
    """, (customer_id,))
    row = cur.fetchone()
    con.close()
    return row


def list_customers():
    """Returns every customer as (id, customer_name, phone, address,
    doctor, gstin, discount_percent, credit_limit), ordered by name -
    same shape load_customers() in customer.py fed straight into the
    Treeview before."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
    SELECT id, customer_name, phone, address, doctor, gstin, discount_percent, credit_limit
    FROM customers ORDER BY customer_name
    """)
    rows = cur.fetchall()
    con.close()
    return rows


def search_customers(text):
    """Same shape as list_customers(), filtered to names containing
    `text` (case-insensitive - LIKE's default collation) - identical
    query to what search_customer() in customer.py ran inline."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
    SELECT id, customer_name, phone, address, doctor, gstin, discount_percent, credit_limit
    FROM customers WHERE customer_name LIKE ? ORDER BY customer_name
    """, ("%" + text + "%",))
    rows = cur.fetchall()
    con.close()
    return rows


def count_customers():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM customers")
    total = cur.fetchone()[0]
    con.close()
    return total
