"""
purchase_order_repository.py
Data-access layer for the Purchase Order screen - sixth file in the
Aug 2026 repository-layer pass (see customer_repository.py's docstring
for the full rationale; same pattern - plain functions, no classes,
each opening/closing its own connection per call).

save_purchase_order() keeps the "generate PO number then INSERT one
row per item" sequence as a single connection/transaction, same
reasoning as purchase_repository.save_purchase()/billing_repository.
save_bill() - a save that fails partway must roll back every item row
it already inserted, not leave a PO with only some of its items on
file.
"""

import sqlite3

from app_paths import DB_NAME


def list_supplier_names():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT name FROM supplier ORDER BY name")
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return rows


def list_medicine_names():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT DISTINCT name FROM medicine_master ORDER BY name")
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return rows


def get_low_stock_with_last_supplier(threshold):
    """Same low-stock + last-supplier-used query as Smart Alerts' own
    load_low_stock() (see purchase_order.py's module docstring for why
    it's a separate copy rather than a shared import).

    Returns (rows, last_supplier) where rows is a list of
    (name, stock, effective_threshold) tuples (medicine_master's own
    reorder_level overrides `threshold` per-medicine when set), and
    last_supplier is a {medicine_name: supplier_name} dict built from
    each medicine's most recent purchase row - empty dict if no
    low-stock medicines were found at all."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        SELECT name, stock,
               CASE WHEN reorder_level > 0 THEN reorder_level ELSE ? END AS effective_threshold
        FROM medicine_master
        WHERE stock <= CASE WHEN reorder_level > 0 THEN reorder_level ELSE ? END
        ORDER BY stock ASC
    """, (threshold, threshold))
    rows = cur.fetchall()

    names = sorted({r[0] for r in rows})
    last_supplier = {}
    if names:
        placeholders = ",".join("?" * len(names))
        cur.execute(f"""
            SELECT medicine, supplier FROM purchase
            WHERE medicine IN ({placeholders})
            AND id IN (SELECT MAX(id) FROM purchase WHERE medicine IN ({placeholders}) GROUP BY medicine)
        """, names + names)
        for medicine, supplier in cur.fetchall():
            last_supplier[medicine] = supplier
    con.close()
    return rows, last_supplier


def get_medicines_with_open_po():
    """Set of medicine names that already have at least one purchase_orders
    row with status 'Draft' or 'Sent' (i.e. still in flight - not yet
    Received or Cancelled).

    Added Aug 2026 for auto_po.py's background draft generator: without
    this check, re-launching the app (or leaving it open across a shift)
    would re-create a duplicate Draft PO for the same low-stock medicine
    every time the auto-check runs. A medicine only becomes eligible for
    a fresh auto-draft again once its existing PO is marked Received or
    Cancelled."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT DISTINCT medicine FROM purchase_orders WHERE status IN ('Draft', 'Sent')")
    names = {r[0] for r in cur.fetchall()}
    con.close()
    return names


def save_purchase_order(date, supplier, items, note, created_by):
    """Generates the next PO-YYYYMMDD-NNN number for `date` and inserts
    one purchase_orders row per item, all in one transaction (committed
    once, or fully rolled back on any error) - matches purchase_order.
    py's original inline save_po() exactly.

    items: list of (medicine_name, qty) tuples.

    Returns the generated po_no on success. Raises the underlying
    exception after rollback on failure; purchase_order.py's caller
    shows str(e) via messagebox, same as before."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("SELECT COUNT(DISTINCT po_no) FROM purchase_orders WHERE po_date=?", (date,))
        seq = (cur.fetchone()[0] or 0) + 1
        po_no = f"PO-{date.replace('-', '')}-{seq:03d}"

        for name, qty in items:
            cur.execute(
                "INSERT INTO purchase_orders(po_no, po_date, supplier, medicine, qty, note, status, created_by) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (po_no, date, supplier, name, qty, note, "Draft", created_by)
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return po_no


def list_po_groups():
    """(po_no, po_date, supplier, status, item_count) for every distinct
    PO, newest first - feeds the History table."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        SELECT po_no, po_date, supplier, status, COUNT(*) AS item_count
        FROM purchase_orders
        GROUP BY po_no
        ORDER BY po_date DESC, po_no DESC
    """)
    rows = cur.fetchall()
    con.close()
    return rows


def get_po_items(po_no):
    """(medicine, qty, note, status) for every row of `po_no`, in
    insertion order - feeds the View Items popup."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute(
        "SELECT medicine, qty, note, status FROM purchase_orders WHERE po_no=? ORDER BY id",
        (po_no,)
    )
    rows = cur.fetchall()
    con.close()
    return rows


def update_po_status(po_no, new_status):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("UPDATE purchase_orders SET status=? WHERE po_no=?", (new_status, po_no))
    con.commit()
    con.close()
