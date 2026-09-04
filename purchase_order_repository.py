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
from datetime import datetime, timedelta

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


def get_velocity_low_stock_with_last_supplier(lead_days=15, window_days=30):
    """Sales-velocity-based reorder check for auto_po.py's background
    generator (Sep 2026) - a headless, non-tkinter counterpart to
    stock_alerts_gui.py's SmartAlertsDashboard.load_reorder_predictions().
    Deliberately a separate copy rather than a shared import, matching
    this module's own established convention (see
    get_low_stock_with_last_supplier()'s docstring above) - the GUI
    version stays free to add tkinter-only concerns (a live IntVar, a
    tksheet row cache) without this background/headless copy having to
    change with it, and vice versa.

    Catches a genuinely different case than get_low_stock_with_last_
    supplier(): a fast-selling medicine can still be well above its
    numeric reorder_level today and yet run out within `lead_days` at
    its current sales pace - the flat-threshold check has no way to see
    that coming. A slow mover just under threshold is NOT flagged here
    even if it would be by the threshold check - the two checks are
    meant to be combined (see auto_po.py), not to replace one another.

    Returns (rows, last_supplier) where rows is a list of
    (name, stock, suggested_qty) tuples for medicines predicted to run
    out within `lead_days` given their last `window_days` days of
    sales, and last_supplier is a {medicine_name: supplier_name} dict
    for exactly those medicines. Medicines with no sales in the window
    (no usage data to predict from) are never included - matches the
    GUI version's own "not enough data" guard exactly, rather than
    risking a wrong alert built on zero evidence."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()

    cur.execute("SELECT name, SUM(stock) FROM medicine_master GROUP BY name")
    stock_by_name = dict(cur.fetchall())

    # sales.bill_date is stored as free-text "YYYY-MM-DD" (billing.py's
    # save_bill() - see stock_alerts_gui.py's load_reorder_predictions()
    # for the fuller history on this), parsed by hand rather than
    # trusted via SQL string sort, same reasoning as that method.
    cur.execute("""
        SELECT s.bill_date, si.medicine, si.qty
        FROM sales_items si
        JOIN sales s ON si.bill_no = s.bill_no
    """)
    sale_rows = cur.fetchall()
    con.close()

    cutoff = datetime.now() - timedelta(days=window_days)
    usage_qty = {}
    for bill_date_str, medicine, qty in sale_rows:
        try:
            d = datetime.strptime((bill_date_str or "").strip(), "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        if d >= cutoff:
            usage_qty[medicine] = usage_qty.get(medicine, 0) + (qty or 0)

    rows = []
    for name, stock in stock_by_name.items():
        total_used = usage_qty.get(name, 0)
        if total_used <= 0:
            continue
        avg_daily = total_used / window_days
        days_remaining = stock / avg_daily
        if days_remaining > lead_days:
            continue
        suggested_qty = max(int(round(avg_daily * lead_days)) - int(stock), 1)
        rows.append((name, stock, suggested_qty))

    names = sorted(r[0] for r in rows)
    last_supplier = {}
    if names:
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
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


def get_medicine_price_by_supplier(medicine_name):
    """Supplier Price Comparison (Sep 2026) - feeds the Purchase Order
    screen's "Best Price" hint (see purchase_order.py's
    _update_price_hint()), so the pharmacist can see, right while
    picking a Medicine, which Supplier has historically charged the
    least for it - instead of relying on memory or flipping between
    screens.

    One row per Supplier this medicine has ever been bought from - that
    Supplier's own MOST RECENT purchase row's price (not an average),
    since prices drift over months and the last price paid is the one a
    new order would most likely repeat. Rows with a blank/NULL supplier
    (a purchase entered without picking one) or a NULL price are
    excluded - nothing useful to compare there.

    Returns a list of (supplier, purchase_price, bill_date) tuples
    sorted cheapest-first; empty if this medicine has never actually
    been purchased from anyone yet (e.g. a brand-new medicine, or one
    only ever entered via Stock Adjustment)."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        SELECT supplier, purchase, bill_date
        FROM purchase
        WHERE medicine = ?
        AND supplier IS NOT NULL AND TRIM(supplier) != ''
        AND purchase IS NOT NULL
        AND id IN (
            SELECT MAX(id) FROM purchase
            WHERE medicine = ? AND supplier IS NOT NULL AND TRIM(supplier) != ''
            GROUP BY supplier
        )
        ORDER BY purchase ASC
    """, (medicine_name, medicine_name))
    rows = cur.fetchall()
    con.close()
    return rows


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
