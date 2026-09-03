"""
brand_mapping.py
LifeCare Pharmacy ERP - Brand Master (Brand Name -> Generic/Manufacturer/
Category/Dosage Form reference catalog).

Why this is a separate module from generic_mapping.py:
find_brands_by_generic() in generic_mapping.py answers "which brands do
I ALREADY STOCK that share this composition" - it only ever looks at
medicine_master, so a brand that has never been purchased/stocked simply
isn't there. brand_master (this module) is the opposite direction: "I'm
about to add a BRAND-NEW medicine I've never stocked before - has anyone
already told this software what its generic/company/category/dosage form
normally is." Purchase Entry's offer_create_medicine() calls lookup_brand()
here, before insert, to pre-fill those fields instead of leaving them
blank (the earlier behaviour).

Data-safety note: brand_master is seeded ONLY from brand_seed_data.py,
which contains real data the pharmacist supplied directly - never
auto-generated. Indian pharma brand/manufacturer/generic mappings change
often enough, and a wrong mapping in a real pharmacy is a real patient-
safety risk, that this module will not invent or guess additional rows.
Growing the catalog beyond the seed list is meant to happen through
brand_master_gui.py (manual add, or bulk-paste from a spreadsheet the
pharmacist has verified), not from training-data guesses.
"""

import re
import sqlite3

from app_paths import DB_NAME


def normalize(text):
    """Collapse whitespace, lowercase (same base idea as generic_mapping.
    normalize()), AND fold common separator punctuation (hyphen,
    underscore, slash) to spaces. That last part matters more here than
    it does for generic_mapping's synonym matching: real brand names are
    routinely written both ways for the same product - "Zerodol-P" on
    the strip vs "Zerodol P" typed at the counter, "Pan-D" vs "Pan D" -
    and without folding those, a plain substring check treats the hyphen
    and the space as genuinely different characters and fails to match
    what is obviously the same brand."""
    text = (text or '').strip().lower()
    text = re.sub(r'[-_/]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


# ==========================================
# SCHEMA + SEEDING
# ==========================================

def ensure_brand_master(db_name=None):
    """
    Creates brand_master if it doesn't exist yet. database.py's
    create_database() already does this on every app startup, but this
    mirrors that here too (same defensive pattern as generic_mapping.py's
    own ensure_tables()) so any module that imports brand_mapping directly
    - without necessarily having gone through create_database() first,
    e.g. a standalone script or test - still gets a working table.
    """
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS brand_master(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_name TEXT UNIQUE,
            generic_text TEXT,
            dosage_form TEXT,
            manufacturer TEXT,
            category TEXT
        )
    """)
    con.commit()
    con.close()


def seed_brand_master(db_name=None, force=False):
    """
    Loads brand_seed_data.BRANDS into brand_master. Uses INSERT OR IGNORE
    (brand_name is UNIQUE) so re-running is always safe - nothing already
    present gets duplicated or overwritten.

    force=False (default, called on every startup via ensure path below)
    skips the whole list unless the table is currently empty - cheap to
    call repeatedly. force=True (Brand Master screen's "Load Starter
    Brands" button) re-applies the list regardless, restoring rows that
    were deleted, without touching anything the pharmacist added since.

    Returns how many NEW rows were added.
    """
    db_name = db_name or DB_NAME
    ensure_brand_master(db_name)
    con = sqlite3.connect(db_name)
    cur = con.cursor()

    from brand_seed_data import BRANDS

    do_insert = force
    if not do_insert:
        cur.execute("SELECT COUNT(*) FROM brand_master")
        do_insert = cur.fetchone()[0] == 0

    added = 0
    if do_insert:
        for brand_name, generic_text, dosage_form, manufacturer, category in BRANDS:
            cur.execute(
                "INSERT OR IGNORE INTO brand_master"
                "(brand_name, generic_text, dosage_form, manufacturer, category) "
                "VALUES (?,?,?,?,?)",
                (brand_name, generic_text, dosage_form, manufacturer, category)
            )
            added += cur.rowcount

    con.commit()
    con.close()
    return added


# ==========================================
# LOOKUP / RESOLVER FUNCTIONS
# ==========================================

def lookup_brand(brand_name, db_name=None):
    """
    Looks up a single brand by name - exact match first (case/space
    insensitive), then a normalized substring fallback (same longest-
    match idea used throughout generic_mapping.py) so close variations
    like "Zerodol P" vs seeded "Zerodol-P" still resolve.

    Returns a dict {brand_name, generic_text, dosage_form, manufacturer,
    category} for the best match, or None if nothing matches.
    """
    db_name = db_name or DB_NAME
    name = (brand_name or "").strip()
    if not name:
        return None

    ensure_brand_master(db_name)
    con = sqlite3.connect(db_name)
    cur = con.cursor()

    cur.execute(
        "SELECT brand_name, generic_text, dosage_form, manufacturer, category "
        "FROM brand_master WHERE lower(brand_name)=lower(?)",
        (name,)
    )
    row = cur.fetchone()
    if row:
        con.close()
        return {
            "brand_name": row[0], "generic_text": row[1],
            "dosage_form": row[2], "manufacturer": row[3], "category": row[4],
        }

    norm_query = normalize(name)
    cur.execute("SELECT brand_name, generic_text, dosage_form, manufacturer, category FROM brand_master")
    rows = cur.fetchall()
    con.close()

    best_row, best_len = None, 0
    for r in rows:
        norm_brand = normalize(r[0])
        if norm_brand and (norm_brand in norm_query or norm_query in norm_brand):
            if len(norm_brand) > best_len:
                best_row, best_len = r, len(norm_brand)

    if not best_row:
        return None
    return {
        "brand_name": best_row[0], "generic_text": best_row[1],
        "dosage_form": best_row[2], "manufacturer": best_row[3], "category": best_row[4],
    }


def resolve_composition_id(generic_text, db_name=None):
    """
    Gets-or-creates a composition_master row for generic_text, same logic
    as medicine_master.py's _get_or_create_composition_id() (kept in sync
    deliberately, not imported directly, since that method is private to
    the MedicineMaster class and this needs to be callable from
    purchase.py too). Returns None for a blank/missing generic_text
    rather than creating an empty composition row.
    """
    db_name = db_name or DB_NAME
    name = (generic_text or "").strip()
    if not name:
        return None

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute(
        "SELECT composition_id FROM composition_master WHERE lower(composition_name)=lower(?)",
        (name,)
    )
    existing = cur.fetchone()
    if existing:
        con.close()
        return existing[0]

    cur.execute("INSERT INTO composition_master (composition_name) VALUES (?)", (name,))
    con.commit()
    comp_id = cur.lastrowid
    con.close()
    return comp_id


# ==========================================
# MANAGEMENT / BULK-ADD FUNCTIONS (used by brand_master_gui.py)
# ==========================================

def get_all_brands(db_name=None):
    db_name = db_name or DB_NAME
    ensure_brand_master(db_name)
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute(
        "SELECT brand_name, generic_text, dosage_form, manufacturer, category "
        "FROM brand_master ORDER BY brand_name"
    )
    rows = cur.fetchall()
    con.close()
    return rows


def search_brands(search_text, db_name=None):
    """Simple in-memory filter over get_all_brands() - matches the same
    'filter what's on screen' pattern medicine_master.py's search_data()
    uses, appropriate for a catalog this size (dozens-hundreds of rows,
    not tens of thousands)."""
    text = normalize(search_text)
    if not text:
        return get_all_brands(db_name)
    return [
        row for row in get_all_brands(db_name)
        if text in normalize(" ".join(str(v or "") for v in row))
    ]


def add_brand(brand_name, generic_text, dosage_form, manufacturer, category, db_name=None):
    """
    Adds or updates one brand (INSERT OR REPLACE, keyed on the UNIQUE
    brand_name) - used by both the manual Add form and the bulk-paste
    tab in brand_master_gui.py. Returns False (does nothing) for a blank
    brand_name.
    """
    db_name = db_name or DB_NAME
    ensure_brand_master(db_name)
    brand_name = (brand_name or "").strip()
    if not brand_name:
        return False

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO brand_master"
        "(brand_name, generic_text, dosage_form, manufacturer, category) "
        "VALUES (?,?,?,?,?)",
        (brand_name, (generic_text or "").strip(), (dosage_form or "").strip(),
         (manufacturer or "").strip(), (category or "").strip())
    )
    con.commit()
    con.close()
    return True


def delete_brand(brand_name, db_name=None):
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("DELETE FROM brand_master WHERE brand_name=?", (brand_name,))
    con.commit()
    con.close()
