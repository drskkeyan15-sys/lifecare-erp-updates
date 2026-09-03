"""
supplier_ledger_repository.py
Data-access layer for the Supplier Payment & GST Ledger Tracking
screen - eighth (and, for this Aug 2026 pass, final) file in the
repository-layer series (see customer_repository.py's docstring for
the full rationale; same pattern - plain functions, no classes, each
opening/closing its own connection per call).

Two of this screen's original methods (load_suppliers(), the flexible-
scan half of load_ledger()) deliberately introspect sqlite_master/
PRAGMA table_info at runtime instead of querying a fixed table/column
list - a defensive "whatever this install's purchase-like table is
actually called" scan, predating the Aug 2026 repository work. That
scanning logic is moved here verbatim (same table-name/column-name
guesses, same LIKE-based supplier matching) rather than replaced with
a fixed query, since narrowing it to today's exact schema would be a
behavior change, not a refactor.
"""

import sqlite3

from app_paths import DB_NAME


def list_supplier_names_dynamic():
    """Scans every table whose name contains "supplier"/"party"/
    "vendor" for a column whose name contains "name", and collects
    every distinct non-blank value found - same defensive discovery
    load_suppliers() always did, in case the real supplier table isn't
    named/shaped exactly as expected on some install. Returns a sorted,
    de-duplicated list; raises on failure (caller shows the Tamil error
    message, same as before)."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cur.fetchall()]

        suppliers = []
        for table in tables:
            if 'supplier' in table.lower() or 'party' in table.lower() or 'vendor' in table.lower():
                cur.execute(f"PRAGMA table_info({table})")
                columns = [col[1] for col in cur.fetchall()]

                for col in columns:
                    if 'name' in col.lower():
                        try:
                            cur.execute(f"SELECT DISTINCT {col} FROM {table}")
                            for r in cur.fetchall():
                                if r[0]:
                                    suppliers.append(str(r[0]).strip())
                        except Exception:
                            pass

        return sorted(set(suppliers))
    finally:
        con.close()


def get_purchase_like_rows(supplier_like):
    """Scans for the first of purchases/purchase/purchase_bills/
    bill_master that has a supplier-ish column, then returns
    (bill_no, bill_date, total, supplier_invoice_no) rows LIKE-matching
    `supplier_like` - same table/column-name guessing load_ledger()'s
    flexible-scan half always did. Returns [] if nothing matched (or
    none of those tables exist/have a usable supplier column).

    2026-09-03: added supplier_invoice_no as a 4th column (the
    supplier's own bill number, entered on Purchase Entry's "Supp. Inv.
    No" field) - the Supplier Ledger table used to have no way to show
    it at all, so a pharmacist reconciling against a physical supplier
    bill had nothing to match on besides our own internal Bill No.
    Defensive per-table: only added to the SELECT when the matched
    table actually has that column, so a differently-shaped
    purchases/purchase_bills/bill_master table (or an older DB before
    this column existed) still works exactly as before, just with
    supplier_invoice_no coming back as None for every row."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cur.fetchall()]

        purchases = []
        for t_name in ['purchases', 'purchase', 'purchase_bills', 'bill_master']:
            if t_name in tables:
                cur.execute(f"PRAGMA table_info({t_name})")
                cols = [c[1] for c in cur.fetchall()]

                sup_col = 'supplier_name' if 'supplier_name' in cols else ('supplier' if 'supplier' in cols else None)
                tot_col = 'total' if 'total' in cols else ('grand_total' if 'grand_total' in cols else 'net_amount')
                date_col = 'bill_date' if 'bill_date' in cols else ('date' if 'date' in cols else 'purchase_date')
                bill_col = 'bill_no' if 'bill_no' in cols else 'invoice_no'
                supp_inv_col = 'supplier_invoice_no' if 'supplier_invoice_no' in cols else None

                if sup_col:
                    try:
                        select_cols = f"{bill_col}, {date_col}, {tot_col}"
                        if supp_inv_col:
                            select_cols += f", {supp_inv_col}"
                        cur.execute(
                            f"SELECT {select_cols} FROM {t_name} WHERE {sup_col} LIKE ?",
                            (f"%{supplier_like}%",)
                        )
                        rows = cur.fetchall()
                        if rows:
                            purchases = rows if supp_inv_col else [(r[0], r[1], r[2], None) for r in rows]
                            break
                    except Exception:
                        pass
        return purchases
    finally:
        con.close()


def ensure_supplier_payments_schema():
    """CREATE TABLE IF NOT EXISTS + the payment_mode ALTER migration
    (Cash/Bank/UPI/Cheque, needed for Daybook's cash-out figure) -
    same two-step load_ledger() always ran before reading
    supplier_payments."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier TEXT,
            amount REAL,
            pay_date TEXT
        )
    """)
    try:
        cur.execute("ALTER TABLE supplier_payments ADD COLUMN payment_mode TEXT DEFAULT 'Cash'")
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()


def get_supplier_payments_like(supplier_like):
    """(pay_date, amount) for every supplier_payments row whose
    `supplier` LIKE-matches `supplier_like` - same fuzzy match used for
    the purchases scan above, so the ledger's Payment rows line up with
    whichever supplier-name variant the Purchase rows matched."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        SELECT pay_date, amount
        FROM supplier_payments
        WHERE supplier LIKE ?
    """, (f"%{supplier_like}%",))
    rows = cur.fetchall()
    con.close()
    return rows


def get_invoice_rows(supplier_name):
    """(bill_no, bill_date, due_date, SUM(total)) grouped per real
    invoice from the KNOWN `purchase` table (exact supplier match, NOT
    the LIKE-based fuzzy match the two functions above use) - feeds
    compute_invoice_status()'s FIFO payment allocation."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        SELECT bill_no, bill_date, due_date, SUM(total)
        FROM purchase
        WHERE supplier = ?
        GROUP BY bill_no, bill_date, due_date
    """, (supplier_name,))
    rows = cur.fetchall()
    con.close()
    return rows


def get_total_payments(supplier_name):
    """Total ever paid to `supplier_name` (exact match) - the payment
    pool compute_invoice_status() spends oldest-invoice-first."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM supplier_payments WHERE supplier = ?", (supplier_name,))
    total = float(cur.fetchone()[0] or 0)
    con.close()
    return total


def insert_supplier_payment(supplier, amount, pay_date, payment_mode):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("""
            INSERT INTO supplier_payments (supplier, amount, pay_date, payment_mode)
            VALUES (?, ?, ?, ?)
        """, (supplier, amount, pay_date, payment_mode))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
