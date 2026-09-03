"""
generic_mapping.py
LifeCare Pharmacy ERP - Generic Medicine Auto Mapping (Brand <-> Generic Link)

Why a separate file:
This isn't a form tweak - it's its own small data model (synonym groups) on
top of medicine_master, and purchase.py, billing.py, and medicine_master.py
can all import the same resolver functions. Keeping it in one shared module
(same pattern as pricing_utils.py) means every screen sees the same brand
<-> generic links instead of each one growing its own copy that drifts out
of sync, which is exactly the bug we fixed earlier in stock.py/billing.py
for pack-size math.

What this adds beyond the plain `generic` text column on medicine_master:
  - A synonym table, so "Paracetamol" and "Acetaminophen" (same drug,
    different naming convention - common between Indian/BNF-style and
    US/USAN-style invoices) are recognised as the same composition family,
    which plain text matching on the `generic` column alone cannot do.
  - Two-way lookup: brand -> generic family, and generic -> every brand
    using it.
  - A management screen so the synonym list is something you curate over
    time from the UI, not something buried in code.

NOTE ON SEED DATA: a handful of well-known INN/BAN <-> USAN naming pairs
are pre-loaded below purely as a starting point (these are standard
published nonproprietary-naming differences, not medical dosing advice).
Treat them as a convenience starting list, not an authoritative or
exhaustive reference - review and extend them yourself via the management
screen for your actual stock.
"""

import re
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox

from app_paths import DB_NAME
import ui_style
import theme
import ui_popups

# A small starting point, not an exhaustive medical reference - curate
# further via the GenericMappingManager screen for your own catalog.
_SEED_SYNONYM_GROUPS = [
    ("Paracetamol", ["Paracetamol", "Acetaminophen", "PCM"]),
    ("Salbutamol", ["Salbutamol", "Albuterol"]),
    ("Frusemide", ["Frusemide", "Furosemide"]),
    ("Adrenaline", ["Adrenaline", "Epinephrine"]),
    ("Chlorpheniramine", ["Chlorpheniramine", "Chlorphenamine"]),
]


# ==========================================
# NORMALIZATION
# ==========================================

def normalize(text):
    """Collapses extra whitespace and lowercases, so spacing/case
    differences between invoices never break a match."""
    return re.sub(r'\s+', ' ', (text or '').strip()).lower()


# ==========================================
# SCHEMA + SEEDING
# ==========================================

def ensure_tables(db_name=None):
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS generic_groups(
            group_id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT UNIQUE NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS generic_synonyms(
            synonym_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            synonym_text TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES generic_groups(group_id)
        )
    """)

    cur.execute("SELECT COUNT(*) FROM generic_groups")
    if cur.fetchone()[0] == 0:
        for canonical, synonyms in _SEED_SYNONYM_GROUPS:
            cur.execute("INSERT INTO generic_groups(canonical_name) VALUES (?)", (canonical,))
            group_id = cur.lastrowid
            for syn in synonyms:
                cur.execute(
                    "INSERT INTO generic_synonyms(group_id, synonym_text) VALUES (?,?)",
                    (group_id, syn)
                )

    con.commit()
    con.close()


# ==========================================
# RESOLVER FUNCTIONS (import these from other modules)
# ==========================================

def resolve_group_id(text, db_name=None):
    """Returns the synonym-group id whose synonyms best match `text`
    (substring match on normalized text, longest synonym wins), or None
    if this composition has no group yet."""
    db_name = db_name or DB_NAME
    norm = normalize(text)
    if not norm:
        return None

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT group_id, synonym_text FROM generic_synonyms")
    rows = cur.fetchall()
    con.close()

    best_group, best_len = None, 0
    for group_id, syn in rows:
        norm_syn = normalize(syn)
        if norm_syn and (norm_syn in norm or norm in norm_syn):
            if len(norm_syn) > best_len:
                best_group, best_len = group_id, len(norm_syn)
    return best_group


def get_synonym_family(text, db_name=None):
    """Returns the set of normalized synonym strings that share a synonym
    group with `text` (always includes the normalized text itself)."""
    db_name = db_name or DB_NAME
    norm = normalize(text)
    family = {norm} if norm else set()

    group_id = resolve_group_id(text, db_name)
    if group_id:
        con = sqlite3.connect(db_name)
        cur = con.cursor()
        cur.execute("SELECT synonym_text FROM generic_synonyms WHERE group_id=?", (group_id,))
        for (syn,) in cur.fetchall():
            family.add(normalize(syn))
        con.close()

    return family


def find_brands_by_generic(generic_text, exclude_name=None, db_name=None, use_synonyms=True):
    """
    Main public API. Returns every medicine_master row whose `generic`
    column matches generic_text - including synonym-expanded matches
    (e.g. searching "Paracetamol" also finds brands stored under
    "Acetaminophen") when use_synonyms is True.

    Each result: (name, company, category, generic, sale, mrp, stock, gst)
    """
    db_name = db_name or DB_NAME
    ensure_tables(db_name)

    norm_query = normalize(generic_text)
    if not norm_query:
        return []

    search_terms = get_synonym_family(generic_text, db_name) if use_synonyms else {norm_query}

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("""
        SELECT name, company, category, generic, sale, mrp, stock, gst
        FROM medicine_master
        WHERE generic IS NOT NULL AND generic <> ''
    """)
    rows = cur.fetchall()
    con.close()

    matches = []
    for row in rows:
        row_norm = normalize(row[3])
        if any(term in row_norm or row_norm in term for term in search_terms if term):
            matches.append(row)

    if exclude_name:
        matches = [r for r in matches if r[0].strip().lower() != exclude_name.strip().lower()]

    return matches


def add_synonym(canonical_name, synonym_text, db_name=None):
    """Adds synonym_text to canonical_name's group, creating the group if
    it doesn't exist yet. Used by the management screen and available for
    any other module to call directly."""
    db_name = db_name or DB_NAME
    ensure_tables(db_name)

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    try:
        cur.execute("SELECT group_id FROM generic_groups WHERE canonical_name=?", (canonical_name,))
        row = cur.fetchone()
        if row:
            group_id = row[0]
        else:
            cur.execute("INSERT INTO generic_groups(canonical_name) VALUES (?)", (canonical_name,))
            group_id = cur.lastrowid

        cur.execute(
            "INSERT INTO generic_synonyms(group_id, synonym_text) VALUES (?,?)",
            (group_id, synonym_text)
        )
        con.commit()
        return True
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ==========================================
# MANAGEMENT GUI
# ==========================================

def ensure_composition_master(db_name=None):
    """
    The Centralized Salt/Composition Master - a proper authoritative list
    of compositions (like Category Master or Company Master), separate
    from the free-text `generic` column on medicine_master. Going forward,
    Medicine Master and Purchase should pick compositions FROM this list
    instead of retyping them, which prevents the spacing/casing
    inconsistencies (e.g. "Paracetamol 650mg" vs "paracetamol   650 MG")
    at the source rather than trying to fuzzy-match them after the fact.
    The synonym-group system above still helps match against OLDER
    free-text data that predates this table.
    """
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS composition_master(
            composition_id INTEGER PRIMARY KEY AUTOINCREMENT,
            composition_name TEXT UNIQUE NOT NULL
        )
    """)
    # மைக்ரேஷன்: "Uses" (என்ன condition-க்கு இந்த composition பயன்படுது-ன்னு
    # ஒரு general, layperson-readable tag - e.g. "Pain relief, Fever").
    # Substitute Medicine popup-ல் இது காட்டப்படும், substitute suggest
    # பண்றப்போ ஏன்-ன்னு காரணம் தெரிய.
    try:
        cur.execute("ALTER TABLE composition_master ADD COLUMN uses TEXT")
    except sqlite3.OperationalError:
        pass
    # மைக்ரேஷன்: "Habit Forming" flag (0/1) - benzodiazepines, opioids,
    # Z-drugs etc. Deliberately left NULL (no DEFAULT) for pre-existing
    # rows instead of defaulting to 0, so seed_composition_master()'s
    # backfill can tell "never checked yet" (NULL) apart from "checked
    # and confirmed not habit-forming" (0) or a user's own manual
    # toggle - a re-run of the backfill will never clobber a value that
    # was ever explicitly set, by seeding or by hand.
    try:
        cur.execute("ALTER TABLE composition_master ADD COLUMN habit_forming INTEGER")
    except sqlite3.OperationalError:
        pass
    # மைக்ரேஷன்: Action/Chemical Class (e.g. "NSAID / Analgesic-Antipyretic",
    # "Antibiotic - Penicillin") - broad therapeutic classification, kept
    # separate from medicine_master.category (a free-text field that's
    # unconstrained and largely unused in practice, so not repurposed here).
    # NULL means "not backfilled/checked yet" - same reasoning as
    # habit_forming above.
    try:
        cur.execute("ALTER TABLE composition_master ADD COLUMN action_class TEXT")
    except sqlite3.OperationalError:
        pass
    # மைக்ரேஷன்: Schedule X flag (0/1) - the narrower NDPS/narcotic subset
    # of habit_forming that legally needs a SEPARATE register (double-lock
    # storage, stricter record-keeping) rather than just the ordinary
    # Schedule H1 prescription register. Same NULL-guarded backfill
    # reasoning as habit_forming above.
    try:
        cur.execute("ALTER TABLE composition_master ADD COLUMN schedule_x INTEGER")
    except sqlite3.OperationalError:
        pass
    # மைக்ரேஷன் (Sep 2026): "Side Effects" free text - added for the
    # Composition Master's Edit dialog, so composition info copy-pasted
    # from an outside reference (a medicine info site, a supplier leaflet)
    # has somewhere to go beyond the short "Uses" tag. NULL for every
    # existing row until someone fills it in via the Edit dialog - there
    # is no seed/backfill for this column, unlike uses/action_class/
    # habit_forming/schedule_x above, since composition_seed_data.py
    # never shipped side-effects text.
    try:
        cur.execute("ALTER TABLE composition_master ADD COLUMN side_effects TEXT")
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()

    # Auto-seed only if the table is completely empty (seed_composition_
    # master()'s own force=False check) - so this is a cheap no-op on
    # every normal startup after the first, but self-heals a table that
    # got cleared (accidentally or otherwise) without needing a manual
    # step.
    seed_composition_master(db_name)


def seed_composition_master(db_name=None, force=False):
    """
    Pre-populates composition_master with ~1100 standard pharmaceutical
    compositions (see composition_seed_data.py) - single drugs and
    common Indian-market fixed-dose combinations, strengths included -
    so Medicine Master's Generic dropdown and the Substitute Medicine
    feature (find_brands_by_generic) have real, useful data to work
    with out of the box instead of starting completely empty.

    Uses INSERT OR IGNORE, so re-running this is always safe: nothing
    already in the table gets duplicated or overwritten.

    force=False (the default, used by ensure_composition_master() on
    every startup) skips the whole seed list unless the table is
    currently empty - cheap to call repeatedly. force=True (the
    Composition Master screen's "Load Standard Compositions" button)
    re-applies the seed list regardless, which is what actually restores
    a table that was cleared out without waiting for a fresh install.

    Returns how many NEW rows were added.
    """
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()

    from composition_seed_data import COMPOSITIONS, USES, HABIT_FORMING, ACTION_CLASS, SCHEDULE_X

    do_insert = force
    if not do_insert:
        cur.execute("SELECT COUNT(*) FROM composition_master")
        do_insert = cur.fetchone()[0] == 0

    added = 0
    if do_insert:
        for name in COMPOSITIONS:
            cur.execute(
                "INSERT OR IGNORE INTO composition_master (composition_name, uses, habit_forming, action_class, schedule_x) VALUES (?, ?, ?, ?, ?)",
                (name, USES.get(name, ""), 1 if HABIT_FORMING.get(name) else 0, ACTION_CLASS.get(name, ""), 1 if SCHEDULE_X.get(name) else 0)
            )
            added += cur.rowcount

    # Backfill `uses` on any row that matches a seed name but doesn't
    # have it yet - covers compositions that were inserted before this
    # Uses column existed (exactly what happened to this app's own DB:
    # 1122 compositions were seeded first, the Uses column came after).
    # Cheap after the first run: skips the whole loop once nothing is
    # missing a use, instead of re-checking 1122 rows on every startup.
    cur.execute("SELECT COUNT(*) FROM composition_master WHERE uses IS NULL OR uses=''")
    if cur.fetchone()[0] > 0:
        for name, use_text in USES.items():
            if use_text:
                cur.execute(
                    "UPDATE composition_master SET uses=? WHERE composition_name=? AND (uses IS NULL OR uses='')",
                    (use_text, name)
                )

    # Backfill `habit_forming` the same way, but only for rows that are
    # still NULL (never checked). Unlike `uses`, 0 is a real, meaningful
    # value here ("checked, not habit-forming") - so once a row has 0 or
    # 1, from seeding or a manual toggle in the UI, this loop leaves it
    # alone forever, even if HABIT_FORMING's own keyword list changes.
    cur.execute("SELECT COUNT(*) FROM composition_master WHERE habit_forming IS NULL")
    if cur.fetchone()[0] > 0:
        for name, flag in HABIT_FORMING.items():
            cur.execute(
                "UPDATE composition_master SET habit_forming=? WHERE composition_name=? AND habit_forming IS NULL",
                (1 if flag else 0, name)
            )

    # Backfill `action_class` the same NULL-guarded way. An empty string
    # here is a legitimate result ("checked, keyword list didn't match
    # anything") - once set (by seeding or a future manual edit), it's
    # never touched again.
    cur.execute("SELECT COUNT(*) FROM composition_master WHERE action_class IS NULL")
    if cur.fetchone()[0] > 0:
        for name, cls in ACTION_CLASS.items():
            cur.execute(
                "UPDATE composition_master SET action_class=? WHERE composition_name=? AND action_class IS NULL",
                (cls, name)
            )

    # Backfill `schedule_x` the same NULL-guarded way as habit_forming -
    # once a row has 0 or 1 (seeded or manually toggled), this leaves it
    # alone forever.
    cur.execute("SELECT COUNT(*) FROM composition_master WHERE schedule_x IS NULL")
    if cur.fetchone()[0] > 0:
        for name, flag in SCHEDULE_X.items():
            cur.execute(
                "UPDATE composition_master SET schedule_x=? WHERE composition_name=? AND schedule_x IS NULL",
                (1 if flag else 0, name)
            )

    con.commit()
    con.close()
    return added


def get_all_compositions(db_name=None):
    db_name = db_name or DB_NAME
    ensure_composition_master(db_name)
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT composition_name FROM composition_master ORDER BY composition_name")
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return rows


def get_composition_uses(composition_name, db_name=None):
    """
    Looks up the Uses/Indication tag for a composition - exact match first
    (fast path for names picked straight from the Composition Master list),
    falling back to a normalized substring match (same longest-match idea
    as resolve_group_id above) so older free-text `generic` values typed
    before this master existed still surface a Uses tag when they're close
    enough to a known composition (e.g. "Paracetamol 500 mg tab" vs the
    master's "Paracetamol 500mg"). Returns "" if nothing matches.
    """
    db_name = db_name or DB_NAME
    name = (composition_name or "").strip()
    if not name:
        return ""

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT uses FROM composition_master WHERE composition_name=?", (name,))
    row = cur.fetchone()
    if row and row[0]:
        con.close()
        return row[0]

    norm_query = normalize(name)
    cur.execute("SELECT composition_name, uses FROM composition_master WHERE uses IS NOT NULL AND uses <> ''")
    rows = cur.fetchall()
    con.close()

    best_uses, best_len = "", 0
    for comp_name, uses in rows:
        norm_comp = normalize(comp_name)
        if norm_comp and (norm_comp in norm_query or norm_query in norm_comp):
            if len(norm_comp) > best_len:
                best_uses, best_len = uses, len(norm_comp)
    return best_uses


def get_composition_habit_forming(composition_name, db_name=None):
    """
    Returns True if `composition_name` is flagged Habit Forming - exact
    match first, then the same normalized substring fallback used by
    get_composition_uses() above, so free-text `generic` values close to
    a known composition still surface the flag. Returns False (not
    flagged / unknown) if nothing matches - callers should treat this as
    "no caution known", not as a confirmed "safe" determination.
    """
    db_name = db_name or DB_NAME
    name = (composition_name or "").strip()
    if not name:
        return False

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT habit_forming FROM composition_master WHERE composition_name=?", (name,))
    row = cur.fetchone()
    if row is not None and row[0] is not None:
        con.close()
        return bool(row[0])

    norm_query = normalize(name)
    cur.execute("SELECT composition_name, habit_forming FROM composition_master WHERE habit_forming=1")
    rows = cur.fetchall()
    con.close()

    for comp_name, _flag in rows:
        norm_comp = normalize(comp_name)
        if norm_comp and (norm_comp in norm_query or norm_query in norm_comp):
            return True
    return False


def get_composition_action_class(composition_name, db_name=None):
    """
    Returns the Action/Chemical Class text for `composition_name` - exact
    match first, then the same normalized substring fallback as
    get_composition_uses(). Returns "" if unclassified or unmatched.
    """
    db_name = db_name or DB_NAME
    name = (composition_name or "").strip()
    if not name:
        return ""

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT action_class FROM composition_master WHERE composition_name=?", (name,))
    row = cur.fetchone()
    if row and row[0]:
        con.close()
        return row[0]

    norm_query = normalize(name)
    cur.execute("SELECT composition_name, action_class FROM composition_master WHERE action_class IS NOT NULL AND action_class <> ''")
    rows = cur.fetchall()
    con.close()

    best_class, best_len = "", 0
    for comp_name, cls in rows:
        norm_comp = normalize(comp_name)
        if norm_comp and (norm_comp in norm_query or norm_query in norm_comp):
            if len(norm_comp) > best_len:
                best_class, best_len = cls, len(norm_comp)
    return best_class


def get_composition_schedule_x(composition_name, db_name=None):
    """
    Returns True if `composition_name` is flagged Schedule X (narcotic/
    NDPS register required) - same exact-match-then-normalized-substring
    fallback as get_composition_habit_forming(). Returns False if nothing
    matches - callers should treat this as "no flag known", not a
    confirmed "not controlled" determination.
    """
    db_name = db_name or DB_NAME
    name = (composition_name or "").strip()
    if not name:
        return False

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT schedule_x FROM composition_master WHERE composition_name=?", (name,))
    row = cur.fetchone()
    if row is not None and row[0] is not None:
        con.close()
        return bool(row[0])

    norm_query = normalize(name)
    cur.execute("SELECT composition_name, schedule_x FROM composition_master WHERE schedule_x=1")
    rows = cur.fetchall()
    con.close()

    for comp_name, _flag in rows:
        norm_comp = normalize(comp_name)
        if norm_comp and (norm_comp in norm_query or norm_query in norm_comp):
            return True
    return False


def set_schedule_x(name, flag, db_name=None):
    """Explicitly sets (or clears) the Schedule X flag for one
    composition - used by the Composition Master screen's toggle button.
    Once set here, seed_composition_master()'s backfill will never
    overwrite it again."""
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute(
        "UPDATE composition_master SET schedule_x=? WHERE composition_name=?",
        (1 if flag else 0, name)
    )
    con.commit()
    con.close()


def set_habit_forming(name, flag, db_name=None):
    """Explicitly sets (or clears) the Habit Forming flag for one
    composition - used by the Composition Master screen's toggle button.
    Once set here, seed_composition_master()'s backfill will never
    overwrite it again (see that function's habit_forming backfill,
    which only touches rows still NULL)."""
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute(
        "UPDATE composition_master SET habit_forming=? WHERE composition_name=?",
        (1 if flag else 0, name)
    )
    con.commit()
    con.close()


def get_composition_side_effects(composition_name, db_name=None):
    """Same exact-match-then-normalized-substring fallback as
    get_composition_uses() above. Returns "" if nothing matches - there is
    no seed data for this column, so this only ever finds something after
    someone has typed it in via the Edit dialog."""
    db_name = db_name or DB_NAME
    name = (composition_name or "").strip()
    if not name:
        return ""

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT side_effects FROM composition_master WHERE composition_name=?", (name,))
    row = cur.fetchone()
    if row and row[0]:
        con.close()
        return row[0]

    norm_query = normalize(name)
    cur.execute(
        "SELECT composition_name, side_effects FROM composition_master WHERE side_effects IS NOT NULL AND side_effects <> ''"
    )
    rows = cur.fetchall()
    con.close()

    best, best_len = "", 0
    for comp_name, side_effects in rows:
        norm_comp = normalize(comp_name)
        if norm_comp and (norm_comp in norm_query or norm_query in norm_comp):
            if len(norm_comp) > best_len:
                best, best_len = side_effects, len(norm_comp)
    return best


def get_composition_details(name, db_name=None):
    """Exact-match lookup of every editable field for one composition -
    used by the Composition Master's Edit dialog, which always edits one
    specific row picked from the list (never a free-text generic string),
    so the fuzzy fallback the get_composition_*() lookups above use isn't
    needed or wanted here. Returns None if `name` isn't in the table."""
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute(
        "SELECT composition_name, uses, action_class, side_effects FROM composition_master WHERE composition_name=?",
        (name,)
    )
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    return {"name": row[0], "uses": row[1] or "", "action_class": row[2] or "", "side_effects": row[3] or ""}


def update_composition(old_name, new_name, uses, action_class, side_effects, db_name=None):
    """
    Saves every editable field for one composition in a single commit -
    used by the Composition Master's Edit dialog Save button. Handles a
    rename (when `new_name` differs from `old_name`) plus the three free
    text fields together, so a half-saved edit (name renamed but Uses
    text lost, or vice versa) can't happen.

    Renaming is safe for medicines already using this composition:
    medicine_master links to a composition by composition_id (see
    _get_or_create_composition_id() in medicine_master.py), never by
    name, so changing the name here doesn't orphan or break anything
    already pointing at this row.

    Returns "" on success, or a short error message (empty name, or a
    rename that collides with an existing composition) for the dialog to
    show inline without closing.
    """
    db_name = db_name or DB_NAME
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not new_name:
        return "Composition name can't be empty."

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    try:
        if new_name != old_name:
            cur.execute("SELECT 1 FROM composition_master WHERE composition_name=?", (new_name,))
            if cur.fetchone():
                return f'"{new_name}" already exists in the Composition Master.'
            cur.execute(
                "UPDATE composition_master SET composition_name=? WHERE composition_name=?",
                (new_name, old_name)
            )
        cur.execute(
            "UPDATE composition_master SET uses=?, action_class=?, side_effects=? WHERE composition_name=?",
            ((uses or "").strip(), (action_class or "").strip(), (side_effects or "").strip(), new_name)
        )
        con.commit()
        return ""
    finally:
        con.close()


def open_edit_composition_dialog(parent, name, db_name=None, on_saved=None):
    """
    Edit dialog for one composition's full record - Composition Name
    (rename), Therapy/Action Class, Uses, and Side Effects. Opened from
    the Composition Master screen's "Edit Selected" button.

    This is deliberately free-text for all three description fields -
    unlike the ~1100 compositions composition_seed_data.py ships with
    (which only ever get Uses/Action Class from that file), this is how
    you enrich a composition you added yourself, or correct/extend an
    existing one, straight from copy-pasted reference text (a medicine
    information site, a supplier leaflet) without editing any code file.
    """
    db_name = db_name or DB_NAME
    details = get_composition_details(name, db_name)
    if not details:
        ui_popups.show_error(parent, "Not Found", f'"{name}" is no longer in the Composition Master.')
        return

    win = tk.Toplevel(parent)
    win.title("Edit Composition")
    ui_style.center_window(win, 520, 560, parent=parent)
    win.bind("<Escape>", lambda event: win.destroy())
    win.focus_force()
    win.grab_set()

    body = ui_style.popup_header(win, "EDIT COMPOSITION", icon="✏️")

    form = tk.Frame(body, bg=theme.SURFACE_WHITE)
    form.pack(fill="both", expand=True, padx=10, pady=(0, 5))

    def labeled_entry(label_text, initial_value):
        tk.Label(
            form, text=label_text, bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
            font=("Segoe UI", 9, "bold"), anchor="w"
        ).pack(fill="x", pady=(6, 2))
        var = tk.StringVar(value=initial_value)
        tk.Entry(
            form, textvariable=var, font=("Segoe UI", 10), bg=theme.SURFACE_FIELD,
            relief="flat", highlightthickness=1, highlightbackground=theme.BORDER_DEFAULT,
            highlightcolor=theme.BORDER_FOCUS,
        ).pack(fill="x", ipady=4)
        return var

    def labeled_text(label_text, initial_value, height=3):
        tk.Label(
            form, text=label_text, bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
            font=("Segoe UI", 9, "bold"), anchor="w"
        ).pack(fill="x", pady=(6, 2))
        txt = tk.Text(
            form, height=height, font=("Segoe UI", 10), bg=theme.SURFACE_FIELD,
            relief="flat", highlightthickness=1, highlightbackground=theme.BORDER_DEFAULT,
            highlightcolor=theme.BORDER_FOCUS, wrap="word",
        )
        txt.insert("1.0", initial_value)
        txt.pack(fill="x")
        return txt

    name_var = labeled_entry("Composition Name (Salt Content)", details["name"])
    action_class_var = labeled_entry("Therapy / Action Class", details["action_class"])
    uses_text = labeled_text("Uses", details["uses"], height=3)
    side_effects_text = labeled_text("Side Effects", details["side_effects"], height=3)

    status_var = tk.StringVar(value="")
    tk.Label(
        form, textvariable=status_var, bg=theme.SURFACE_WHITE, fg=theme.STATUS_DANGER,
        font=("Segoe UI", 9), anchor="w", wraplength=460, justify="left",
    ).pack(fill="x", pady=(8, 0))

    def do_save():
        error = update_composition(
            details["name"],
            name_var.get(),
            uses_text.get("1.0", "end"),
            action_class_var.get(),
            side_effects_text.get("1.0", "end"),
            db_name,
        )
        if error:
            status_var.set(error)
            return
        if on_saved:
            on_saved()
        win.destroy()

    btn_row = tk.Frame(body, bg=theme.SURFACE_WHITE)
    btn_row.pack(pady=(5, 10))
    ui_style.flat_button(btn_row, "Save", theme.STATUS_SUCCESS, do_save, width=14).pack(side="left", padx=5)
    ui_style.flat_button(btn_row, "Cancel", theme.ACCENT_NEUTRAL, win.destroy, width=14).pack(side="left", padx=5)


def add_composition(name, db_name=None):
    """Adds a composition to the master if it isn't already there.
    Returns True if it was newly added, False if it already existed."""
    db_name = db_name or DB_NAME
    ensure_composition_master(db_name)
    name = (name or "").strip()
    if not name:
        return False

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT 1 FROM composition_master WHERE composition_name=?", (name,))
    already_exists = cur.fetchone() is not None
    if not already_exists:
        cur.execute("INSERT INTO composition_master(composition_name) VALUES (?)", (name,))
        con.commit()
    con.close()
    return not already_exists


def delete_composition(name, db_name=None):
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("DELETE FROM composition_master WHERE composition_name=?", (name,))
    con.commit()
    con.close()


def add_composition_full(name, uses, action_class, side_effects, db_name=None):
    """
    Adds a brand-new composition with every field filled in one step -
    used by the Composition Master screen's "Add New Composition"
    dialog, so entering a composition copy-pasted from an outside
    reference (a medicine info site, a supplier leaflet) doesn't need a
    separate Add-a-bare-name-then-Edit-Selected round trip: Name,
    Therapy/Action Class, Uses, and Side Effects are all saved together
    in a single commit.

    Returns "" on success, or a short error message (empty name, or a
    name that already exists) for the dialog to show inline without
    closing.
    """
    db_name = db_name or DB_NAME
    ensure_composition_master(db_name)
    name = (name or "").strip()
    if not name:
        return "Composition name can't be empty."

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    try:
        cur.execute("SELECT 1 FROM composition_master WHERE composition_name=?", (name,))
        if cur.fetchone():
            return f'"{name}" already exists in the Composition Master.'
        cur.execute(
            "INSERT INTO composition_master (composition_name, uses, action_class, side_effects) VALUES (?, ?, ?, ?)",
            (name, (uses or "").strip(), (action_class or "").strip(), (side_effects or "").strip())
        )
        con.commit()
        return ""
    finally:
        con.close()


def open_add_composition_dialog(parent, db_name=None, on_saved=None):
    """
    Add dialog for a brand-new composition - the same four fields as
    open_edit_composition_dialog() (Composition Name, Therapy/Action
    Class, Uses, Side Effects), so a new composition can be entered with
    full details in one step instead of adding a bare name first and
    coming back to "Edit Selected" afterwards to fill in the rest.
    """
    db_name = db_name or DB_NAME
    win = tk.Toplevel(parent)
    win.title("Add Composition")
    ui_style.center_window(win, 520, 560, parent=parent)
    win.bind("<Escape>", lambda event: win.destroy())
    win.focus_force()
    win.grab_set()

    body = ui_style.popup_header(win, "ADD COMPOSITION", icon="➕")

    form = tk.Frame(body, bg=theme.SURFACE_WHITE)
    form.pack(fill="both", expand=True, padx=10, pady=(0, 5))

    def labeled_entry(label_text, initial_value):
        tk.Label(
            form, text=label_text, bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
            font=("Segoe UI", 9, "bold"), anchor="w"
        ).pack(fill="x", pady=(6, 2))
        var = tk.StringVar(value=initial_value)
        tk.Entry(
            form, textvariable=var, font=("Segoe UI", 10), bg=theme.SURFACE_FIELD,
            relief="flat", highlightthickness=1, highlightbackground=theme.BORDER_DEFAULT,
            highlightcolor=theme.BORDER_FOCUS,
        ).pack(fill="x", ipady=4)
        return var

    def labeled_text(label_text, initial_value, height=3):
        tk.Label(
            form, text=label_text, bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
            font=("Segoe UI", 9, "bold"), anchor="w"
        ).pack(fill="x", pady=(6, 2))
        txt = tk.Text(
            form, height=height, font=("Segoe UI", 10), bg=theme.SURFACE_FIELD,
            relief="flat", highlightthickness=1, highlightbackground=theme.BORDER_DEFAULT,
            highlightcolor=theme.BORDER_FOCUS, wrap="word",
        )
        txt.insert("1.0", initial_value)
        txt.pack(fill="x")
        return txt

    name_var = labeled_entry("Composition Name (Salt Content)", "")
    action_class_var = labeled_entry("Therapy / Action Class", "")
    uses_text = labeled_text("Uses", "", height=3)
    side_effects_text = labeled_text("Side Effects", "", height=3)

    status_var = tk.StringVar(value="")
    tk.Label(
        form, textvariable=status_var, bg=theme.SURFACE_WHITE, fg=theme.STATUS_DANGER,
        font=("Segoe UI", 9), anchor="w", wraplength=460, justify="left",
    ).pack(fill="x", pady=(8, 0))

    def do_save():
        error = add_composition_full(
            name_var.get(),
            uses_text.get("1.0", "end"),
            action_class_var.get(),
            side_effects_text.get("1.0", "end"),
            db_name,
        )
        if error:
            status_var.set(error)
            return
        if on_saved:
            on_saved()
        win.destroy()

    btn_row = tk.Frame(body, bg=theme.SURFACE_WHITE)
    btn_row.pack(pady=(5, 10))
    ui_style.flat_button(btn_row, "Save", theme.STATUS_SUCCESS, do_save, width=14).pack(side="left", padx=5)
    ui_style.flat_button(btn_row, "Cancel", theme.ACCENT_NEUTRAL, win.destroy, width=14).pack(side="left", padx=5)


def show_composition_master(parent, db_name=None, on_change=None):
    """
    Management popup for the Composition Master - view every canonical
    composition, add new ones, delete unused ones. Called from a button
    inside Medicine Master / Purchase rather than living as its own
    top-level sidebar item.
    `on_change` (optional) is called after any add/delete, so the caller
    can refresh its own dropdown immediately.
    """
    db_name = db_name or DB_NAME
    win = tk.Toplevel(parent)
    win.title("Composition Master (Salt Master)")
    ui_style.center_window(win, 480, 520, parent=parent)
    # Esc key also closes this popup, same as its Close button.
    win.bind("<Escape>", lambda event: win.destroy())
    win.focus_force()

    # Aug 2026 visual refresh: same colored-header / white-body /
    # flat-button look as every other hand-built popup app-wide (see
    # ui_style.popup_header()'s docstring).
    body = ui_style.popup_header(win, "CENTRALIZED SALT / COMPOSITION MASTER", icon="🧪")

    add_frame = tk.Frame(body, bg=theme.SURFACE_WHITE)
    add_frame.pack(fill="x", padx=10, pady=10)

    listbox = tk.Listbox(
        body, width=50, height=18, font=("Segoe UI", 10),
        bg=theme.SURFACE_FIELD, relief="flat", highlightthickness=1,
        highlightbackground=theme.BORDER_DEFAULT, highlightcolor=theme.BORDER_FOCUS,
    )

    # Parallel list mapping each listbox row back to its plain composition
    # name (the listbox itself shows "name  -  uses" for readability, so we
    # can't just listbox.get() and use that directly for delete/lookup).
    current_names = []

    def refresh_list():
        listbox.delete(0, tk.END)
        current_names.clear()
        con = sqlite3.connect(db_name)
        cur = con.cursor()
        cur.execute("SELECT composition_name, uses, habit_forming, action_class, schedule_x FROM composition_master ORDER BY composition_name")
        rows = cur.fetchall()
        con.close()
        for name, uses, habit_forming, action_class, schedule_x in rows:
            current_names.append(name)
            display = f"{name}  -  {uses}" if uses else name
            if action_class:
                display = f"{display}  [{action_class}]"
            if habit_forming:
                display = f"⚠ {display}"
            if schedule_x:
                display = f"🔒 {display}"
            listbox.insert(tk.END, display)
        if on_change:
            on_change()

    def do_add():
        open_add_composition_dialog(win, db_name, on_saved=refresh_list)

    ui_style.flat_button(
        add_frame, "Add New Composition (Full Details)",
        theme.STATUS_SUCCESS, do_add, width=32,
    ).pack(side="left")

    listbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def do_delete():
        selection = listbox.curselection()
        if not selection:
            return
        name = current_names[selection[0]]
        if ui_popups.show_confirmation(win, "Delete Composition", f'Remove "{name}" from the Composition Master?'):
            delete_composition(name, db_name)
            refresh_list()

    def do_load_standard():
        if not ui_popups.show_confirmation(win, 
            "Load Standard Compositions",
            "Add ~1100 standard pharmaceutical compositions (single drugs + "
            "common fixed-dose combinations) to this list?\n\n"
            "Safe to run anytime - anything already in your list is left "
            "untouched, only missing ones get added."
        ):
            return
        added = seed_composition_master(db_name, force=True)
        ui_popups.show_info(win, "Done", f"{added} new composition(s) added.")
        refresh_list()

    def do_toggle_habit_forming():
        selection = listbox.curselection()
        if not selection:
            ui_popups.show_info(win, "Select a Row", "Select a composition first.")
            return
        name = current_names[selection[0]]
        current = get_composition_habit_forming(name, db_name)
        new_flag = not current
        set_habit_forming(name, new_flag, db_name)
        state_text = "flagged as Habit Forming" if new_flag else "un-flagged (no longer Habit Forming)"
        ui_popups.show_info(win, "Updated", f'"{name}" is now {state_text}.')
        refresh_list()

    def do_toggle_schedule_x():
        selection = listbox.curselection()
        if not selection:
            ui_popups.show_info(win, "Select a Row", "Select a composition first.")
            return
        name = current_names[selection[0]]
        current = get_composition_schedule_x(name, db_name)
        new_flag = not current
        set_schedule_x(name, new_flag, db_name)
        state_text = "flagged as Schedule X (narcotic register)" if new_flag else "un-flagged (no longer Schedule X)"
        ui_popups.show_info(win, "Updated", f'"{name}" is now {state_text}.')
        refresh_list()

    def do_edit():
        selection = listbox.curselection()
        if not selection:
            ui_popups.show_info(win, "Select a Row", "Select a composition first.")
            return
        name = current_names[selection[0]]
        open_edit_composition_dialog(win, name, db_name, on_saved=refresh_list)

    ui_style.flat_button(
        body, "Load Standard Compositions (1000+)", theme.ACCENT_SUBSTITUTE, do_load_standard, width=32,
    ).pack(pady=(0, 5))

    ui_style.flat_button(
        body, "Edit Selected (Full Details)", theme.PRIMARY, do_edit, width=32,
    ).pack(pady=(0, 5))

    ui_style.flat_button(
        body, "Toggle Habit Forming (⚠) for Selected", theme.ACCENT_PRESCRIPTION, do_toggle_habit_forming, width=32,
    ).pack(pady=(0, 5))

    ui_style.flat_button(
        body, "Toggle Schedule X (🔒) for Selected", theme.ACCENT_SCHEDULE_X, do_toggle_schedule_x, width=32,
    ).pack(pady=(0, 5))

    ui_style.flat_button(body, "Delete Selected", theme.STATUS_DANGER, do_delete, width=32).pack(pady=(0, 10))

    refresh_list()
    return win


def show_matches_popup(parent, generic_text, exclude_name=None, db_name=None):
    """
    Shared popup: shows every brand matching generic_text (synonym-aware).
    Used by both purchase.py and medicine_master.py so there's only one
    version of this UI to maintain.
    """
    matches = find_brands_by_generic(generic_text, exclude_name=exclude_name, db_name=db_name, use_synonyms=True)

    win = tk.Toplevel(parent)
    win.title(f'Brands matching "{generic_text}"')
    ui_style.center_window(win, 650, 350, parent=parent)
    # Esc key also closes this popup, same as its Close button.
    win.bind("<Escape>", lambda event: win.destroy())
    win.focus_force()

    # Aug 2026 visual refresh: same colored-header / white-body /
    # flat-button look as every other hand-built popup app-wide (see
    # ui_style.popup_header()'s docstring).
    body = ui_style.popup_header(win, f'Brands matching "{generic_text}"', icon="🔎")

    if not matches:
        tk.Label(
            body,
            text="No existing medicine found with this composition (including known naming synonyms).\nThis looks like a genuinely new entry.",
            bg=theme.SURFACE_WHITE, fg=theme.STATUS_SUCCESS,
            font=("Segoe UI", 10)
        ).pack(pady=30)
        ui_style.flat_button(body, "Close", theme.PRIMARY, win.destroy, width=15).pack(pady=10)
        return matches

    cols = ("Brand Name", "Company", "Category", "Generic", "MRP", "Stock")
    table = ttk.Treeview(body, columns=cols, show="headings", height=10)
    for c in cols:
        table.heading(c, text=c)
        table.column(c, width=100 if c != "Brand Name" else 150, anchor="center")
    table.pack(fill="both", expand=True, padx=10, pady=5)

    for name, company, category, generic, sale, mrp, stock, gst in matches:
        table.insert("", "end", values=(name, company or "", category or "", generic or "", mrp or 0, stock or 0))

    tk.Label(
        body,
        text=f"{len(matches)} existing brand(s) found with this composition (synonyms included).",
        bg=theme.SURFACE_WHITE, fg=theme.STATUS_DANGER,
        font=("Segoe UI", 10, "bold")
    ).pack(pady=(0, 5))

    ui_style.flat_button(body, "Close", theme.PRIMARY, win.destroy, width=15).pack(pady=5)
    return matches


def show_substitute_selector(parent, generic_text, exclude_name=None, db_name=None, on_select=None, in_stock_only=True):
    """
    Actionable substitute-brand picker - used by Billing (offer an
    alternative when the selected medicine is genuinely out of stock)
    and Stock (browse what else shares this composition). Unlike
    show_matches_popup() above (a read-only duplicate-composition check
    used when creating new medicine entries), this one can react to a
    choice: pass `on_select(name)` and double-clicking a row calls it
    with the chosen brand name, then closes the popup - Billing uses
    this to swap the selected medicine and re-run its own stock lookup
    immediately, without the user having to retype anything.

    `in_stock_only=True` (Billing's case) hides substitutes that are
    themselves out of stock - suggesting another empty shelf doesn't
    help. Stock's own browse-everything view passes False.
    """
    matches = find_brands_by_generic(generic_text, exclude_name=exclude_name, db_name=db_name, use_synonyms=True)
    if in_stock_only:
        matches = [m for m in matches if (m[6] or 0) > 0]

    win = tk.Toplevel(parent)
    win.title(f'Substitutes for "{generic_text}"')
    ui_style.center_window(win, 680, 380, parent=parent)
    win.grab_set()

    def _close(event=None):
        # Same grab-release-before-destroy pattern used everywhere else
        # in the app (see bulk_import.py's edit dialog) - skipping
        # grab_release() here would leave the parent window's own
        # buttons unresponsive after this popup closes.
        try:
            win.grab_release()
        except Exception:
            pass
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", _close)
    # Esc key also closes this popup (same as Close/the window's X).
    win.bind("<Escape>", _close)
    win.focus_force()

    # Aug 2026 visual refresh: same colored-header / white-body /
    # flat-button look as every other hand-built popup app-wide (see
    # ui_style.popup_header()'s docstring).
    body = ui_style.popup_header(win, f'Alternatives for composition: "{generic_text}"', icon="🔄")

    # Uses/Indication tag for the composition being searched - tells the
    # user WHY these are being offered as substitutes (e.g. "Pain relief,
    # Fever"), not just that the salt name matches. Only shown when a tag
    # is actually found (exact or close-match) - stays silent otherwise
    # rather than showing an empty/awkward line.
    uses_text = get_composition_uses(generic_text, db_name)
    class_text = get_composition_action_class(generic_text, db_name)
    info_line = " | ".join(filter(None, [
        f"Class: {class_text}" if class_text else "",
        f"Uses: {uses_text}" if uses_text else "",
    ]))
    if info_line:
        tk.Label(
            body, text=info_line,
            bg=theme.QUICK_PICK_BG, fg=theme.TABLE_SELECT_FG, font=("Segoe UI", 10, "italic"), pady=4
        ).pack(fill="x")

    # Habit Forming caution - advisory only (see composition_seed_data.py's
    # HABIT_FORMING_KEYWORDS docstring). Shown prominently since this is
    # exactly the moment a substitute is being picked for dispensing.
    if get_composition_habit_forming(generic_text, db_name):
        tk.Label(
            body,
            text="⚠ HABIT FORMING - verify prescription / Schedule H1 requirement before dispensing",
            bg=theme.WARNING_BANNER_BG, fg=theme.WARNING_BANNER_FG, font=("Segoe UI", 10, "bold"), pady=4
        ).pack(fill="x")

    if not matches:
        msg = (
            "No IN-STOCK alternative found with this composition."
            if in_stock_only else
            "No other brand found with this composition."
        )
        tk.Label(body, text=msg, bg=theme.SURFACE_WHITE, fg=theme.STATUS_DANGER, font=("Segoe UI", 10)).pack(pady=30)
        ui_style.flat_button(body, "Close", theme.PRIMARY, _close, width=15).pack(pady=10)
        return matches

    cols = ("Brand Name", "Company", "MRP", "Sale Rate", "Stock")
    table = ttk.Treeview(body, columns=cols, show="headings", height=10)
    for c in cols:
        table.heading(c, text=c)
        table.column(c, width=170 if c == "Brand Name" else 110, anchor="center")
    table.pack(fill="both", expand=True, padx=10, pady=5)

    for name, company, category, generic, sale, mrp, stock, gst in matches:
        table.insert("", "end", values=(name, company or "", mrp or 0, sale or 0, stock or 0))

    hint = (
        f"{len(matches)} alternative(s) found - double-click a row to use it in this bill."
        if on_select else
        f"{len(matches)} alternative(s) found with this composition."
    )
    tk.Label(
        body, text=hint, bg=theme.SURFACE_WHITE, fg=theme.TABLE_SELECT_FG, font=("Segoe UI", 9, "italic"),
    ).pack(pady=(0, 5))

    if on_select:
        def _pick(event=None):
            selected = table.selection()
            if not selected:
                return
            chosen_name = table.item(selected[0])["values"][0]
            _close()
            on_select(chosen_name)
        table.bind("<Double-1>", _pick)

    ui_style.flat_button(body, "Close", theme.PRIMARY, _close, width=15).pack(pady=5)
    return matches


class GenericMappingManager(tk.Frame):
    """
    Reusable Brand <-> Generic mapping screen. Usage (same pattern as
    SmartAlertsDashboard):
        mapping_ui = GenericMappingManager(self.body)
        mapping_ui.pack(fill="both", expand=True)
    """

    def __init__(self, parent):
        super().__init__(parent, bg="white")
        ensure_tables(DB_NAME)

        self.canonical_name = tk.StringVar()
        self.synonym_text = tk.StringVar()
        self.brand_lookup = tk.StringVar()

        self.selected_group_id = None

        self.create_ui()
        self.load_groups()

    # ---------------- UI ----------------

    def create_ui(self):
        tk.Label(
            self, text="GENERIC MEDICINE AUTO MAPPING (Brand ↔ Generic Link)",
            bg="#1565C0", fg="white", font=("Segoe UI", 18, "bold"), pady=10
        ).pack(fill="x")

        # ---- Brand lookup (top) ----
        lookup = tk.LabelFrame(self, text="Look up a brand's generic family", font=("Segoe UI", 10, "bold"))
        lookup.pack(fill="x", padx=10, pady=10)

        tk.Label(lookup, text="Brand Name").grid(row=0, column=0, padx=5, pady=5)
        entry = tk.Entry(lookup, textvariable=self.brand_lookup, width=30)
        entry.grid(row=0, column=1, padx=5)
        entry.bind("<Return>", lambda e: self.lookup_brand())

        tk.Button(lookup, text="Find Equivalent Brands", bg="#0D47A1", fg="white",
                  command=self.lookup_brand).grid(row=0, column=2, padx=5)

        self.lookupResult = tk.Label(lookup, text="", fg="#333333", justify="left", anchor="w")
        self.lookupResult.grid(row=1, column=0, columnspan=3, sticky="w", padx=5, pady=(0, 5))

        # ---- Two-pane: groups list | synonyms + brands using it ----
        body = tk.Frame(self, bg="white")
        body.pack(fill="both", expand=True, padx=10, pady=5)

        left = tk.LabelFrame(body, text="Generic Groups", font=("Segoe UI", 10, "bold"))
        left.pack(side="left", fill="y", padx=(0, 10))

        self.groupList = tk.Listbox(left, width=28, height=20)
        self.groupList.pack(side="left", fill="y", padx=5, pady=5)
        self.groupList.bind("<<ListboxSelect>>", self.on_group_select)

        gscroll = ttk.Scrollbar(left, orient="vertical", command=self.groupList.yview)
        self.groupList.configure(yscrollcommand=gscroll.set)
        gscroll.pack(side="left", fill="y")

        right = tk.Frame(body, bg="white")
        right.pack(side="left", fill="both", expand=True)

        add_frame = tk.LabelFrame(right, text="Add / Link Synonym", font=("Segoe UI", 10, "bold"))
        add_frame.pack(fill="x", pady=(0, 10))

        tk.Label(add_frame, text="Canonical Name").grid(row=0, column=0, padx=5, pady=5)
        tk.Entry(add_frame, textvariable=self.canonical_name, width=25).grid(row=0, column=1)

        tk.Label(add_frame, text="Synonym / Alt Name").grid(row=0, column=2, padx=5)
        tk.Entry(add_frame, textvariable=self.synonym_text, width=25).grid(row=0, column=3)

        tk.Button(add_frame, text="Add Synonym", bg="#2E7D32", fg="white",
                  command=self.add_synonym_clicked).grid(row=0, column=4, padx=10)

        cols = ("Synonym",)
        self.synonymTable = ttk.Treeview(right, columns=cols, show="headings", height=6)
        self.synonymTable.heading("Synonym", text="Synonyms in this group")
        self.synonymTable.column("Synonym", width=250, anchor="w")
        self.synonymTable.pack(fill="x", pady=(0, 10))

        tk.Label(right, text="Brands currently using this composition:",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")

        cols2 = ("Brand", "Company", "Category", "Stored Generic", "MRP", "Stock")
        self.brandTable = ttk.Treeview(right, columns=cols2, show="headings", height=10)
        for c in cols2:
            self.brandTable.heading(c, text=c)
            self.brandTable.column(c, width=110 if c != "Brand" else 150, anchor="center")
        self.brandTable.pack(fill="both", expand=True)

    # ---------------- DATA ----------------

    def load_groups(self):
        self.groupList.delete(0, tk.END)
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT group_id, canonical_name FROM generic_groups ORDER BY canonical_name")
        self._groups = cur.fetchall()
        con.close()

        for _, name in self._groups:
            self.groupList.insert(tk.END, name)

    def on_group_select(self, event=None):
        selection = self.groupList.curselection()
        if not selection:
            return
        group_id, canonical_name = self._groups[selection[0]]
        self.selected_group_id = group_id
        self.canonical_name.set(canonical_name)

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT synonym_text FROM generic_synonyms WHERE group_id=? ORDER BY synonym_text", (group_id,))
        synonyms = [r[0] for r in cur.fetchall()]
        con.close()

        self.synonymTable.delete(*self.synonymTable.get_children())
        for s in synonyms:
            self.synonymTable.insert("", "end", values=(s,))

        # Show every brand in medicine_master using any synonym in this group
        self.brandTable.delete(*self.brandTable.get_children())
        seen = set()
        for syn in synonyms:
            for row in find_brands_by_generic(syn, use_synonyms=False):
                key = row[0]
                if key in seen:
                    continue
                seen.add(key)
                name, company, category, generic, sale, mrp, stock, gst = row
                self.brandTable.insert("", "end", values=(name, company or "", category or "", generic or "", mrp or 0, stock or 0))

    def add_synonym_clicked(self):
        canonical = self.canonical_name.get().strip()
        synonym = self.synonym_text.get().strip()

        if not canonical or not synonym:
            ui_popups.show_warning(self, "Missing Info", "Enter both a canonical name and a synonym.")
            return

        try:
            add_synonym(canonical, synonym)
        except Exception as e:
            ui_popups.show_error(self, "Database Error", str(e))
            return

        ui_popups.show_info(self, "Saved", f'"{synonym}" linked to "{canonical}".')
        self.synonym_text.set("")
        self.load_groups()

    def lookup_brand(self):
        brand = self.brand_lookup.get().strip()
        if not brand:
            return

        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT generic FROM medicine_master WHERE name=?", (brand,))
        row = cur.fetchone()
        con.close()

        if not row or not row[0]:
            self.lookupResult.config(
                text=f'"{brand}" not found, or has no generic/composition stored yet.',
                fg="#C62828"
            )
            return

        generic_text = row[0]
        equivalents = find_brands_by_generic(generic_text, exclude_name=brand, use_synonyms=True)

        if not equivalents:
            self.lookupResult.config(
                text=f'"{brand}" ({generic_text}) - no other brand with this composition found.',
                fg="#2E7D32"
            )
        else:
            names = ", ".join(e[0] for e in equivalents)
            self.lookupResult.config(
                text=f'"{brand}" ({generic_text}) is also sold as: {names}',
                fg="#0D47A1"
            )
