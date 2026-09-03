"""
medicine_matcher.py
LifeCare Pharmacy ERP - Fuzzy Medicine/Pack-Size Matching

The core idea: OCR doesn't need to read an invoice perfectly. It only
needs to get close enough that fuzzy matching against your EXISTING,
correct Medicine Master data can confidently identify the real medicine
- something OCR alone can never do, because OCR has no idea what
medicines your pharmacy actually stocks.

Uses `rapidfuzz` (C-optimized, token-order-tolerant) when it's
installed, and transparently falls back to Python's built-in difflib
otherwise - so this module still works on a shop's PC that hasn't run
the updated requirements.txt yet, just with the older/slower matcher
until they do. Same behavior everywhere else: every caller just sees
`_similarity(a, b)` return 0.0-1.0, higher is better.

Nothing here ever auto-commits a match. Every function returns ranked
CANDIDATES with a confidence score - the calling UI is responsible for
showing them to a human for confirmation, per the human-in-the-loop
design that makes this reliable in practice.
"""

import re
import sqlite3

from app_paths import DB_NAME

try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz
except ImportError:
    _rapidfuzz_fuzz = None
    import difflib


# ==========================================
# NORMALIZATION
# ==========================================

def _normalize(text):
    """Strips punctuation, collapses whitespace, uppercases - so OCR
    noise like extra spaces, stray periods, or inconsistent casing
    doesn't tank the similarity score before matching even starts."""
    text = re.sub(r"[^A-Za-z0-9\s]", " ", str(text or ""))
    return re.sub(r"\s+", " ", text).strip().upper()


def _similarity(a, b):
    """0.0-1.0 similarity, higher is better. rapidfuzz's token_sort_ratio
    (word-order tolerant - "TAB PARACETAMOL" vs "PARACETAMOL TAB" still
    scores high) when available; difflib's SequenceMatcher ratio
    (order-sensitive) as the fallback when rapidfuzz isn't installed."""
    if not a or not b:
        return 0.0
    if _rapidfuzz_fuzz is not None:
        return _rapidfuzz_fuzz.token_sort_ratio(a, b) / 100.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ==========================================
# DOSAGE-FORM GUARDRAIL (Aug 2026)
# ==========================================
#
# Real bug this fixes: a purchase invoice line "OMEE SYP" (a Syrup)
# fuzzy-matched against the already-existing "OMEE CAP 20'S" (a
# Capsule) at a 0.57 token_sort_ratio score - just above Bulk Import's
# min_score=0.50 - purely because both names share the "OMEE" brand
# prefix. It showed as "~ Possible Match - Verify", the pharmacist
# committed it anyway, and bulk_import.py's commit_rows() then
# overwrote OMEE CAP 20'S's master pack_size with the Syrup's "170ML" -
# corrupting that Capsule's stock-multiplier math on every screen that
# reads pricing_utils.get_pack_multiplier() (Billing/Stock/Purchase/
# Clinic Ledger alike), exactly like the "170ML instead of 3rs/unit"
# report that first surfaced this. A shared brand prefix is common and
# fine (many brands sell the same name as Tab/Cap/Syrup/Inj); what's
# NOT fine is silently linking two different physical products just
# because of it. This guardrail detects an explicit dosage-form word in
# each name and, when both sides name a form and the forms differ,
# heavily penalizes the score - same idea as the company guardrail
# below, applied to product form instead of manufacturer.
_DOSAGE_FORM_KEYWORDS = {
    "TAB": "TABLET", "TABS": "TABLET", "TABLET": "TABLET", "TABLETS": "TABLET",
    "CAP": "CAPSULE", "CAPS": "CAPSULE", "CAPSULE": "CAPSULE", "CAPSULES": "CAPSULE",
    "SYP": "SYRUP", "SYRUP": "SYRUP",
    "INJ": "INJECTION", "INJECTION": "INJECTION", "AMP": "INJECTION",
    "AMPOULE": "INJECTION", "VIAL": "INJECTION",
    "OINT": "OINTMENT", "OINTMENT": "OINTMENT",
    "CREAM": "CREAM",
    "LOTION": "LOTION",
    "DROP": "DROPS", "DROPS": "DROPS",
    "GEL": "GEL",
    "SPRAY": "SPRAY",
    "POWDER": "POWDER", "POW": "POWDER",
}

# Multiplier applied when both names declare a dosage form and the
# forms disagree - severe enough to push almost any realistic
# brand-prefix-only match (the OMEE SYP/OMEE CAP case scored 0.57) back
# under a sane min_score, without needing per-caller threshold changes.
_DOSAGE_FORM_MISMATCH_PENALTY = 0.2


def _dosage_form(norm_text):
    """Returns the canonical dosage-form label found in `norm_text`
    (already _normalize()'d - uppercase, whitespace-separated tokens),
    or None if no recognized form keyword appears anywhere in it."""
    for token in norm_text.split():
        form = _DOSAGE_FORM_KEYWORDS.get(token)
        if form:
            return form
    return None


# ==========================================
# BRAND-ROOT GUARDRAIL (2026-08-31)
# ==========================================
#
# Real incident this fixes: a purchase invoice line "SPASMONIL PLUS
# TAB" scored high enough against the already-existing, completely
# unrelated "NICIP PLUS TAB" to show as "~ Possible Match" - purely
# because token_sort_ratio (see _similarity()'s docstring) treats a
# name as an unordered BAG of tokens and rewards every shared token
# equally. "PLUS" and "TAB" are generic marketing/packaging words that
# appear on hundreds of unrelated Indian pharma brands with zero
# brand-identifying meaning - two tokens out of three matching pushed
# the score up even though the one token that actually IDENTIFIES the
# brand ("SPASMONIL" vs "NICIP") shares no similarity at all. Same root
# cause as the dosage-form guardrail above (OMEE SYP/OMEE CAP), just
# triggered by a shared FILLER word instead of a shared BRAND prefix.
#
# Fix: strip known filler words from both names, then require the
# remaining "core" tokens (the part that actually names the brand) to
# share at least SOME similarity - if what's left is completely
# unrelated, the raw token_sort_ratio score is heavily penalized
# regardless of how many filler words happened to coincide. Order-
# insensitive by design (core tokens are compared as a set, not a
# string), so this does not undo _similarity()'s own token-order
# tolerance for genuinely reordered OCR text like "TAB PARACETAMOL" vs
# "PARACETAMOL TAB" (both reduce to the same single core token).
#
# Deliberately NOT included here: short suffix letters like "P", "SP",
# "D", "N", "SR", "XL", "DS", "CR", "LA", "MR" - those usually DO mark a
# real formulation difference (see "ALDIGESIC P" vs "ALDIGESIC-SP",
# already handled by the plain CONFIDENT_MATCH_SCORE threshold below),
# so treating them as meaningless filler would hide a genuine
# difference instead of a false one.
_BRAND_FILLER_TOKENS = set(_DOSAGE_FORM_KEYWORDS.keys()) | {
    "PLUS", "FORTE", "GOLD", "MAX", "NEW", "EXTRA", "TOTAL", "ADVANCE",
}

# Same severity as the dosage-form penalty - enough to push a
# filler-word-only coincidence (the SPASMONIL/NICIP case scored 0.67
# before this fix) back under a sane min_score.
_BRAND_ROOT_MISMATCH_PENALTY = 0.2

# Two core tokens below this pairwise similarity are treated as
# "unrelated" rather than "the same brand word with an OCR typo".
_BRAND_ROOT_SIMILARITY_FLOOR = 0.7


def _core_tokens(norm_text):
    """`norm_text`'s tokens with generic filler words removed - what's
    left is (usually) the actual brand name, the part of a medicine's
    name that identifies WHICH drug this is, as opposed to what form/
    variant it comes in."""
    return {t for t in norm_text.split() if t not in _BRAND_FILLER_TOKENS}


def _core_tokens_related(query_core, row_core):
    """True if any token in query_core is a close match (or exact
    substring either way, for a truncated OCR read) to any token in
    row_core - False only when EVERY pairing is unrelated."""
    if not query_core or not row_core:
        # Nothing left to compare after stripping filler (e.g. a name
        # that's ALL filler words) - can't call this a mismatch off of
        # no evidence either way, so let the plain score stand.
        return True
    for qt in query_core:
        for rt in row_core:
            if qt == rt or qt.startswith(rt) or rt.startswith(qt):
                return True
            if _similarity(qt, rt) >= _BRAND_ROOT_SIMILARITY_FLOOR:
                return True
    return False


# ==========================================
# MEDICINE NAME MATCHING
# ==========================================

def find_medicine_matches(ocr_name, ocr_company=None, db_name=None, top_n=5, min_score=0.55):
    """
    (docstring அப்படியே வெச்சுக்கோங்க)
    """
    db_name = db_name or DB_NAME
    norm_query = _normalize(ocr_name)
    if not norm_query:
        return []

    norm_query_company = _normalize(ocr_company) if ocr_company else None
    query_form = _dosage_form(norm_query)

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT DISTINCT name, company, pack_size, gst, generic FROM medicine_master")
    rows = cur.fetchall()
    con.close()

    scored = []
    for name, company, pack_size, gst, generic in rows:
        norm_name = _normalize(name)
        score = _similarity(norm_query, norm_name)

        if norm_name and (norm_name.startswith(norm_query) or norm_query.startswith(norm_name)):
            score = max(score, 0.85)

        if generic:
            generic_score = _similarity(norm_query, _normalize(generic))
            score = max(score, generic_score * 0.8)

        # ─── Dosage-Form Guardrail: Syrup vs Capsule/Tablet/Injection
        # etc. தப்பா merge ஆகாம தடுக்க (see _dosage_form()'s docstring -
        # this is the exact fix for the real "OMEE SYP" -> "OMEE CAP
        # 20'S" mismatch that corrupted that Capsule's pack_size) ───
        row_form = _dosage_form(norm_name)
        if query_form and row_form and query_form != row_form:
            score = score * _DOSAGE_FORM_MISMATCH_PENALTY

        # ─── Brand-Root Guardrail: "SPASMONIL PLUS TAB" invoice line
        # தப்பா "NICIP PLUS TAB"-ஓட merge ஆகாம தடுக்க (see
        # _core_tokens()'s docstring - PLUS/TAB இரண்டும் பொதுவான filler
        # words, brand பெயரே வேற) ───
        query_core = _core_tokens(norm_query)
        row_core = _core_tokens(norm_name)
        if not _core_tokens_related(query_core, row_core):
            score = score * _BRAND_ROOT_MISMATCH_PENALTY

        # ─── Company Guardrail: Omee/Omez தப்பா merge ஆகாம தடுக்க ───
        if norm_query_company:
            norm_row_company = _normalize(company) if company else ""
            if norm_row_company and norm_row_company != norm_query_company:
                score = score * 0.3   # company வேற ஆனா score கடுமையா குறையும்

        if score >= min_score:
            scored.append((name, company, pack_size, gst, round(score, 3)))

    scored.sort(key=lambda r: r[-1], reverse=True)

    seen = set()
    deduped = []
    for r in scored:
        if r[0] not in seen:
            seen.add(r[0])
            deduped.append(r)

    return deduped[:top_n]


# ==========================================
# PACK SIZE MATCHING
# ==========================================

def find_pack_size_matches(ocr_pack_text, medicine_name=None, db_name=None, top_n=3):
    """
    Suggests the best pack-size match. Strongly biased toward whatever
    pack size THIS SAME medicine has used in previous purchases (pack
    size for a given brand essentially never changes between orders -
    "Dolo 650" is always "15'S", regardless of what today's invoice's
    print quality looks like), falling back to fuzzy-matching against
    every pack size seen anywhere in the catalog.

    Returns: list of (pack_size, score 0-1, reason).
    """
    db_name = db_name or DB_NAME
    norm_query = _normalize(ocr_pack_text)

    con = sqlite3.connect(db_name)
    cur = con.cursor()

    candidates = []
    if medicine_name:
        cur.execute(
            "SELECT DISTINCT pack_size FROM medicine_master "
            "WHERE name=? AND pack_size IS NOT NULL AND pack_size<>''",
            (medicine_name,)
        )
        own_packs = [r[0] for r in cur.fetchall()]
        for p in own_packs:
            score = 0.9 if not norm_query else max(0.9, _similarity(norm_query, _normalize(p)))
            candidates.append((p, round(score, 3), "previously used for this medicine"))

    cur.execute("SELECT DISTINCT pack_size FROM medicine_master WHERE pack_size IS NOT NULL AND pack_size<>''")
    all_packs = [r[0] for r in cur.fetchall()]
    con.close()

    seen = {c[0] for c in candidates}
    if norm_query:
        for p in all_packs:
            if p in seen:
                continue
            score = _similarity(norm_query, _normalize(p))
            if score >= 0.5:
                candidates.append((p, round(score, 3), "seen elsewhere in catalog"))
                seen.add(p)

    candidates.sort(key=lambda c: c[1], reverse=True)
    return candidates[:top_n]


# ==========================================
# COMBINED: ONE ROW -> BEST GUESS + ALTERNATES
# ==========================================

def match_invoice_row(ocr_name, ocr_pack_text=None, db_name=None):
    """
    Convenience wrapper for a single OCR'd invoice line: returns the
    best medicine match (or None if nothing scored high enough) plus
    its alternates, and the best pack-size match biased to that
    medicine. This is what a review-grid row handler should call.
    """
    medicine_matches = find_medicine_matches(ocr_name, db_name=db_name)
    best_medicine = medicine_matches[0] if medicine_matches else None
    best_medicine_name = best_medicine[0] if best_medicine else None

    pack_matches = find_pack_size_matches(ocr_pack_text, medicine_name=best_medicine_name, db_name=db_name)

    return {
        "best_medicine": best_medicine,          # (name, company, pack_size, gst, score) or None
        "medicine_alternates": medicine_matches,  # includes best_medicine as [0]
        "best_pack": pack_matches[0] if pack_matches else None,
        "pack_alternates": pack_matches,
    }


# RAPIDFUZZ STATUS: wired in above (see _similarity) as of Aug 2026.
# `RapidFuzz==3.14.5` is listed in requirements.txt - run
# `pip install -r requirements.txt` on any shop PC that hasn't picked it
# up yet. Until then this file keeps working via the difflib fallback,
# just with the older/slower matcher.