"""
purchase_repository.py
Data-access layer for the Purchase Entry screen - fourth file in the
Aug 2026 repository-layer pass (see customer_repository.py's docstring
for the full rationale; same pattern - plain functions, no classes,
each opening/closing its own connection per call).

Purchase Entry is the most database-heavy screen refactored so far: on
top of simple lookups (supplier list, medicine defaults, shop details)
it owns the actual money/stock-writing transaction (save_purchase())
and the quick Add/Edit Supplier popups. save_purchase() is kept as ONE
function that opens a single connection and does the whole duplicate-
check + insert-per-item + stock-update sequence inside one transaction,
exactly mirroring the original inline code in purchase.py - splitting
it into several auto-committing calls (the pattern used for the
simpler Customer/Supplier/Stock repositories) would have silently
dropped the all-or-nothing guarantee a purchase invoice needs (a save
that fails halfway must roll back every row and every stock update it
already made, not leave partial data committed).
"""

import re
import sqlite3
from datetime import datetime, timedelta

from app_paths import DB_NAME


class DuplicateBillNumber(Exception):
    """Raised by save_purchase() when bill_no already exists in the
    purchase table - purchase.py's save_purchase() catches this
    specifically to show its own Tamil "already recorded" message,
    same as the original inline duplicate check did."""
    def __init__(self, bill_no):
        self.bill_no = bill_no
        super().__init__(f"Duplicate bill number: {bill_no}")


# ======================================
# SUPPLIER LOOKUPS (Add Item bar / Add-New-Supplier / Edit-Supplier
# popups)
# ======================================

def get_supplier_contact(name):
    """(address, mobile) for the read-only Address/Phone display when a
    supplier is picked - None if no such supplier."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT address, mobile FROM supplier WHERE name=?", (name,))
    row = cur.fetchone()
    con.close()
    return row


def supplier_name_exists(name):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT 1 FROM supplier WHERE lower(name)=lower(?)", (name,))
    row = cur.fetchone()
    con.close()
    return row is not None


def insert_quick_supplier(name, mobile, address):
    """Add New Supplier popup's minimal insert - GSTIN/DL No/Credit
    Period/City/Email are left for Supplier Master to fill in later,
    same as before this extraction."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute(
            "INSERT INTO supplier (name, mobile, address, credit_period_days) VALUES (?, ?, ?, 0)",
            (name, mobile, address)
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def get_supplier_for_edit(name):
    """(id, name, mobile, address, gstin, dlno, credit_period_days) for
    the Edit Supplier popup - None if not found."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute(
        "SELECT id, name, mobile, address, gstin, dlno, credit_period_days "
        "FROM supplier WHERE name=?", (name,)
    )
    row = cur.fetchone()
    con.close()
    return row


def supplier_name_exists_excluding(name, exclude_id):
    """Same duplicate-name guard as supplier_name_exists(), but for the
    Edit popup - a supplier is always allowed to keep ITS OWN name."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute(
        "SELECT 1 FROM supplier WHERE lower(name)=lower(?) AND id<>?",
        (name, exclude_id)
    )
    row = cur.fetchone()
    con.close()
    return row is not None


def update_supplier_full(supplier_id, name, mobile, address, gstin, dlno, credit_days):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("""
            UPDATE supplier
            SET name=?, mobile=?, address=?, gstin=?, dlno=?, credit_period_days=?
            WHERE id=?
        """, (name, mobile, address, gstin, dlno, credit_days, supplier_id))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def list_supplier_names():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT name FROM supplier ORDER BY name")
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return rows


# ======================================
# MEDICINE LOOKUPS (Add Item bar / new-medicine creation)
# ======================================

def list_medicine_names():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT name FROM medicine_master ORDER BY name")
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return rows


def get_medicine_defaults(name):
    """(batch, expiry, purchase, sale, gst, hsn, pack_size) to auto-fill
    the Add Item bar when an existing medicine is picked - None if not
    found."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("""
        SELECT batch, expiry, purchase, sale, gst, hsn, pack_size
        FROM medicine_master
        WHERE name=?
    """, (name,))
    row = cur.fetchone()
    con.close()
    return row


def get_medicine_generic(name):
    """Generic/composition text for `name`, or None if the medicine
    doesn't exist at all - feeds _get_formatted_description()'s
    "Description of Goods" convention on export."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT generic FROM medicine_master WHERE name = ?", (name,))
    row = cur.fetchone()
    con.close()
    return row


def insert_new_medicine_from_purchase(name, generic_text, company, category, dosage_form,
                                       composition_id, batch, expiry, purchase_value,
                                       sale_value, gst_value, pack_size):
    """offer_create_medicine()'s INSERT - a brand-new medicine created
    mid-purchase, always needs_review=1 (HSN/Rack still need a
    pharmacist's eyes in Medicine Master even when Brand Master filled
    in the rest - see purchase.py's own comment on this)."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        try:
            cur.execute("ALTER TABLE medicine_master ADD COLUMN needs_review INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        cur.execute("""
            INSERT INTO medicine_master(
                name, generic, company, category, dosage_form, composition_id,
                batch, expiry, purchase, mrp, sale, gst,
                stock, pack_size, free_qty, needs_review
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            name, generic_text, company, category, dosage_form, composition_id,
            batch, expiry, purchase_value, sale_value, sale_value, gst_value,
            0, pack_size, 0, 1
        ))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ======================================
# SAVE PURCHASE - the money/stock transaction. See module docstring for
# why this stays as one connection/one transaction instead of several
# small auto-committing calls.
# ======================================

def _compute_pack_multiplier(pack_raw):
    """Same pack-size-string parsing save_purchase() always used
    ("15'S" -> 15, "10*10" -> 100, "5ML" -> 1, etc) - unchanged, just
    moved here alongside the stock write it feeds."""
    pack_str = str(pack_raw).upper().replace(" ", "").replace("'", "")
    pack_mult = 1
    if '*' in pack_str or 'X' in pack_str:
        nums = re.findall(r'\d+', pack_str)
        if len(nums) >= 2:
            n1, n2 = int(nums[0]), int(nums[1])
            pack_mult = n2 if n1 == 1 else (n1 if "ML" in pack_str or "GM" in pack_str or "MG" in pack_str else n1 * n2)
    elif re.search(r'\d+(ML|GM|MG|G|M|KG|L)$', pack_str):
        pack_mult = 1
    else:
        nums = re.findall(r'\d+', pack_str)
        if nums:
            pack_mult = int(nums[0]) if int(nums[0]) > 0 else 1
    return pack_mult


def save_purchase(bill_no, bill_date, supplier_name, supplier_invoice_no,
                   supplier_invoice_date, items):
    """Saves one whole purchase invoice: duplicate-bill check, one
    INSERT INTO purchase per item, and the matching medicine_master
    stock/pack_size UPDATE per item - all inside a single transaction,
    committed once at the end (or fully rolled back on any error),
    exactly matching purchase.py's original inline save_purchase().

    items: list of dicts, each with keys medicine, batch, expiry, hsn,
    gst (float), purchase (float), sale (float), pack_size (str, may be
    blank), qty (int), total (float).

    Raises DuplicateBillNumber if bill_no is already recorded (checked
    first, before touching anything else). Any other DB error
    propagates as a plain exception after rollback - purchase.py's
    caller shows str(e) via messagebox, same as before.
    """
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        # --- Duplicate bill check ---
        cur.execute("SELECT * FROM purchase WHERE bill_no = ?", (bill_no,))
        if cur.fetchone():
            raise DuplicateBillNumber(bill_no)

        # இந்த supplier-ன் credit period எடுத்து, bill_date + credit days
        # = due_date கணக்கிடுவது - Distributor Ledger-ல் Due/Overdue
        # status காட்ட இதுவே அடிப்படை. bill_date parse தோல்வி அடைந்தா
        # due_date NULL-ஆவே விடப்படும்.
        cur.execute("SELECT credit_period_days FROM supplier WHERE name=?", (supplier_name,))
        supplier_row = cur.fetchone()
        credit_days = int(supplier_row[0] or 0) if supplier_row else 0

        due_date = None
        try:
            bill_dt = datetime.strptime(bill_date.strip(), "%d-%m-%Y")
            due_date = (bill_dt + timedelta(days=credit_days)).strftime("%d-%m-%Y")
        except ValueError:
            pass

        for item in items:
            medicine = item["medicine"]
            batch = item["batch"]

            cur.execute("""
            INSERT INTO purchase
            (
                bill_no, bill_date, supplier, medicine, batch,
                expiry, purchase, sale, gst, hsn, qty, total, due_date,
                supplier_invoice_no, supplier_invoice_date
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                bill_no, bill_date, supplier_name, medicine, batch,
                item["expiry"], item["purchase"], item["sale"], item["gst"],
                item["hsn"], item["qty"], item["total"], due_date,
                supplier_invoice_no, supplier_invoice_date
            ))

            # --- NEW SMART STOCK UPDATE --- prefer what the pharmacist
            # typed/confirmed in THIS purchase's own Pack Size grid cell
            # over blindly re-querying medicine_master.
            pack_raw = item["pack_size"]
            if not pack_raw:
                cur.execute("SELECT pack_size FROM medicine_master WHERE name=? AND batch=?", (medicine, batch))
                row = cur.fetchone()
                pack_raw = (row[0] if row and row[0] else "1")

            pack_mult = _compute_pack_multiplier(pack_raw)
            actual_qty_to_add = item["qty"] * pack_mult

            # --- DUMMY BATCH REPLACEMENT (Aug 2026) ---
            # The old code below this comment ran a single
            # "UPDATE ... WHERE name=? AND batch=?" and stopped there. That
            # silently added stock to NOTHING - 0 rows affected, no error -
            # every time the batch number typed on this invoice didn't
            # already exist as a medicine_master row for that medicine.
            # In real use that is almost every purchase: a genuinely new
            # delivery almost always carries a batch number never seen
            # before. So real purchases for an existing medicine (any batch
            # not identical to one already on file) were being recorded in
            # the `purchase` history table but never actually landing as
            # stock - Stock Management would keep showing the old quantity
            # forever. This replaces that with three cases, tried in order:
            #
            #  1. EXACT match (same name AND same batch already on file) -
            #     same as before: just add stock to that row.
            #  2. No exact match, but this medicine has a placeholder row
            #     with stock=0 and no real batch on file yet (batch is
            #     NULL/blank/'DUMMY' - how a pre-loaded catalog entry, or a
            #     medicine just created via "New Medicine" with nothing
            #     bought against it yet, looks). That placeholder is
            #     replaced in place with this purchase's real batch/expiry/
            #     price/stock, instead of sitting there forever unused
            #     while a second, disconnected row would otherwise need to
            #     be created for the real stock.
            #  3. Neither of the above - a genuinely new batch for a
            #     medicine that already has real stock under other
            #     batch(es). A new medicine_master row is inserted for this
            #     batch, copying the static fields (generic/company/
            #     category/rack/composition/dosage form/reorder level)
            #     from that medicine's existing row so it doesn't have to
            #     be re-typed, alongside the older batches rather than
            #     replacing them - Stock/Billing already support several
            #     batch rows per medicine name (FEFO picks the earliest
            #     expiry among them).
            cur.execute("SELECT id FROM medicine_master WHERE name=? AND batch=?", (medicine, batch))
            exact_row = cur.fetchone()

            if exact_row:
                # Case 1 - identical batch already on file: add to it.
                # pack_size = COALESCE(NULLIF(?, ''), pack_size) - a blank
                # grid cell must NEVER wipe out an existing correct
                # pack_size on file; only a genuinely typed value updates it.
                cur.execute("""
                UPDATE medicine_master
                SET stock = stock + ?,
                    pack_size = COALESCE(NULLIF(?, ''), pack_size)
                WHERE id = ?
                """, (actual_qty_to_add, item["pack_size"], exact_row[0]))
                continue

            # needs_review=1 also counts as a placeholder: that's exactly
            # how a medicine created via "New Medicine" mid-purchase looks
            # (see insert_new_medicine_from_purchase() - always stock=0,
            # needs_review=1) BEFORE its first real purchase lands. Without
            # this, a medicine created with one batch spelling (e.g. "0900")
            # and then genuinely purchased with a differently-typed batch
            # (e.g. "900" - same delivery, just typed without the leading
            # zero the second time) fell through to Case 3 and left that
            # first row stranded at 0 stock forever, needing a manual
            # Medicine Master cleanup - exactly what happened with the
            # very first "aldigesic p" test on 31-Aug-2026.
            cur.execute("""
                SELECT id FROM medicine_master
                WHERE name = ? AND stock = 0
                  AND (batch IS NULL OR batch = '' OR batch = 'DUMMY' OR needs_review = 1)
                ORDER BY id LIMIT 1
            """, (medicine,))
            dummy_row = cur.fetchone()

            if dummy_row:
                # Case 2 - fill in the placeholder with the real batch.
                cur.execute("""
                UPDATE medicine_master
                SET batch = ?, expiry = ?, purchase = ?, mrp = ?, sale = ?,
                    gst = ?, hsn = COALESCE(NULLIF(?, ''), hsn),
                    stock = ?,
                    pack_size = COALESCE(NULLIF(?, ''), pack_size)
                WHERE id = ?
                """, (
                    batch, item["expiry"], item["purchase"], item["sale"], item["sale"],
                    item["gst"], item["hsn"], actual_qty_to_add, item["pack_size"],
                    dummy_row[0],
                ))
                continue

            # Case 3 - a genuinely new batch alongside existing real stock.
            # add_item() only ever lets a medicine onto this grid if it's
            # already in Medicine Master (or was just created via
            # offer_create_medicine()), so a base row should always exist -
            # guarded anyway rather than silently dropping the stock if it
            # somehow doesn't.
            cur.execute("""
                SELECT generic, company, category, hsn, rack, composition_id,
                       dosage_form, needs_refrigeration, reorder_level
                FROM medicine_master WHERE name = ? ORDER BY id LIMIT 1
            """, (medicine,))
            base = cur.fetchone()
            if not base:
                raise ValueError(f"'{medicine}' not found in Medicine Master - cannot record this batch.")
            (generic, company, category, hsn_existing, rack, composition_id,
             dosage_form, needs_refrigeration, reorder_level) = base

            cur.execute("""
                INSERT INTO medicine_master(
                    name, generic, company, category, hsn, gst,
                    batch, expiry, purchase, mrp, sale, stock,
                    pack_size, free_qty, rack, composition_id,
                    reorder_level, dosage_form, needs_refrigeration, needs_review
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
            """, (
                medicine, generic, company, category,
                item["hsn"] or hsn_existing, item["gst"],
                batch, item["expiry"], item["purchase"], item["sale"], item["sale"],
                actual_qty_to_add, item["pack_size"] or "1", 0, rack, composition_id,
                reorder_level, dosage_form, needs_refrigeration,
            ))

        con.commit()
    except DuplicateBillNumber:
        con.rollback()
        raise
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def get_next_purchase_id():
    """MAX(id)+1 from the purchase table, feeding generate_bill_no()'s
    PUR-YYYYMMDD-NNNN sequence - 1 if the table is empty."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT MAX(id) FROM purchase")
    row = cur.fetchone()
    con.close()
    return 1 if row[0] is None else row[0] + 1


# ======================================
# EXPORT (CSV/PDF) SUPPORT
# ======================================

def get_shop_details_row():
    """(shop_name, address, city, phone, gstin, dl20, dl21) from
    settings, or None if the settings table/row/column doesn't exist
    yet - get_shop_details() in purchase.py supplies the LIFE CARE
    PHARMACY fallback when this comes back empty."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("SELECT shop_name, address, city, phone, gstin, dl20, dl21 FROM settings LIMIT 1")
        row = cur.fetchone()
    except Exception:
        row = None
    con.close()
    return row


def get_supplier_export_row(name):
    """(address, mobile, gstin, dlno, credit_period_days) for the
    Export CSV/PDF supplier block - None if not found."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute(
        "SELECT address, mobile, gstin, dlno, credit_period_days FROM supplier WHERE name=?",
        (name,)
    )
    row = cur.fetchone()
    con.close()
    return row


def get_purchase_export_columns_json():
    """Raw saved purchase_export_columns JSON string from settings, or
    None if there's no settings row / the column doesn't exist yet -
    get_export_column_config() merges this onto DEFAULT_EXPORT_COLUMNS."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        cur.execute("SELECT purchase_export_columns FROM settings LIMIT 1")
        row = cur.fetchone()
    except sqlite3.OperationalError:
        row = None
    con.close()
    return row[0] if row else None


def save_purchase_export_columns_json(columns_json):
    """Best-effort persistence (same UPDATE-only pattern as
    dashboard.py's set_dark_mode_pref()) - if settings has zero rows,
    this affects 0 rows and the choice just isn't remembered next
    launch; must never raise and block Export Settings from closing."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    try:
        try:
            cur.execute("ALTER TABLE settings ADD COLUMN purchase_export_columns TEXT")
        except sqlite3.OperationalError:
            pass
        cur.execute("UPDATE settings SET purchase_export_columns=?", (columns_json,))
        con.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()
