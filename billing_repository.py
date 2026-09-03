"""
billing_repository.py
Data-access layer for the Billing (counter sale) screen - fifth file in
the Aug 2026 repository-layer pass (see customer_repository.py's
docstring for the full rationale; same pattern - plain functions, no
classes, each opening/closing its own connection per call).

Billing is the highest-stakes screen refactored so far: save_bill() is
the one function that actually deducts stock and records revenue, so
(same reasoning as purchase_repository.py's save_purchase()) it stays
as ONE function/ONE transaction here rather than being split into
several small auto-committing calls - a save that fails partway must
roll back the sales header, every sales_items row, and every stock
deduction it already made, not leave a half-sold bill on record.
"""

import re
import sqlite3
from datetime import datetime

from app_paths import DB_NAME


def _extract_pack_qty(pack_raw):
    """Same pack_size-string-to-count parsing save_bill() always used
    ("15'S" -> 15, blank/unparseable -> 1) - unchanged, just moved here
    alongside the purchase-cost calc it feeds."""
    pack_nums = re.findall(r'\d+', str(pack_raw or "1"))
    pack_qty = int(pack_nums[0]) if pack_nums else 1
    return pack_qty if pack_qty > 0 else 1


# ======================================
# MEDICINE LOOKUPS
# ======================================

def get_medicine_generic(name):
    """Generic/composition text for `name`, or None if not found -
    feeds get_formatted_billing_item() (invoice/receipt brand+generic
    display) and show_substitutes()."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT generic FROM medicine_master WHERE name = ?", (name,))
    row = cur.fetchone()
    con.close()
    return row


def list_medicine_names():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT DISTINCT name FROM medicine_master ORDER BY name")
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return rows


def search_medicine_names(text):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute(
        "SELECT DISTINCT name FROM medicine_master WHERE name LIKE ? ORDER BY name",
        ("%" + text + "%",)
    )
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return rows


def get_sales_items_with_dates():
    """(medicine, qty, bill_date) for every sales_items row ever
    recorded, joined to its sales.bill_date - load_quick_picks() does
    the date-cutoff filtering and ranking itself (same manual strptime
    approach reports.py's own slow_moving_report()/expiry_report() use,
    since bill_date is free-text "YYYY-MM-DD", not filtered in SQL
    here)."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        SELECT si.medicine, si.qty, s.bill_date
        FROM sales_items si
        JOIN sales s ON s.bill_no = si.bill_no
    """)
    rows = cur.fetchall()
    con.close()
    return rows


def get_batches_in_stock(name):
    """(batch, stock, sale, pack_size, expiry) for every in-stock batch
    of `name` - get_fifo_batches() turns this into the sorted
    earliest-expiry-first list add_item()/get_medicine() use."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        SELECT batch, stock, sale, pack_size, expiry
        FROM medicine_master
        WHERE name=? AND stock > 0
    """, (name,))
    rows = cur.fetchall()
    con.close()
    return rows


def get_medicine_name_by_barcode(code):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT DISTINCT name FROM medicine_master WHERE barcode=?", (code,))
    row = cur.fetchone()
    con.close()
    return row


def get_medicine_expiry(name, batch):
    """Expiry for a specific name+batch - used per bill-item when
    printing the A4 PDF invoice / thermal receipt."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT expiry FROM medicine_master WHERE name=? AND batch=?", (name, batch))
    row = cur.fetchone()
    con.close()
    return row


def get_medicine_expiry_by_name(name):
    """Same as get_medicine_expiry() but matched by name alone (no
    batch) - kept for get_expiry_date(), unused elsewhere in this app
    currently but refactored faithfully rather than dropped."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT expiry FROM medicine_master WHERE name=?", (name,))
    row = cur.fetchone()
    con.close()
    return row


def get_habit_forming_names(names):
    """Which of `names` (an iterable of medicine names currently in the
    bill) are Schedule H1 / habit-forming, via composition_master
    through medicine_master's composition_id link. One connection for
    the whole batch (matches the original loop's own single-connection
    shape) - returns a sorted list, empty if none flagged or `names` is
    empty."""
    names = list(names)
    if not names:
        return []
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    flagged = []
    try:
        for name in names:
            cur.execute("""
                SELECT cm.habit_forming FROM medicine_master mm
                JOIN composition_master cm ON cm.composition_id = mm.composition_id
                WHERE mm.name = ? LIMIT 1
            """, (name,))
            row = cur.fetchone()
            if row and row[0]:
                flagged.append(name)
    finally:
        con.close()
    return sorted(flagged)


# ======================================
# CUSTOMER / DOCTOR AUTOFILL
# ======================================

def get_default_doctor():
    """Settings' Default Doctor, or "" if settings has no row yet /
    the query fails for any reason - _apply_default_doctor() falls
    back to a blank field on that, matching the original's own
    try/except-swallow-everything behaviour."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("SELECT default_doctor FROM settings LIMIT 1")
        row = cur.fetchone()
    except Exception:
        row = None
    con.close()
    return (row[0] or "").strip() if row else ""


def get_customer_doctor(name):
    """Customer Master's own `doctor` field for `name`, or None on any
    failure (missing table/column, no match) - _autofill_doctor()
    treats any exception here as "nothing to suggest", same as before."""
    try:
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT doctor FROM customers WHERE customer_name=?", (name,))
        row = cur.fetchone()
        con.close()
        return row
    except Exception:
        return None


def get_customer_discount_percent(name):
    """Same shape as get_customer_doctor(), for the loyalty discount %
    suggestion."""
    try:
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT discount_percent FROM customers WHERE customer_name=?", (name,))
        row = cur.fetchone()
        con.close()
        return row
    except Exception:
        return None


def get_customer_credit_status(name):
    """(credit_limit, total_credit_ever_billed, total_paid) for `name` -
    None if the customer isn't on file. save_bill()'s Credit Limit
    check uses this to decide whether to warn before saving; matches
    customer_ledger.py's own Khata "every sale counts as credit until
    offset by a payment" formula exactly."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT credit_limit FROM customers WHERE customer_name=?", (name,))
    row = cur.fetchone()
    if not row:
        con.close()
        return None
    limit = row[0] or 0
    cur.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE customer=?", (name,))
    total_credit = cur.fetchone()[0] or 0
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM customer_payments WHERE customer=?", (name,))
    total_paid = cur.fetchone()[0] or 0
    con.close()
    return {"limit": limit, "total_credit": total_credit, "total_paid": total_paid}


# ======================================
# SAVE BILL - the money/stock transaction. See module docstring for
# why this stays as one connection/one transaction.
# ======================================

def get_next_sales_id():
    """MAX(id)+1 from the sales table, feeding generate_bill_no()'s
    BILL-YYYYMMDD-NNNN sequence - 1 if the table is empty/missing."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("SELECT MAX(id) FROM sales")
        row = cur.fetchone()
        next_id = 1 if row[0] is None else row[0] + 1
    except Exception:
        next_id = 1
    con.close()
    return next_id


def save_bill(bill_no, bill_date, customer, doctor, subtotal, discount_amt,
              grand_total, payment_mode, received, balance, address, items):
    """Saves one whole bill: one INSERT INTO sales header row, then per
    item an INSERT INTO sales_items (with unit_purchase derived from
    medicine_master.purchase/pack_size/gst, same landed-cost convention
    used everywhere else in this app) and a medicine_master stock
    deduction - all inside a single transaction, committed once at the
    end (or fully rolled back on any error), exactly matching
    billing.py's original inline save_bill().

    items: list of dicts, each with keys medicine, batch, qty (int),
    price (float, the sale price actually charged), total (float).

    Raises the underlying exception (e.g. sqlite3.IntegrityError from
    the trg_sales_items_medicine_exists_ins trigger - Aug 2026 FK-
    integrity work - if an item's medicine somehow isn't in
    medicine_master) after rolling back; billing.py's caller shows
    str(e) via messagebox, same as before.
    """
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("""
        INSERT INTO sales(bill_no, bill_date, customer, doctor, subtotal, discount, total, payment_mode, received_amt, balance_amt, address)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (
            bill_no, bill_date, customer, doctor, subtotal, discount_amt,
            grand_total, payment_mode, received, balance, address
        ))

        for item in items:
            medicine = item["medicine"]
            batch = item["batch"]
            qty = item["qty"]
            price = item["price"]
            total = item["total"]

            cur.execute("SELECT purchase, pack_size, gst FROM medicine_master WHERE name=? AND batch=?", (medicine, batch))
            row = cur.fetchone()

            if row:
                raw_purchase = float(row[0] or 0.0)
                pack_qty = _extract_pack_qty(row[1])
                gst_rate = float(row[2] or 0.0)

                unit_purchase_base = raw_purchase / pack_qty if raw_purchase > 0 else 0.0
                unit_purchase = unit_purchase_base * (1 + (gst_rate / 100))
            else:
                unit_purchase = 0.0

            cur.execute("""
                INSERT INTO sales_items(bill_no, medicine, batch, qty, purchase, sale, total)
                VALUES(?,?,?,?,?,?,?)
            """, (bill_no, medicine, batch, qty, unit_purchase, price, total))

            cur.execute("""
            UPDATE medicine_master SET stock = stock - ? WHERE name = ? AND batch = ?
            """, (qty, medicine, batch))

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ======================================
# SHOP DETAILS / EXPORT
# ======================================

def get_shop_details_row():
    """(shop_name, address, city, phone, gstin, dl20, dl21, fssai,
    footer, show_payment_on_receipt, default_doctor, upi_id,
    receipt_logo_path, thermal_printer_name) from settings -
    progressively falls back to older, narrower SELECTs (and pads the
    missing trailing columns with None) on a DB where Settings has
    never been reopened since a given column was added, so a genuinely
    old install still gets its DL/GSTIN/footer instead of losing
    everything to one missing column. Returns None if there's no
    settings row / none of the SELECTs succeed - get_shop_details()
    in billing.py supplies the LIFE CARE PHARMACY fallback dict for that
    case, same as before this extraction."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute(
            "SELECT shop_name, address, city, phone, gstin, dl20, dl21, fssai, footer, "
            "show_payment_on_receipt, default_doctor, upi_id, "
            "receipt_logo_path, thermal_printer_name FROM settings LIMIT 1"
        )
        row = cur.fetchone()
    except Exception:
        try:
            cur.execute(
                "SELECT shop_name, address, city, phone, gstin, dl20, dl21, fssai, footer, "
                "show_payment_on_receipt, default_doctor, upi_id FROM settings LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                row = row + (None, None)
        except Exception:
            try:
                cur.execute(
                    "SELECT shop_name, address, city, phone, gstin, dl20, dl21, fssai, footer, "
                    "show_payment_on_receipt, default_doctor FROM settings LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    row = row + (None, None, None)
            except Exception:
                try:
                    cur.execute(
                        "SELECT shop_name, address, city, phone, gstin, dl20, dl21, fssai, footer, "
                        "show_payment_on_receipt FROM settings LIMIT 1"
                    )
                    row = cur.fetchone()
                    if row:
                        row = row + (None, None, None, None)
                except Exception:
                    row = None
    con.close()
    return row
