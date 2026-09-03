"""
stock_repository.py
Data-access layer for the Stock Management screen - third file in the
Aug 2026 repository-layer pass (see customer_repository.py's docstring
for the full rationale; same pattern).

Unlike customer_repository.py/supplier_repository.py, this module owns
NO table of its own and has no ensure_schema() - Stock is a read-only
view over medicine_master, whose table/columns are created and migrated
by database.py and written to by Medicine Master/Purchase/Billing. This
file only centralizes the SELECT queries stock.py needs, so they can be
tested without a live Tkinter screen and have one seam to redirect
later if stock data ever needs to come from somewhere other than the
local medicine_master table.
"""

import sqlite3
from datetime import datetime
from app_paths import DB_NAME
from pricing_utils import get_pack_multiplier

_LIST_COLUMNS = "id, name, company, batch, expiry, rack, purchase, sale, mrp, stock, pack_size, gst"

# Every date format actually written by the transaction screens that
# move stock (see pharmacy_erp_advanced_features.md "Round 8"/Stock
# Summary notes) - each table picked its own format independently over
# time, so a single BETWEEN-style SQL filter can't be used across all
# of them. Tried in order; the first one that parses wins. A stock_
# adjustments.adj_date is a plain Entry field a user can type anything
# into, so it gets the widest net.
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y")


def _parse_any_date(text):
    text = (text or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def list_medicines():
    """Every medicine_master row WITH STOCK (stock > 0), in the exact
    column order stock.py's load_stock() already expected (id, name,
    company, batch, expiry, rack, purchase, sale, mrp, stock, pack_size,
    gst), ordered by name.

    stock > 0 filter added Aug 2026 (Master Catalog feature): Medicine
    Master can now be pre-loaded with thousands of real medicine names
    as catalog-only placeholder rows (stock=0, batch='DUMMY' - see
    seed_dummy_medicines.py) so they're selectable from Purchase Entry's
    dropdown before ever being bought. Stock Management's whole purpose
    is showing what's physically on the shelf right now, so those
    catalog placeholders must stay invisible here - they only appear
    once a real purchase replaces the dummy batch with real stock (see
    purchase_repository.py's save_purchase() "DUMMY BATCH REPLACEMENT").
    Medicine Master itself is untouched by this - it still lists every
    row, catalog placeholders included, since IT is the dictionary."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute(f"SELECT {_LIST_COLUMNS} FROM medicine_master WHERE stock > 0 ORDER BY name")
    rows = cur.fetchall()
    con.close()
    return rows


def search_medicines(text):
    """Same shape as list_medicines(), filtered to names containing
    `text` - identical query to search_stock()'s original inline SQL.
    stock > 0 for the same reason as list_medicines() above."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute(
        f"SELECT {_LIST_COLUMNS} FROM medicine_master WHERE stock > 0 AND name LIKE ? ORDER BY name",
        ("%" + text.strip() + "%",)
    )
    rows = cur.fetchall()
    con.close()
    return rows


# ─── Category / Dosage Form filter (Aug 2026 Stock filter round) ───────
# list_medicines()/search_medicines() above are left exactly as they
# were - nothing else calls them, but there's no reason to disturb
# working code just to fold in two new optional filters when a single
# new function below does the same job for stock.py's actual needs
# (name search + Category dropdown + Dosage Form dropdown, any
# combination of which may be "All"/blank at once).
def list_medicines_filtered(search_text="", category="All", dosage_form="All"):
    """Same row shape as list_medicines()/search_medicines() (see
    _LIST_COLUMNS), narrowed by whichever of the three filters is
    actually active. `category`/`dosage_form` of "All" (or blank) means
    "don't filter on this" - exactly like leaving `search_text` blank
    already meant "match every medicine" before this function existed.

    Deliberately an exact `=` match on category/dosage_form, NOT the
    "OR blank" fallback clinic_repository.search_clinic_medicines() uses
    for its item picker - there, hiding an unclassified medicine would
    make it permanently unfindable in that screen, which is worse than
    the alternative. Here the whole point of picking "IV Fluids" is to
    see ONLY IV Fluids; "All" already covers "show everything, including
    anything nobody has categorised yet".
    """
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()

    # stock > 0 is always-on, not one of the optional filters below - see
    # list_medicines()'s docstring (Master Catalog feature, Aug 2026):
    # Stock Management only ever shows what's physically on the shelf.
    clauses = ["stock > 0"]
    params = []

    search_text = (search_text or "").strip()
    if search_text:
        clauses.append("name LIKE ?")
        params.append(f"%{search_text}%")

    if category and category != "All":
        clauses.append("category = ?")
        params.append(category)

    if dosage_form and dosage_form != "All":
        clauses.append("dosage_form = ?")
        params.append(dosage_form)

    where = f"WHERE {' AND '.join(clauses)}"
    cur.execute(f"SELECT {_LIST_COLUMNS} FROM medicine_master {where} ORDER BY name", params)
    rows = cur.fetchall()
    con.close()
    return rows


def list_distinct_categories():
    """Every non-blank Category value actually present in medicine_master
    right now, for stock.py to union with medicine_master.CATEGORY_OPTIONS
    when building the Category filter dropdown - so a category typed
    into the database some other way (or a future option this file
    doesn't know about yet) still shows up as a working filter choice
    instead of silently having no way to select it."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute(
        "SELECT DISTINCT category FROM medicine_master "
        "WHERE category IS NOT NULL AND TRIM(category) <> '' ORDER BY category"
    )
    values = [row[0] for row in cur.fetchall()]
    con.close()
    return values


def list_distinct_dosage_forms():
    """Same idea as list_distinct_categories(), for Dosage Form."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute(
        "SELECT DISTINCT dosage_form FROM medicine_master "
        "WHERE dosage_form IS NOT NULL AND TRIM(dosage_form) <> '' ORDER BY dosage_form"
    )
    values = [row[0] for row in cur.fetchall()]
    con.close()
    return values


def get_medicine_summary(name):
    """Returns (generic, gst, purchase, stock, expiry, pack_size) for
    the first medicine_master row matching `name`, or None - feeds the
    Selected Medicine Info panel (on_row_select()). Matches by name
    only (not batch) same as the original query - if multiple batches
    share a name, this is whichever one the table returns first, same
    behavior as before this extraction."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute(
        "SELECT generic, gst, purchase, stock, expiry, pack_size FROM medicine_master WHERE name=? LIMIT 1",
        (name,)
    )
    row = cur.fetchone()
    con.close()
    return row


def get_generic(name):
    """Returns the generic/composition text for `name` (empty string if
    none on file), or None if no such medicine exists at all - feeds
    view_substitutes()'s composition lookup."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT generic FROM medicine_master WHERE name=?", (name,))
    row = cur.fetchone()
    con.close()
    return row


def get_current_stock_by_name():
    """{medicine name -> total stock right now, summed across every
    batch row} - feeds Stock Summary's Closing-stock reconciliation
    (2026-08-22). medicine_master keeps one row per batch (see
    database.py's own comment on why name isn't UNIQUE), so a name's
    real on-hand quantity is the SUM of its rows, not any single one."""
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT name, COALESCE(SUM(stock), 0) FROM medicine_master GROUP BY name")
    result = dict(cur.fetchall())
    con.close()
    return result


def get_all_stock_movements():
    """
    Every event that has ever changed a medicine's stock count, as a
    flat list of dicts: {"medicine", "batch", "date" (a date object or
    None if unparseable), "qty" (signed - positive adds to stock,
    negative removes), "value" (positive money amount, direction
    already implied by qty's sign)}.

    Feeds Stock Summary's Opening/Inward/Outward/Closing report
    (2026-08-22) - there is no dedicated "opening stock" or stock-
    ledger table anywhere in this schema (see database.py), so a
    stock level as of any given date has to be reconstructed by
    walking every row that has ever touched medicine_master.stock.
    The five UPDATE statements that actually change .stock are the
    source of truth for sign/columns here - each one was re-read
    directly before writing this function, not assumed:
      - purchase_repository.py::save_purchase()   stock += qty
      - billing_repository.py::save_bill()        stock -= qty
      - purchase_return.py::save_return()         stock -= qty
      - sales_return.py::save_return()            stock += qty
      - stock_adjustment.py::save_adjustment()    stock += qty_change (already signed)

    Value is estimated the same way every other screen in this app
    already estimates "Stock Value" (stock.py/medicine_master.py/
    reports.py's slow_moving_report(), all using the identical
    `(purchase + purchase*gst/100) / pack_multiplier` landed-cost
    formula) - using each movement's OWN (medicine, batch)'s current
    purchase/gst/pack_size on file, not a stored historical cost. If a
    batch has since been fully consumed and its medicine_master row
    removed, its movements value as 0.00 (qty/count are still exact -
    only the money estimate is affected, and only for batches that no
    longer exist at all).
    """
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()

    cost_lookup = {}
    cur.execute("SELECT name, batch, purchase, gst, pack_size FROM medicine_master")
    for name, batch, purchase, gst, pack_size in cur.fetchall():
        cost_lookup[(name, batch or "")] = (purchase or 0.0, gst or 0.0, pack_size or "1")

    def _unit_value(medicine, batch, qty):
        purchase, gst, pack_size = cost_lookup.get((medicine, batch or ""), (0.0, 0.0, "1"))
        if not purchase:
            return 0.0
        try:
            pack_mult = get_pack_multiplier(pack_size) or 1
        except Exception:
            pack_mult = 1
        unit_price = (purchase + purchase * (gst / 100.0)) / pack_mult
        return round(unit_price * abs(qty), 2)

    movements = []

    cur.execute("SELECT bill_date, medicine, batch, qty FROM purchase")
    for bill_date, medicine, batch, qty in cur.fetchall():
        qty = qty or 0
        movements.append({
            "medicine": medicine, "batch": batch, "date": _parse_any_date(bill_date),
            "qty": qty, "value": _unit_value(medicine, batch, qty),
        })

    cur.execute("""
        SELECT s.bill_date, si.medicine, si.batch, si.qty
        FROM sales_items si JOIN sales s ON s.bill_no = si.bill_no
    """)
    for bill_date, medicine, batch, qty in cur.fetchall():
        qty = -(qty or 0)
        movements.append({
            "medicine": medicine, "batch": batch, "date": _parse_any_date(bill_date),
            "qty": qty, "value": _unit_value(medicine, batch, qty),
        })

    cur.execute("SELECT return_date, medicine, batch, qty FROM purchase_return")
    for return_date, medicine, batch, qty in cur.fetchall():
        qty = -(qty or 0)
        movements.append({
            "medicine": medicine, "batch": batch, "date": _parse_any_date(return_date),
            "qty": qty, "value": _unit_value(medicine, batch, qty),
        })

    cur.execute("SELECT return_date, medicine, batch, qty FROM sales_return")
    for return_date, medicine, batch, qty in cur.fetchall():
        qty = qty or 0
        movements.append({
            "medicine": medicine, "batch": batch, "date": _parse_any_date(return_date),
            "qty": qty, "value": _unit_value(medicine, batch, qty),
        })

    cur.execute("SELECT adj_date, medicine, batch, qty_change FROM stock_adjustments")
    for adj_date, medicine, batch, qty_change in cur.fetchall():
        qty_change = qty_change or 0
        movements.append({
            "medicine": medicine, "batch": batch, "date": _parse_any_date(adj_date),
            "qty": qty_change, "value": _unit_value(medicine, batch, qty_change),
        })

    con.close()
    return movements
