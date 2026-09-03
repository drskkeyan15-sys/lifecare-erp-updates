"""
ddi_checker.py
LifeCare Pharmacy ERP - Drug-Drug Interaction (DDI) Safety Checker
FRAMEWORK (Aug 2026).

IMPORTANT - READ BEFORE RELYING ON THIS FOR REAL DISPENSING DECISIONS:
This module ships with a SMALL, EXPLICITLY NON-COMPREHENSIVE reference
list of well-known, publicly documented severe drug interactions
(SEED_INTERACTIONS below) - NOT a licensed/verified clinical drug
interaction database. It exists to demonstrate the mechanism (cart
cross-check -> warning popup -> pharmacist acknowledgment) that a real
dataset would plug into, not to be treated as a complete or authoritative
safety check. A missing warning here does NOT mean a combination is
safe, and an interaction shown here should still be judged by the
pharmacist/doctor, not treated as the final word. Before relying on this
for real patient-safety decisions, replace/extend SEED_INTERACTIONS with
a licensed, verified interaction database (e.g. RxNorm-based data or a
commercial DDI API), reviewed and curated by a doctor or pharmacist for
this pharmacy's own formulary.

Design, matching the rest of this codebase's "resolver module" pattern
(see generic_mapping.py's own module docstring):
  - Each medicine's generic/composition is resolved via medicine_master.
    composition_id -> composition_master.composition_name (the SAME
    curated, ~1100-row canonical name registry already used for the
    Schedule H1 habit-forming check in billing_repository.
    get_habit_forming_names()) - falling back to medicine_master's own
    free-text `generic` column only when composition_id isn't set for
    that medicine, so an item never silently drops out of checking just
    because it hasn't been linked to composition_master yet.
  - Interaction rules are matched by normalized substring containment
    (same normalize() convention as generic_mapping.py) in EITHER
    direction, so a rule term like "Warfarin" matches a resolved
    composition of "Warfarin Sodium" (or similar) without needing an
    exact string match.
"""

import sqlite3

from app_paths import DB_NAME
import generic_mapping

# ==========================================================
# REFERENCE DATASET - NOT COMPREHENSIVE. See module docstring above.
# Severity is currently always "Severe" (the only tier this framework
# acts on - see check_cart_interactions()/billing.py's warning popup);
# a future round could add "Moderate"/"Minor" tiers with a softer,
# non-blocking UI treatment instead of this hard acknowledgment gate.
# ==========================================================
SEED_INTERACTIONS = [
    ("Warfarin", "Aspirin", "Severe", "Increased bleeding risk - additive anticoagulant/antiplatelet effect."),
    ("Warfarin", "Ibuprofen", "Severe", "Increased bleeding risk - NSAID displaces warfarin and impairs platelet function."),
    ("Warfarin", "Diclofenac", "Severe", "Increased bleeding risk - NSAID interaction with an anticoagulant."),
    ("Warfarin", "Metronidazole", "Severe", "Metronidazole inhibits warfarin metabolism - markedly increased INR/bleeding risk."),
    ("Warfarin", "Amiodarone", "Severe", "Amiodarone inhibits warfarin metabolism - significantly increased INR/bleeding risk."),
    ("Warfarin", "Fluconazole", "Severe", "Fluconazole inhibits warfarin metabolism - increased bleeding risk."),
    ("Methotrexate", "Ibuprofen", "Severe", "NSAIDs reduce methotrexate clearance - risk of methotrexate toxicity."),
    ("Methotrexate", "Diclofenac", "Severe", "NSAIDs reduce methotrexate clearance - risk of methotrexate toxicity."),
    ("Sildenafil", "Nitroglycerin", "Severe", "Severe, potentially fatal hypotension when combined with nitrates."),
    ("Sildenafil", "Isosorbide", "Severe", "Severe, potentially fatal hypotension when combined with nitrates."),
    ("Simvastatin", "Clarithromycin", "Severe", "Markedly increased statin levels - risk of rhabdomyolysis."),
    ("Simvastatin", "Erythromycin", "Severe", "Increased statin levels - risk of rhabdomyolysis."),
    ("Digoxin", "Amiodarone", "Severe", "Amiodarone raises digoxin levels - risk of digoxin toxicity."),
    ("Spironolactone", "Enalapril", "Severe", "Risk of dangerous hyperkalaemia (ACE inhibitor + potassium-sparing diuretic)."),
    ("Spironolactone", "Losartan", "Severe", "Risk of dangerous hyperkalaemia (ARB + potassium-sparing diuretic)."),
    ("Tramadol", "Sertraline", "Severe", "Risk of serotonin syndrome (opioid + SSRI)."),
    ("Tramadol", "Fluoxetine", "Severe", "Risk of serotonin syndrome (opioid + SSRI)."),
    ("Clopidogrel", "Omeprazole", "Severe", "Omeprazole may reduce clopidogrel's antiplatelet effect via CYP2C19 inhibition."),
    ("Theophylline", "Ciprofloxacin", "Severe", "Ciprofloxacin raises theophylline levels - risk of theophylline toxicity."),
    ("Lithium", "Ibuprofen", "Severe", "NSAIDs reduce lithium clearance - risk of lithium toxicity."),
]


def ensure_table(db_name=None):
    """Creates drug_interactions (id, generic_a, generic_b, severity,
    description) if missing and seeds it with SEED_INTERACTIONS on a
    first run only (same "seed once, never overwrite user edits again"
    convention as generic_mapping.ensure_tables()'s _SEED_SYNONYM_GROUPS -
    a pharmacist who edits/removes a seeded row later won't have it
    silently reappear on the next app start)."""
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS drug_interactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generic_a TEXT NOT NULL,
            generic_b TEXT NOT NULL,
            severity TEXT NOT NULL,
            description TEXT
        )
    """)
    cur.execute("SELECT COUNT(*) FROM drug_interactions")
    if cur.fetchone()[0] == 0:
        for a, b, severity, desc in SEED_INTERACTIONS:
            cur.execute(
                "INSERT INTO drug_interactions(generic_a, generic_b, severity, description) VALUES (?,?,?,?)",
                (a, b, severity, desc)
            )
    con.commit()
    con.close()


def _medicine_generics(names, db_name=None):
    """{medicine_name: generic_text} for every name in `names` that has
    a resolvable generic - prefers composition_master.composition_name
    (the curated canonical name, via medicine_master.composition_id),
    falls back to medicine_master's own free-text `generic` column when
    a medicine has no composition_id set yet. A medicine with neither is
    simply omitted from the returned dict - nothing to check it against."""
    names = list(dict.fromkeys(names))  # de-dup, keep order
    if not names:
        return {}

    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    result = {}
    try:
        for name in names:
            cur.execute("""
                SELECT cm.composition_name, mm.generic
                FROM medicine_master mm
                LEFT JOIN composition_master cm ON cm.composition_id = mm.composition_id
                WHERE mm.name = ? LIMIT 1
            """, (name,))
            row = cur.fetchone()
            if not row:
                continue
            composition_name, free_text_generic = row
            generic_text = composition_name or free_text_generic
            if generic_text:
                result[name] = generic_text
    finally:
        con.close()
    return result


def _matches(rule_term, generic_text):
    a = generic_mapping.normalize(rule_term)
    b = generic_mapping.normalize(generic_text)
    return bool(a) and bool(b) and (a in b or b in a)


def check_cart_interactions(medicine_names, db_name=None):
    """Pairwise-checks every DISTINCT pair among `medicine_names` (an
    iterable of medicine names, e.g. everything currently in a Billing
    cart) against the drug_interactions reference table. Returns a list
    of (medicine_1, medicine_2, severity, description) tuples - empty if
    fewer than 2 medicines have a resolvable generic, or no rule
    matches. See this module's own docstring for the "reference only,
    not comprehensive" caveat - an empty result does NOT mean the
    combination is safe, only that it isn't in this small seed list."""
    ensure_table(db_name)

    names = list(dict.fromkeys(n for n in medicine_names if n))
    if len(names) < 2:
        return []

    generics = _medicine_generics(names, db_name)
    if len(generics) < 2:
        return []

    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT generic_a, generic_b, severity, description FROM drug_interactions")
    rules = cur.fetchall()
    con.close()

    findings = []
    seen_pairs = set()
    resolvable = [n for n in names if n in generics]
    for i in range(len(resolvable)):
        for j in range(i + 1, len(resolvable)):
            med_i, med_j = resolvable[i], resolvable[j]
            gen_i, gen_j = generics[med_i], generics[med_j]
            for a, b, severity, description in rules:
                if (_matches(a, gen_i) and _matches(b, gen_j)) or (_matches(a, gen_j) and _matches(b, gen_i)):
                    pair_key = (tuple(sorted((med_i, med_j))), a, b)
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    findings.append((med_i, med_j, severity, description))
    return findings
