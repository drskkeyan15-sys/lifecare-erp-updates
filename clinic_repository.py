"""
clinic_repository.py
LifeCare Pharmacy ERP - Clinic Ledger data access layer.

Per CODING_RULES.md ("UI logic must stay separate from database logic. No
SQL inside UI code.") every clinic_*.py screen calls into this module
instead of running its own sqlite3 queries - same split billing.py /
billing_repository.py already use.

Nothing here creates a second inventory or a second database. Every
medicine/injection/consumable line item is deducted straight out of the
SAME medicine_master.stock the Billing/Purchase/Stock screens already
maintain, using the SAME pack-multiplier (pricing_utils.get_pack_multiplier)
and FEFO batch-consumption idea pricing_utils.allocate_fifo()/billing.py's
add_item() already implement. Purchase-cost-per-unit is computed
GST-inclusive, matching billing_repository.save_bill()'s own
`unit_purchase_base * (1 + gst/100)` convention, so a medicine's "true
cost" means the same number everywhere in this app.

No financial row is ever hard-deleted. cancel_visit() reverses stock and
marks status='Cancelled' - the exact same non-destructive convention
sales_return.py/purchase_return.py already use for their own reversals.
"""

import sqlite3
from datetime import datetime

from app_paths import DB_NAME
from pricing_utils import get_pack_multiplier, get_unit_price
from money import to_money, money_sum
import audit_log


class InsufficientStockError(Exception):
    """Raised when a visit tries to use more of a medicine than is
    currently in stock (across all non-expired batches combined)."""
    pass


def _connect():
    return sqlite3.connect(DB_NAME)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# PROFIT BREAKDOWN (pure function - no DB access, no side effects)
# ============================================================

def compute_profit_breakdown(total_amount_collected, medicines):
    """
    Calculates the three distinct profit metrics a clinic visit needs,
    from a patient charge and a list of medicines used. This is a PURE
    function (no database, no stock deduction) - it is the "what would
    this visit's numbers be" calculator, usable standalone (a quick
    manual estimate, an API preview before Save) as well as internally
    by range_summary()/gross_profit_report() below, which derive the
    same three numbers from already-saved clinic_visits rows.

    Args:
        total_amount_collected (float): the Patient Charge - the actual
            rupee amount collected for this visit (consultation +
            medicines combined, however the clinic actually billed it -
            see add_visit()'s `total_collected` docstring for why this
            is not always Consultation + sum(MRP)).
        medicines (list[dict]): one dict per medicine/injection/consumable
            used, each with keys:
                'medicine_name' (str)
                'qty' (float)
                'mrp_per_unit' (float)
                'purchase_cost_with_gst_per_unit' (float) - the REAL
                    cost per sellable unit, GST already included (same
                    convention as _fefo_batches_with_cost()'s
                    unit_purchase_cost below - pass that number here,
                    not the raw pack purchase rate).

    Returns:
        dict (JSON-serializable) with the per-item breakdown and all
        three profit metrics:
            total_mrp             - sum(qty * mrp_per_unit)
            total_purchase_cost   - sum(qty * purchase_cost_with_gst_per_unit)
            consulting_charge     = total_amount_collected - total_mrp
                -> what's left of the patient charge after "paying for"
                   the medicines at their full MRP value. Positive means
                   the clinic charged MORE than the medicines' MRP (the
                   surplus is effectively the consultation/service fee);
                   negative means the bundled charge was BELOW the
                   medicines' combined MRP (a discount vs retail, common
                   in flat-fee clinics - see this module's docstring on
                   add_visit()'s total_collected override).
            actual_net_profit     = total_amount_collected - total_purchase_cost
                -> the REAL money made on this visit - what actually
                   lands in the till after covering the true medicine cost.
            medicine_margin_profit = total_mrp - total_purchase_cost
                -> the theoretical margin if every item had been billed
                   at its printed MRP, regardless of what was actually
                   collected. Useful to compare against actual_net_profit
                   to see whether real collections run above or below
                   standard MRP margins.

    Every amount is rounded via money.to_money()/money_sum() (Decimal-
    based, ROUND_HALF_UP) - the same rounding rule as every other
    financial calculation in this app, so results always match to the
    paisa with what billing.py/gst_reports.py would compute for the
    same inputs.
    """
    item_breakdown = []
    mrp_totals = []
    purchase_totals = []

    for item in medicines:
        qty = float(item["qty"])
        mrp_per_unit = to_money(item["mrp_per_unit"])
        purchase_per_unit = to_money(item["purchase_cost_with_gst_per_unit"])

        item_total_mrp = to_money(qty * mrp_per_unit)
        item_total_purchase_cost = to_money(qty * purchase_per_unit)

        mrp_totals.append(item_total_mrp)
        purchase_totals.append(item_total_purchase_cost)

        item_breakdown.append({
            "medicine_name": item["medicine_name"],
            "qty": qty,
            "mrp_per_unit": mrp_per_unit,
            "purchase_cost_with_gst_per_unit": purchase_per_unit,
            "item_total_mrp": item_total_mrp,
            "item_total_purchase_cost": item_total_purchase_cost,
        })

    total_amount_collected = to_money(total_amount_collected)
    total_mrp = money_sum(mrp_totals)
    total_purchase_cost = money_sum(purchase_totals)

    consulting_charge = to_money(total_amount_collected - total_mrp)
    actual_net_profit = to_money(total_amount_collected - total_purchase_cost)
    medicine_margin_profit = to_money(total_mrp - total_purchase_cost)

    return {
        "patient_charge": total_amount_collected,
        "total_mrp": total_mrp,
        "total_purchase_cost": total_purchase_cost,
        "consulting_charge": consulting_charge,
        "actual_net_profit": actual_net_profit,
        "medicine_margin_profit": medicine_margin_profit,
        "items": item_breakdown,
    }


# ============================================================
# PATIENTS
# ============================================================

def generate_patient_code(cur):
    """Next 'PT-000123'-style code. Reads the highest existing numeric
    suffix rather than COUNT(*), so it stays correct even if a patient
    row is ever removed by hand in the DB directly."""
    cur.execute("SELECT patient_code FROM clinic_patients ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    next_num = 1
    if row and row[0]:
        try:
            next_num = int(row[0].split("-")[-1]) + 1
        except (ValueError, IndexError):
            cur.execute("SELECT COUNT(*) FROM clinic_patients")
            next_num = (cur.fetchone()[0] or 0) + 1
    return f"PT-{next_num:06d}"


def search_patients(text, limit=30):
    """Search by name OR phone (dedup helper for 'search existing
    patient before creating a new one', per the workflow spec)."""
    con = _connect()
    cur = con.cursor()
    text = (text or "").strip()
    if text:
        cur.execute("""
            SELECT id, patient_code, name, age, gender, phone, address
            FROM clinic_patients
            WHERE name LIKE ? OR phone LIKE ?
            ORDER BY name LIMIT ?
        """, (f"%{text}%", f"%{text}%", limit))
    else:
        cur.execute("""
            SELECT id, patient_code, name, age, gender, phone, address
            FROM clinic_patients ORDER BY id DESC LIMIT ?
        """, (limit,))
    rows = cur.fetchall()
    con.close()
    return rows


def get_patient(patient_id):
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT id, patient_code, name, age, gender, phone, address, linked_customer_id
        FROM clinic_patients WHERE id=?
    """, (patient_id,))
    row = cur.fetchone()
    con.close()
    return row


def create_patient(name, age, gender, phone, address, linked_customer_id=None, created_by=None):
    if not (name or "").strip():
        raise ValueError("Patient name is required")
    con = _connect()
    cur = con.cursor()
    try:
        code = generate_patient_code(cur)
        cur.execute("""
            INSERT INTO clinic_patients(patient_code, name, age, gender, phone, address, linked_customer_id, created_by, created_at)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (code, name.strip(), age or None, gender, phone, address, linked_customer_id, created_by, _now()))
        patient_id = cur.lastrowid
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    audit_log.log_action("Clinic Ledger", "Create Patient", f"Created patient '{name}' (code={code})")
    return patient_id, code


def update_patient(patient_id, name, age, gender, phone, address):
    con = _connect()
    cur = con.cursor()
    try:
        cur.execute("""
            UPDATE clinic_patients SET name=?, age=?, gender=?, phone=?, address=?
            WHERE id=?
        """, (name, age or None, gender, phone, address, patient_id))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    audit_log.log_action("Clinic Ledger", "Update Patient", f"Updated patient id={patient_id}")


# ============================================================
# STOCK / COST LOOKUP (reuses medicine_master, no duplicate inventory)
# ============================================================

# The New Visit screen's "Add Item" category dropdown (Aug 2026 "A-la-
# carte" upgrade) - one clinic-facing category can map to MORE than one
# Medicine Master Dosage Form ("Medicine" covers both Tablet and
# Capsule), so this is a category -> list-of-dosage-forms mapping, not
# a 1:1 rename. Keep this in sync with medicine_master.DOSAGE_FORM_OPTIONS
# whenever a new Dosage Form is added there for clinic use.
CLINIC_ITEM_CATEGORIES = ["Medicine", "Syrup", "Injection", "Consumable", "IV Fluids"]

CLINIC_CATEGORY_TO_DOSAGE_FORMS = {
    "Medicine": ["Tablet", "Capsule"],
    "Syrup": ["Syrup"],
    # "Ampoule"/"Vial" added (Aug 2026 Stock filter round) alongside the
    # existing plain "Injection" value - see medicine_master.
    # DOSAGE_FORM_OPTIONS's own comment on why they were added, kept in
    # sync here per this file's own "keep this in sync" note above.
    # Medicines classified as an ampoule or a vial must keep showing up
    # under Clinic Ledger's "Injection" category exactly like a plain
    # "Injection" one always has.
    "Injection": ["Injection", "Ampoule", "Vial"],
    "Consumable": ["Consumable"],
    "IV Fluids": ["IV Fluid"],
}


def search_clinic_medicines(text, limit=20, category=None):
    """Same substring-search shape as billing_repository.search_medicine_names(),
    kept as its own function here only so Clinic Ledger screens don't need
    to import a billing-named module - the query itself is identical,
    plus an optional category filter (Aug 2026).

    `category` (optional): one of CLINIC_ITEM_CATEGORIES, e.g. "Syrup" -
    when given, restricts results to medicines whose Medicine Master
    Dosage Form matches (via CLINIC_CATEGORY_TO_DOSAGE_FORMS above) -
    OR whose Dosage Form is still blank/unset. That "OR blank" half is
    the deliberate safe-fallback: most real pharmacies have plenty of
    medicines nobody has ever gone back to classify by Dosage Form, and
    those must keep showing up in every category's search instead of
    silently vanishing the moment category filtering is turned on. Only
    a medicine that HAS a Dosage Form set is actually restricted to its
    matching categor{y/ies}. Pass category=None (default) for the old,
    unfiltered, show-everything behaviour.
    """
    con = _connect()
    cur = con.cursor()
    if category and category in CLINIC_CATEGORY_TO_DOSAGE_FORMS:
        forms = CLINIC_CATEGORY_TO_DOSAGE_FORMS[category]
        placeholders = ",".join("?" for _ in forms)
        cur.execute(f"""
            SELECT DISTINCT name FROM medicine_master
            WHERE name LIKE ?
              AND (dosage_form IS NULL OR TRIM(dosage_form) = '' OR dosage_form IN ({placeholders}))
            ORDER BY name LIMIT ?
        """, (f"%{text}%", *forms, limit))
    else:
        cur.execute(
            "SELECT DISTINCT name FROM medicine_master WHERE name LIKE ? ORDER BY name LIMIT ?",
            (f"%{text}%", limit)
        )
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return rows


def _fefo_batches_with_cost(cur, name):
    """All in-stock, non-expired batches of `name`, earliest-expiry
    first, each carrying BOTH its per-unit purchase cost (GST-inclusive,
    same convention as billing_repository.save_bill()) and per-unit MRP
    (MRP is already tax-inclusive by law, per money.py's own docstring -
    it is not multiplied by GST again)."""
    cur.execute("""
        SELECT batch, stock, purchase, mrp, sale, pack_size, gst, expiry
        FROM medicine_master WHERE name=? AND stock > 0
    """, (name,))
    today = datetime.today().replace(day=1)
    batches = []
    for batch, stock, purchase, mrp, sale, pack_size, gst, expiry in cur.fetchall():
        expiry_dt = None
        expired = False
        if expiry:
            try:
                expiry_dt = datetime.strptime(expiry, "%m/%y").replace(day=1)
                expired = expiry_dt < today
            except Exception:
                expiry_dt = None

        unit_purchase_base = get_unit_price(purchase, pack_size)
        gst_rate = float(gst or 0)
        unit_purchase_cost = to_money(unit_purchase_base * (1 + gst_rate / 100))
        unit_mrp = get_unit_price(mrp or sale, pack_size)

        batches.append({
            "batch": batch,
            "stock": stock,
            "unit_purchase_cost": unit_purchase_cost,
            "unit_mrp": unit_mrp,
            "pack_size": pack_size,
            "expiry_sort": expiry_dt.isoformat() if expiry_dt else None,
            "expired": expired,
        })
    batches.sort(key=lambda b: (b["expiry_sort"] is None, b["expiry_sort"] or ""))
    return batches


def allocate_clinic_stock(cur, name, qty_needed):
    """FEFO-consume `qty_needed` units of medicine `name` out of the real
    medicine_master stock, splitting across batches exactly like
    pricing_utils.allocate_fifo() does for Billing - the only difference
    is each allocation here carries two prices (purchase cost AND MRP)
    instead of allocate_fifo()'s one, since a visit needs both. Raises
    InsufficientStockError instead of silently under-filling, so a save
    never records more usage than what was actually deducted.
    """
    batches = _fefo_batches_with_cost(cur, name)
    usable = [b for b in batches if not b["expired"]]
    remaining = qty_needed
    allocations = []
    for b in usable:
        if remaining <= 0:
            break
        take = min(b["stock"], remaining)
        if take <= 0:
            continue
        allocations.append({
            "batch": b["batch"],
            "qty": take,
            "pack_size": b["pack_size"],
            "unit_purchase_cost": b["unit_purchase_cost"],
            "unit_mrp": b["unit_mrp"],
            "purchase_cost_total": to_money(b["unit_purchase_cost"] * take),
            "mrp_value_total": to_money(b["unit_mrp"] * take),
        })
        remaining -= take
    if remaining > 0:
        raise InsufficientStockError(
            f"Only {qty_needed - remaining} of {qty_needed} '{name}' available in stock (non-expired)."
        )
    return allocations


# ============================================================
# VISITS (the core "New Visit" save/cancel transaction)
# ============================================================

def generate_visit_no(cur):
    today = datetime.now().strftime("%Y%m%d")
    cur.execute("SELECT COUNT(*) FROM clinic_visits WHERE visit_no LIKE ?", (f"CL-{today}-%",))
    seq = (cur.fetchone()[0] or 0) + 1
    return f"CL-{today}-{seq:04d}"


def preview_item_cost(name, qty):
    """Read-only preview of what allocate_clinic_stock() would deduct/cost
    for `name`/`qty`, for the New Visit screen's live totals BEFORE Save
    is clicked. Opens its own connection and never writes - safe to call
    on every keystroke. Returns (purchase_cost_total, mrp_value_total) or
    raises InsufficientStockError exactly like the real save would."""
    con = _connect()
    cur = con.cursor()
    try:
        allocations = allocate_clinic_stock(cur, name, qty)
    finally:
        con.close()
    purchase_cost_total = money_sum(a["purchase_cost_total"] for a in allocations)
    mrp_value_total = money_sum(a["mrp_value_total"] for a in allocations)
    return purchase_cost_total, mrp_value_total


def add_visit(patient_id, doctor, reason, consultation_amount, items, created_by, total_collected=None,
              auto_generate_bill=False, patient_name=None):
    """
    Saves one full patient visit: header + line items + real stock
    deduction, all in ONE transaction (mirrors billing_repository.
    save_bill()'s insert-then-deduct-then-commit/rollback shape).

    `items` is a list of dicts the New Visit screen builds, one per
    medicine/injection/consumable the doctor used:
        {
            "item_type": "Medicine" | "Injection" | "Consumable",
            "name": "Paracetamol 500",
            "qty": 3,
            "medicine_id": 42,          # None for an ad-hoc consumable
            "manual_unit_cost": None,   # only used when medicine_id is None
            "manual_unit_mrp": None,    # only used when medicine_id is None
        }

    A stock-tracked item (medicine_id is not None) is FEFO-allocated via
    allocate_clinic_stock() and may expand into MORE than one
    clinic_visit_items row if it spans two batches - same one-row-per-
    batch shape as sales_items. An ad-hoc consumable (medicine_id is
    None - e.g. a loose item never entered in Medicine Master) is
    recorded as a single row from its manual cost/MRP and does NOT
    touch medicine_master.stock, since there is nothing to deduct.

    Server-side computed only - callers must never pass in pre-computed
    cost/profit numbers for a stock-tracked item; this function always
    recalculates from the live medicine_master row, so a stale UI value
    can never be saved as truth.

    `total_collected` (optional): the ACTUAL rupee amount the patient
    paid, if it differs from Consultation + sum(MRP Value) - many small
    clinics collect one bundled/negotiated amount per visit (e.g. a flat
    Rs.200 covering 2 injections + 4 tablets) rather than billing every
    item strictly at its Medicine Master MRP. When omitted (None), the
    old itemized behaviour applies: total_collected defaults to
    Consultation + total MRP Value. Either way, Actual Net Profit is
    always Total Collected - Medicine Purchase Cost (the user-confirmed
    formula - see clinic_visit.py's _refresh_totals() for the matching
    live-preview logic and range_summary() for the matching rollup
    definition, both of which must stay in sync with this).

    `auto_generate_bill` (optional, default False): the "All-in-One"
    save flow - when True, this SAME transaction also creates a
    Pharmacy Sales invoice (sales + sales_items rows) for the
    stock-tracked medicines/injections used in this visit, so a second
    manual Billing-screen entry is never needed. Two things this
    deliberately does NOT do, both by design (confirmed with the
    pharmacy owner - see CLINIC_LEDGER_MODULE notes):
      - Stock is deducted EXACTLY ONCE, in the loop below. The
        auto-generated sales_items rows reuse the SAME allocations
        already taken out of medicine_master here; inserting them a
        second time via billing_repository.save_bill() would double-
        deduct the same stock.
      - Ad-hoc/manual-cost items (medicine_id is None) are NEVER put on
        the auto-generated bill - sales_items has a DB trigger
        (trg_sales_items_medicine_exists_ins) that rejects any
        `medicine` value not present in medicine_master.name, so a
        loose consumable that was never entered in Medicine Master
        simply cannot be invoiced this way. Its cost/profit still
        counts fully in this visit's own Clinic Ledger totals - it is
        just excluded from the Pharmacy Sales side.
    The invoice's `total` is reconciled to the real money collected for
    those stock-tracked items specifically (total_collected minus
    whatever this visit's Consultation fee and ad-hoc items already
    account for, floored at 0), and the gap versus their combined MRP
    is written into the invoice's own `discount` column - the same
    column a normal counter-sale discount already uses. The row is
    tagged sales.source='Clinic' (see database.py's migration) so a
    future combined-revenue report can tell it apart from a normal
    counter sale and never double-count the same rupee against both
    the Clinic Dashboard and the Pharmacy Dashboard - GST reporting
    SHOULD include it (it is real taxable turnover that was previously
    going out the door unbilled), but a "total business revenue for
    today" figure must pick ONE of Clinic Dashboard's Total Collection
    or Pharmacy Sales Total, not both.

    `patient_name` is required when auto_generate_bill is True (used as
    the invoice's `customer` field) - ignored otherwise.

    Returns (visit_id, visit_no, bill_no). bill_no is None whenever
    auto_generate_bill is False, or True but nothing stock-tracked was
    actually billable (e.g. a consultation-only visit, or a visit made
    entirely of ad-hoc items) - there is nothing a Pharmacy invoice
    could legitimately represent in that case.
    """
    con = _connect()
    cur = con.cursor()
    try:
        visit_no = generate_visit_no(cur)
        now = _now()

        cur.execute("""
            INSERT INTO clinic_visits(
                visit_no, patient_id, visit_date, doctor, reason,
                consultation_amount, status, created_by, created_at, updated_by, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (visit_no, patient_id, now, doctor, reason,
              to_money(consultation_amount), "Active", created_by, now, created_by, now))
        visit_id = cur.lastrowid

        item_rows = []      # for total roll-up after all inserts (both types)
        billable_items = []  # stock-tracked items only - feeds the auto-bill below
        adhoc_mrp_total = 0.0
        for item in items:
            qty = float(item["qty"])
            if qty <= 0:
                continue

            if item.get("medicine_id"):
                allocations = allocate_clinic_stock(cur, item["name"], qty)
                for alloc in allocations:
                    gross_profit = to_money(alloc["mrp_value_total"] - alloc["purchase_cost_total"])
                    cur.execute("""
                        INSERT INTO clinic_visit_items(
                            visit_id, item_type, medicine_id, item_name, batch, pack_size, qty,
                            unit_purchase_cost, unit_mrp, purchase_cost_total, mrp_value_total,
                            gross_profit, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        visit_id, item["item_type"], item["medicine_id"], item["name"],
                        alloc["batch"], alloc["pack_size"], alloc["qty"],
                        alloc["unit_purchase_cost"], alloc["unit_mrp"],
                        alloc["purchase_cost_total"], alloc["mrp_value_total"], gross_profit, now
                    ))
                    cur.execute(
                        "UPDATE medicine_master SET stock = stock - ? WHERE name=? AND batch=?",
                        (alloc["qty"], item["name"], alloc["batch"])
                    )
                    item_rows.append((alloc["purchase_cost_total"], alloc["mrp_value_total"]))
                    billable_items.append({
                        "item_name": item["name"], "batch": alloc["batch"], "qty": alloc["qty"],
                        "unit_purchase_cost": alloc["unit_purchase_cost"], "unit_mrp": alloc["unit_mrp"],
                        "mrp_value_total": alloc["mrp_value_total"],
                    })
            else:
                # Ad-hoc, not-stock-tracked consumable (spec section on
                # consumables) - manual cost entry, no stock to deduct,
                # and (see docstring above) never eligible for the
                # auto-generated Pharmacy bill.
                unit_cost = to_money(item.get("manual_unit_cost") or 0)
                unit_mrp = to_money(item.get("manual_unit_mrp") or 0)
                purchase_cost_total = to_money(unit_cost * qty)
                mrp_value_total = to_money(unit_mrp * qty)
                gross_profit = to_money(mrp_value_total - purchase_cost_total)
                cur.execute("""
                    INSERT INTO clinic_visit_items(
                        visit_id, item_type, medicine_id, item_name, batch, pack_size, qty,
                        unit_purchase_cost, unit_mrp, purchase_cost_total, mrp_value_total,
                        gross_profit, created_at
                    ) VALUES(?,?,NULL,?,NULL,NULL,?,?,?,?,?,?,?)
                """, (
                    visit_id, item["item_type"], item["name"], qty,
                    unit_cost, unit_mrp, purchase_cost_total, mrp_value_total, gross_profit, now
                ))
                item_rows.append((purchase_cost_total, mrp_value_total))
                adhoc_mrp_total += mrp_value_total

        total_purchase_cost = money_sum(r[0] for r in item_rows)
        total_mrp_value = money_sum(r[1] for r in item_rows)
        auto_collection = to_money(to_money(consultation_amount) + total_mrp_value)
        # A flat/bundled amount the clinic actually collected overrides
        # the itemized MRP-based total - see this function's docstring.
        total_collection = to_money(total_collected) if total_collected is not None else auto_collection
        # Actual Net Profit = Total Collection - Medicine Purchase Cost.
        # This MUST match range_summary()'s Revenue-minus-Direct-Cost
        # definition below and clinic_visit.py's live preview, since
        # consultation income has no cost of its own (100% of it is
        # profit) and a bundled collected amount already stands in for
        # both consultation AND medicine income combined.
        total_gross_profit = to_money(total_collection - total_purchase_cost)

        cur.execute("""
            UPDATE clinic_visits SET
                total_purchase_cost=?, total_mrp_value=?, total_gross_profit=?, total_collection=?
            WHERE id=?
        """, (total_purchase_cost, total_mrp_value, total_gross_profit, total_collection, visit_id))

        # ---- All-in-One auto-bill (see docstring above for the full
        # design/reasoning) ----
        bill_no = None
        if auto_generate_bill and billable_items:
            billable_mrp = money_sum(b["mrp_value_total"] for b in billable_items)
            if billable_mrp > 0:
                # What the patient actually paid FOR THESE stock-tracked
                # items specifically - strip out consultation and any
                # ad-hoc item value (neither can appear on this invoice),
                # floored at 0 so a visit where those two already exceed
                # the total collected never produces a negative invoice.
                billable_collected = to_money(total_collection - to_money(consultation_amount) - adhoc_mrp_total)
                if billable_collected < 0:
                    billable_collected = 0.0
                discount = to_money(billable_mrp - billable_collected)

                cur.execute("SELECT MAX(id) FROM sales")
                next_sales_id = (cur.fetchone()[0] or 0) + 1
                bill_no = f"BILL-{datetime.now().strftime('%Y%m%d')}-{next_sales_id:04d}"

                cur.execute("""
                    INSERT INTO sales(bill_no, bill_date, customer, doctor, subtotal, discount, total,
                                       payment_mode, received_amt, balance_amt, address, source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    bill_no, now, patient_name or "Clinic Patient", doctor,
                    billable_mrp, discount, billable_collected,
                    "Cash", billable_collected, 0, "", "Clinic"
                ))
                for b in billable_items:
                    cur.execute("""
                        INSERT INTO sales_items(bill_no, medicine, batch, qty, purchase, sale, total)
                        VALUES (?,?,?,?,?,?,?)
                    """, (
                        bill_no, b["item_name"], b["batch"], b["qty"],
                        b["unit_purchase_cost"], b["unit_mrp"], b["mrp_value_total"]
                    ))
                # Link the visit to its auto-bill (database.py's
                # clinic_visits.bill_no column) so cancel_visit() below
                # can flag it for manual review instead of silently
                # leaving an orphaned "valid" sale on record.
                cur.execute("UPDATE clinic_visits SET bill_no=? WHERE id=?", (bill_no, visit_id))

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    audit_log.log_action(
        "Clinic Ledger", "Save Visit",
        f"Visit {visit_no} (patient_id={patient_id}) - collection={total_collection}, "
        f"actual_net_profit={total_gross_profit}"
        + (f", auto-bill={bill_no}" if bill_no else "")
    )
    return visit_id, visit_no, bill_no


def cancel_visit(visit_id, reason, cancelled_by):
    """Non-destructive cancel: reverses every line item's stock deduction
    (same `stock = stock + ?` convention sales_return.py already uses)
    and flips status - the row and its full history stay in the table
    forever, per the workflow spec's 'no hard delete' rule.

    NOTE on visits saved with auto_generate_bill=True: stock IS correctly
    restored here (it was only ever deducted once, in add_visit()'s own
    loop - the auto-bill never deducts a second time, see that
    function's docstring), but the Pharmacy Sales invoice itself
    (clinic_visits.bill_no) is intentionally left untouched - this
    codebase has no cancellation/void concept for a `sales` row (only
    sales_return.py's separate reversing-transaction pattern), and
    silently rewriting a financial record would break the same
    no-hard-delete principle this function itself follows. Instead this
    logs a clear audit-trail flag so the pharmacist knows to review/
    return that bill by hand in Billing if the visit being cancelled
    means those medicines are coming back."""
    con = _connect()
    cur = con.cursor()
    try:
        cur.execute("SELECT status, bill_no FROM clinic_visits WHERE id=?", (visit_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Visit id={visit_id} not found")
        if row[0] == "Cancelled":
            raise ValueError("This visit is already cancelled.")
        linked_bill_no = row[1]

        cur.execute("""
            SELECT item_name, batch, qty FROM clinic_visit_items
            WHERE visit_id=? AND medicine_id IS NOT NULL
        """, (visit_id,))
        for item_name, batch, qty in cur.fetchall():
            cur.execute(
                "UPDATE medicine_master SET stock = stock + ? WHERE name=? AND batch=?",
                (qty, item_name, batch)
            )

        now = _now()
        cur.execute("""
            UPDATE clinic_visits SET status='Cancelled', cancel_reason=?, cancelled_by=?, cancelled_at=?
            WHERE id=?
        """, (reason, cancelled_by, now, visit_id))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    audit_log.log_action("Clinic Ledger", "Cancel Visit", f"Cancelled visit id={visit_id}: {reason}")
    if linked_bill_no:
        audit_log.log_action(
            "Clinic Ledger", "Cancel Visit - Review Linked Bill",
            f"Visit id={visit_id} had auto-generated Pharmacy Bill {linked_bill_no} - "
            f"stock was restored, but the bill itself was NOT auto-voided. "
            f"Please review/return it manually in Billing if needed."
        )


def get_visit(visit_id):
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT v.id, v.visit_no, v.patient_id, p.name, v.visit_date, v.doctor, v.reason,
               v.consultation_amount, v.total_purchase_cost, v.total_mrp_value,
               v.total_gross_profit, v.total_collection, v.status, v.cancel_reason,
               v.bill_no
        FROM clinic_visits v JOIN clinic_patients p ON p.id = v.patient_id
        WHERE v.id=?
    """, (visit_id,))
    header = cur.fetchone()
    cur.execute("""
        SELECT item_type, item_name, batch, qty, unit_purchase_cost, unit_mrp,
               purchase_cost_total, mrp_value_total, gross_profit
        FROM clinic_visit_items WHERE visit_id=?
    """, (visit_id,))
    items = cur.fetchall()
    con.close()
    return header, items


class AlreadyBilledError(Exception):
    """Raised when generate_bill_for_visit() is asked to bill a visit
    that already has a linked bill_no - refusing this is what prevents
    the same medicines being invoiced (and their revenue/GST counted)
    twice over."""
    pass


def generate_bill_for_visit(visit_id, patient_name, item_types=None, created_by=None):
    """
    The AFTER-THE-FACT counterpart to add_visit()'s auto_generate_bill=
    True flow: bills an ALREADY-SAVED visit's medicines separately,
    instead of at Save time. Use this when the doctor wants to finish
    and save the visit fast during the consultation, then decide the
    billing details afterward (e.g. a Patient History / "Pending Bills"
    screen with a "Create Bill" button - no code work needed on your
    side to wire that up beyond calling this function with the visit_id
    the user picked).

    No new stock deduction happens here - stock was already deducted
    once, at add_visit() Save time, regardless of whether or when it
    gets billed. This function only reads that visit's ALREADY-RECORDED
    clinic_visit_items rows and turns a subset of them into a Sales
    invoice, exactly like add_visit()'s own auto-bill step does.

    `item_types` (optional): restrict the bill to specific item types,
    e.g. item_types=["Medicine"] to bill ONLY the take-home medicine
    lines and leave injections/consumables off the Pharmacy invoice
    entirely (still fully recorded in this visit's own Clinic Ledger
    totals either way - this only controls what shows up in Sales).
    None (default) bills every stock-tracked item in the visit, same as
    add_visit()'s own auto-bill. Ad-hoc/manual-cost items (medicine_id
    is NULL) are ALWAYS excluded regardless of this filter - same
    sales_items FK-trigger reason documented on add_visit().

    Billing only some item types is safe by design: the invoice's
    `total` is reconciled to only the MRP of the items actually being
    billed (money attributable to consultation, ad-hoc items, and any
    item type you leave OUT is simply never touched here - it stays
    exactly as already recorded in the Clinic Ledger, and never appears
    twice). The only real risk this function guards against is billing
    the SAME visit more than once - it raises AlreadyBilledError if
    clinic_visits.bill_no is already set, since a second bill for
    medicines already invoiced once WOULD double the revenue/GST/stock-
    on-paper picture (the physical stock itself is only ever deducted
    the one time, at Save).

    Returns bill_no, or None if nothing in `item_types` was actually
    stock-tracked/billable for this visit (nothing is created in that
    case - there is nothing a Pharmacy invoice could legitimately
    represent).
    """
    con = _connect()
    cur = con.cursor()
    try:
        cur.execute("""
            SELECT status, bill_no, doctor, consultation_amount, total_collection
            FROM clinic_visits WHERE id=?
        """, (visit_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Visit id={visit_id} not found")
        status, existing_bill_no, doctor, consultation_amount, total_collection = row
        if status == "Cancelled":
            raise ValueError("Cannot bill a cancelled visit.")
        if existing_bill_no:
            raise AlreadyBilledError(
                f"Visit {visit_id} already has Pharmacy Bill {existing_bill_no} - "
                f"billing it again would double-count that revenue/GST."
            )

        cur.execute("""
            SELECT item_type, medicine_id, item_name, batch, qty,
                   unit_purchase_cost, unit_mrp, mrp_value_total
            FROM clinic_visit_items WHERE visit_id=?
        """, (visit_id,))
        all_items = cur.fetchall()

        billable_items = []   # matches item_types filter (or all, if no filter) AND is stock-tracked
        excluded_mrp = to_money(consultation_amount)  # money this bill can NEVER represent
        for item_type, medicine_id, item_name, batch, qty, unit_purchase_cost, unit_mrp, mrp_value_total in all_items:
            is_stock_tracked = medicine_id is not None
            matches_filter = item_types is None or item_type in item_types
            if is_stock_tracked and matches_filter:
                billable_items.append({
                    "item_name": item_name, "batch": batch, "qty": qty,
                    "unit_purchase_cost": unit_purchase_cost, "unit_mrp": unit_mrp,
                    "mrp_value_total": mrp_value_total,
                })
            else:
                # Ad-hoc items, and any stock-tracked item type the
                # caller chose to leave off this bill, both fall here -
                # their value must never appear on this invoice.
                excluded_mrp += to_money(mrp_value_total)

        if not billable_items:
            con.close()
            return None

        billable_mrp = money_sum(b["mrp_value_total"] for b in billable_items)
        billable_collected = to_money(total_collection - excluded_mrp)
        if billable_collected < 0:
            billable_collected = 0.0
        discount = to_money(billable_mrp - billable_collected)

        cur.execute("SELECT MAX(id) FROM sales")
        next_sales_id = (cur.fetchone()[0] or 0) + 1
        bill_no = f"BILL-{datetime.now().strftime('%Y%m%d')}-{next_sales_id:04d}"
        now = _now()

        cur.execute("""
            INSERT INTO sales(bill_no, bill_date, customer, doctor, subtotal, discount, total,
                               payment_mode, received_amt, balance_amt, address, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            bill_no, now, patient_name or "Clinic Patient", doctor,
            billable_mrp, discount, billable_collected,
            "Cash", billable_collected, 0, "", "Clinic"
        ))
        for b in billable_items:
            cur.execute("""
                INSERT INTO sales_items(bill_no, medicine, batch, qty, purchase, sale, total)
                VALUES (?,?,?,?,?,?,?)
            """, (
                bill_no, b["item_name"], b["batch"], b["qty"],
                b["unit_purchase_cost"], b["unit_mrp"], b["mrp_value_total"]
            ))
        cur.execute("UPDATE clinic_visits SET bill_no=? WHERE id=?", (bill_no, visit_id))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    audit_log.log_action(
        "Clinic Ledger", "Generate Bill For Visit",
        f"Visit id={visit_id} billed after-the-fact as {bill_no}"
        + (f" (item_types={item_types})" if item_types else " (all stock-tracked items)")
        + f" - by {created_by}"
    )
    return bill_no


def list_visits_for_patient(patient_id):
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT id, visit_no, visit_date, doctor, consultation_amount, total_collection, total_gross_profit, status
        FROM clinic_visits WHERE patient_id=? ORDER BY visit_date DESC
    """, (patient_id,))
    rows = cur.fetchall()
    con.close()
    return rows


# ============================================================
# EXPENSES (reuses the existing, previously-unused `expenses` table)
# ============================================================

def add_clinic_expense(expense_date, category, description, amount, payment_mode, created_by=None):
    con = _connect()
    cur = con.cursor()
    try:
        cur.execute("""
            INSERT INTO expenses(expense_date, category, description, amount, payment_mode, module)
            VALUES(?,?,?,?,?,'Clinic')
        """, (expense_date, category, description, to_money(amount), payment_mode or "Cash"))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    audit_log.log_action("Clinic Ledger", "Add Expense", f"{category}: {to_money(amount)} ({expense_date})")


def list_clinic_expenses(date_from, date_to):
    """date_from/date_to may be plain 'YYYY-MM-DD' (as typed on the
    Clinic Expenses screen) OR 'YYYY-MM-DD HH:MM:SS' (as range_summary()
    passes for daily/monthly/yearly rollups) - expense_date itself is
    always stored plain-date (see add_clinic_expense()). Comparing via
    SQLite's date() on both sides normalizes either shape instead of a
    raw string BETWEEN, which would otherwise silently exclude every
    same-day expense (a plain 'YYYY-MM-DD' sorts BEFORE its own
    'YYYY-MM-DD 00:00:00' timestamp in a lexicographic comparison)."""
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT id, expense_date, category, description, amount, payment_mode
        FROM expenses WHERE module='Clinic' AND date(expense_date) BETWEEN date(?) AND date(?)
        ORDER BY expense_date DESC
    """, (date_from, date_to))
    rows = cur.fetchall()
    con.close()
    return rows


def total_clinic_expenses(date_from, date_to):
    """See list_clinic_expenses()'s docstring for why date(...) wraps
    both sides of this comparison."""
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT COALESCE(SUM(amount), 0) FROM expenses
        WHERE module='Clinic' AND date(expense_date) BETWEEN date(?) AND date(?)
    """, (date_from, date_to))
    total = cur.fetchone()[0]
    con.close()
    return to_money(total)


# ============================================================
# REPORTS (13 reports required by CLINIC_LEDGER_WORKFLOW.md, section on
# reports) - every date filter is on clinic_visits.visit_date, formatted
# "YYYY-MM-DD HH:MM:SS" same as every other date column in this app, so
# BETWEEN 'YYYY-MM-DD 00:00:00' AND 'YYYY-MM-DD 23:59:59' style ranges
# work directly. Only status='Active' visits count towards money totals -
# a Cancelled visit's reversed stock/profit must never double up in a
# report just because the row itself is still kept (see cancel_visit()).
# ============================================================

def range_summary(date_from, date_to):
    """Core numbers behind the Daily/Monthly/Yearly Clinic Report and the
    Clinic Dashboard cards:
        Revenue            = Total Collection actually received (the real
                              clinic_visits.total_collection column - NOT
                              recomputed as consultation+MRP, since many
                              visits collect one flat/bundled amount that
                              does not equal itemized MRP - see
                              add_visit()'s total_collected parameter)
        Direct Cost        = medicine/injection/consumable purchase cost
        Consulting Charge  = Revenue - Medicine MRP Value
        Actual Net Profit  = Revenue - Direct Cost
        Medicine Margin Profit = Medicine MRP Value - Direct Cost
        Operating Expense  = clinic_ledger expenses in the same range
        Net Profit         = Actual Net Profit - Operating Expense
    consultation_income/medicine_mrp_value are still returned separately
    below, for display only - they are informational (what an itemized
    MRP bill WOULD have been), not what Revenue/Actual Net Profit are
    based on.
    """
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT COUNT(*), COUNT(DISTINCT patient_id),
               COALESCE(SUM(consultation_amount), 0), COALESCE(SUM(total_mrp_value), 0),
               COALESCE(SUM(total_purchase_cost), 0), COALESCE(SUM(total_collection), 0)
        FROM clinic_visits
        WHERE status='Active' AND visit_date BETWEEN ? AND ?
    """, (date_from, date_to))
    visits, unique_patients, consultation, mrp_value, purchase_cost, collection = cur.fetchone()
    con.close()

    consultation = to_money(consultation)
    mrp_value = to_money(mrp_value)
    purchase_cost = to_money(purchase_cost)
    revenue = to_money(collection)
    # Three distinct profit figures, all returned - matches the official
    # naming from compute_profit_breakdown() above and clinic_visit.py's
    # create_variables() docstring, just rolled up over a date range
    # instead of a single visit:
    #   consulting_charge       = Revenue - Medicine MRP Value
    #   actual_net_profit       = real money made = Revenue - Purchase Cost
    #   medicine_margin_profit  = what margin WOULD be if every item were
    #                             billed at its printed MRP
    consulting_charge = to_money(revenue - mrp_value)
    actual_net_profit = to_money(revenue - purchase_cost)
    medicine_margin_profit = to_money(mrp_value - purchase_cost)
    expenses = total_clinic_expenses(date_from, date_to)
    net_profit = to_money(actual_net_profit - expenses)

    return {
        "visits": visits or 0,
        "unique_patients": unique_patients or 0,
        "consultation_income": consultation,
        "medicine_mrp_value": mrp_value,
        "medicine_purchase_cost": purchase_cost,
        "total_collection": to_money(collection),
        "revenue": revenue,
        "consulting_charge": consulting_charge,
        "actual_net_profit": actual_net_profit,
        "medicine_margin_profit": medicine_margin_profit,
        "expenses": expenses,
        "net_profit": net_profit,
    }


def daily_report(date):
    """1. Daily Clinic Report - date is a plain 'YYYY-MM-DD' string."""
    return range_summary(f"{date} 00:00:00", f"{date} 23:59:59")


def monthly_report(year, month):
    """2. Monthly Clinic Report - adds average-per-patient and the
    highest/lowest single-day collection within the month, per spec."""
    date_from = f"{year:04d}-{month:02d}-01 00:00:00"
    # Simpler/safer than a manual days-in-month table: ask SQLite itself.
    con = _connect()
    cur = con.cursor()
    cur.execute("SELECT date(?, '+1 month', '-1 day')", (f"{year:04d}-{month:02d}-01",))
    last_day_str = cur.fetchone()[0]
    date_to = f"{last_day_str} 23:59:59"

    summary = range_summary(date_from, date_to)
    summary["avg_collection_per_patient"] = (
        to_money(summary["total_collection"] / summary["visits"]) if summary["visits"] else 0.0
    )
    summary["avg_profit_per_patient"] = (
        to_money(summary["actual_net_profit"] / summary["visits"]) if summary["visits"] else 0.0
    )

    cur.execute("""
        SELECT date(visit_date) d, SUM(total_collection) c
        FROM clinic_visits WHERE status='Active' AND visit_date BETWEEN ? AND ?
        GROUP BY d ORDER BY c DESC
    """, (date_from, date_to))
    day_rows = cur.fetchall()
    con.close()
    summary["highest_day"] = day_rows[0] if day_rows else None
    summary["lowest_day"] = day_rows[-1] if day_rows else None
    return summary


def yearly_report(year):
    """3. Yearly Clinic Report - whole-year totals plus a Jan-Dec
    month-by-month comparison list."""
    date_from = f"{year:04d}-01-01 00:00:00"
    date_to = f"{year:04d}-12-31 23:59:59"
    summary = range_summary(date_from, date_to)
    summary["monthly_breakdown"] = [monthly_report(year, m) for m in range(1, 13)]
    return summary


def patient_visit_report(date_from, date_to):
    """4. Patient Visit Report."""
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT v.visit_no, p.name, v.doctor, v.visit_date, v.consultation_amount,
               v.total_collection, v.total_gross_profit, v.status
        FROM clinic_visits v JOIN clinic_patients p ON p.id = v.patient_id
        WHERE v.visit_date BETWEEN ? AND ?
        ORDER BY v.visit_date DESC
    """, (date_from, date_to))
    rows = cur.fetchall()
    con.close()
    return rows


def _usage_report(date_from, date_to, item_type):
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT ci.item_name, SUM(ci.qty), SUM(ci.purchase_cost_total),
               SUM(ci.mrp_value_total), SUM(ci.gross_profit)
        FROM clinic_visit_items ci JOIN clinic_visits v ON v.id = ci.visit_id
        WHERE v.status='Active' AND v.visit_date BETWEEN ? AND ? AND ci.item_type=?
        GROUP BY ci.item_name ORDER BY SUM(ci.qty) DESC
    """, (date_from, date_to, item_type))
    rows = cur.fetchall()
    con.close()
    return rows


def medicine_usage_report(date_from, date_to):
    """5. Medicine Usage Report."""
    return _usage_report(date_from, date_to, "Medicine")


def injection_usage_report(date_from, date_to):
    """6. Injection Usage Report."""
    return _usage_report(date_from, date_to, "Injection")


def medicine_cost_report(date_from, date_to):
    """7. Medicine Cost Report - purchase cost across ALL item types
    (medicine + injection + consumable), grouped by item name."""
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT ci.item_name, ci.item_type, SUM(ci.qty), SUM(ci.purchase_cost_total)
        FROM clinic_visit_items ci JOIN clinic_visits v ON v.id = ci.visit_id
        WHERE v.status='Active' AND v.visit_date BETWEEN ? AND ?
        GROUP BY ci.item_name, ci.item_type ORDER BY SUM(ci.purchase_cost_total) DESC
    """, (date_from, date_to))
    rows = cur.fetchall()
    con.close()
    return rows


def gross_profit_report(date_from, date_to):
    """8. Profit Breakdown Report - per-visit breakdown. Shows the actual
    Total Collection (what was really charged - may be a bundled flat
    amount, not necessarily consultation+MRP) alongside Purchase Cost and
    all three official profit metrics (see compute_profit_breakdown()'s
    docstring for the full definition of each):
        Actual Net Profit      (row[7], already stored as
                                 v.total_gross_profit - the column name
                                 predates this naming pass but the
                                 formula is unchanged: Total Collection -
                                 Purchase Cost)
        Consulting Charge      = Total Collection - MRP Value   (appended)
        Medicine Margin Profit = MRP Value - Purchase Cost      (appended)
    These can all differ from each other whenever the collected amount
    isn't itemized MRP billing (see add_visit()'s total_collected
    docstring)."""
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT v.visit_no, v.visit_date, p.name, v.consultation_amount,
               v.total_mrp_value, v.total_collection, v.total_purchase_cost, v.total_gross_profit
        FROM clinic_visits v JOIN clinic_patients p ON p.id = v.patient_id
        WHERE v.status='Active' AND v.visit_date BETWEEN ? AND ?
        ORDER BY v.visit_date DESC
    """, (date_from, date_to))
    rows = cur.fetchall()
    con.close()
    result = []
    for row in rows:
        mrp_value = row[4] or 0
        total_collection = row[5] or 0
        purchase_cost = row[6] or 0
        consulting_charge = to_money(total_collection - mrp_value)
        medicine_margin_profit = to_money(mrp_value - purchase_cost)
        result.append(row + (consulting_charge, medicine_margin_profit))
    return result


def net_profit_report(date_from, date_to):
    """9. Net Profit Report - day-by-day Gross Profit minus that day's
    Clinic expenses. Revenue is the real total_collection column (see
    range_summary()'s docstring on why - a bundled/flat collected amount
    does not always equal consultation+MRP)."""
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT date(visit_date) d, SUM(total_collection), SUM(total_purchase_cost)
        FROM clinic_visits WHERE status='Active' AND visit_date BETWEEN ? AND ?
        GROUP BY d ORDER BY d
    """, (date_from, date_to))
    day_rows = cur.fetchall()
    con.close()

    result = []
    for d, collection, purchase_cost in day_rows:
        revenue = to_money(collection or 0)
        gross_profit = to_money(revenue - (purchase_cost or 0))
        day_expenses = total_clinic_expenses(f"{d} 00:00:00", f"{d} 23:59:59")
        net_profit = to_money(gross_profit - day_expenses)
        result.append((d, revenue, gross_profit, day_expenses, net_profit))
    return result


def expense_report(date_from, date_to):
    """10. Expense Report - list plus category-wise totals."""
    rows = list_clinic_expenses(date_from, date_to)
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT category, SUM(amount) FROM expenses
        WHERE module='Clinic' AND date(expense_date) BETWEEN date(?) AND date(?)
        GROUP BY category ORDER BY SUM(amount) DESC
    """, (date_from, date_to))
    by_category = cur.fetchall()
    con.close()
    return rows, by_category


def doctor_report(date_from, date_to):
    """11. Doctor/Staff-wise Report."""
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT COALESCE(doctor, '(Not Set)'), COUNT(*), SUM(consultation_amount),
               SUM(total_gross_profit), SUM(total_collection)
        FROM clinic_visits WHERE status='Active' AND visit_date BETWEEN ? AND ?
        GROUP BY doctor ORDER BY SUM(total_collection) DESC
    """, (date_from, date_to))
    rows = cur.fetchall()
    con.close()
    return rows


def patient_history_report(patient_id):
    """12. Patient-wise Treatment History - reuses list_visits_for_patient()."""
    return list_visits_for_patient(patient_id)


def stock_used_report(date_from, date_to):
    """13. Stock Used in Clinic Report - qty consumed per medicine+batch,
    for reconciling against Stock Summary / physical counts."""
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT ci.item_name, ci.batch, ci.item_type, SUM(ci.qty)
        FROM clinic_visit_items ci JOIN clinic_visits v ON v.id = ci.visit_id
        WHERE v.status='Active' AND v.visit_date BETWEEN ? AND ? AND ci.medicine_id IS NOT NULL
        GROUP BY ci.item_name, ci.batch, ci.item_type
        ORDER BY ci.item_name
    """, (date_from, date_to))
    rows = cur.fetchall()
    con.close()
    return rows
