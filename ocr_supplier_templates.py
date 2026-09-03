"""
ocr_supplier_templates.py
LifeCare Pharmacy ERP - Supplier-specific OCR column templates

BUG CLASS THIS FILE EXISTS TO REDUCE: the same Field Mapping Error class
ocr_table_reconstruction.py already targets (a correctly-read value
landing in the wrong column), but from a different angle. That file has
to GUESS a cell's identity from its shape alone (date-shaped -> Expiry,
percent-shaped -> GST%, ...) because it knows nothing about the specific
invoice layout in front of it. That guessing breaks whenever a
supplier's real column order doesn't match the assumption baked into the
guessing heuristics (verified against real invoices during calibration -
see BUG_LOG entries for the brace-fused-HSN and MRP/Batch-merge cases).

In practice a pharmacy re-orders from the same handful of suppliers
over and over. This file lets the pharmacist calibrate ONCE, per
supplier, by typing out that supplier's exact printed column order
(e.g. "name, pack, mfr, hsn, batch, exp, mrp, qty, free, rate, gst,
amount"), keyed by that supplier's GSTIN (a fixed 15-character code
printed on every invoice they send). When a scanned invoice's GSTIN
matches a saved template, cells get mapped POSITIONALLY using that known
order instead of guessed from shape - far more reliable when it applies.
Unknown suppliers (no matching template) are untouched: bulk_import.py
falls back to ocr_table_reconstruction.extract_invoice_row_fields() for
those exactly as before.

Pure logic, no tkinter/pytesseract/sqlite-schema-writing here (sqlite3
DML only) - testable without a live GUI or Tesseract install, same
reasoning as ocr_table_reconstruction.py.
"""

import re
import sqlite3

from app_paths import DB_NAME
import ocr_table_reconstruction

# The fixed vocabulary a pharmacist can type when calibrating a
# template's column order. "sno" and "skip" are deliberately included
# even though they don't map to a row_dict field - most invoices print a
# serial-number column, and some print a column (Pack/Free-text remarks/
# a physical divider column) this app has no use for; both need an
# explicit "there's a column here but ignore it" role rather than
# forcing the pharmacist to either omit it (breaking positional
# alignment for every column after it) or pick an unrelated real role
# for it (silently corrupting a real field).
VALID_FIELD_ROLES = (
    "sno", "name", "pack", "mfr", "hsn", "batch", "exp",
    "mrp", "qty", "free", "rate", "disc", "gst", "amount", "skip",
)

ROLE_LEGEND = (
    "sno = serial no (ignored)\n"
    "name = medicine name\n"
    "pack = pack size (10'S / 100ML / 10GM)\n"
    "mfr = company / manufacturer code\n"
    "hsn = HSN code\n"
    "batch = batch no\n"
    "exp = expiry (MM/YY)\n"
    "mrp = MRP\n"
    "qty = quantity\n"
    "free = free quantity\n"
    "rate = purchase rate\n"
    "disc = discount %\n"
    "gst = GST %\n"
    "amount = line total\n"
    "skip = a printed column this app doesn't need"
)

# A standard Indian GSTIN is exactly 15 characters: 2-digit state code,
# 10-character PAN (5 letters, 4 digits, 1 letter), 1 digit (entity
# code), 1 fixed letter ('Z' by GST rule), 1 alphanumeric checksum char.
# The checksum position is matched loosely ([A-Za-z\d]) rather than
# validated - this is a SHAPE filter to find GSTIN-looking tokens in
# noisy OCR text, not a real checksum verifier, and a wrong checksum
# character is exactly the kind of single-character OCR misread
# find_matching_template()'s tolerance below is meant to absorb anyway.
_GSTIN_SHAPE_RE = re.compile(r"^\d{2}[A-Za-z]{5}\d{4}[A-Za-z]\d[A-Za-z][A-Za-z\d]$")

# Reused from ocr_table_reconstruction.py rather than redefined, so a
# future tweak to what "looks like a GST%/date cell" only has to happen
# in one place.
_GST_SHAPE_RE = ocr_table_reconstruction._GST_RE
_DATE_SHAPE_RE = ocr_table_reconstruction._DATE_RE


def parse_column_order(text):
    """
    Parses a pharmacist-typed comma-separated column order into a
    validated list of role strings. Raises ValueError naming the exact
    bad token instead of silently dropping or guessing it - a wrong role
    name here would silently mismap every future invoice from this
    supplier, which is a worse outcome than just refusing to save.
    """
    roles = [r.strip().lower() for r in text.split(",") if r.strip()]
    if not roles:
        raise ValueError("Column order can't be empty.")
    for r in roles:
        if r not in VALID_FIELD_ROLES:
            raise ValueError(
                f"'{r}' is not a valid column role.\n\nValid roles:\n{ROLE_LEGEND}"
            )
    return roles


def extract_gstin(words, exclude_gstin=None):
    """
    words: a list of OCR word-dicts (same shape ocr_table_reconstruction
    uses - dicts with a "text" key) OR a plain list of strings. Searches
    for a token shaped like a 15-character Indian GSTIN.

    Deliberately looks at the FULL word list for the page, not just the
    reconstructed product-table rows - the GSTIN is printed in the
    supplier's letterhead, above where the product table itself starts,
    so ocr_table_reconstruction's table-bounds trimming would exclude it
    entirely if this only looked at table_rows.

    exclude_gstin: the pharmacy's OWN GSTIN (from Settings), if known.
    A properly formatted GST invoice legally has to print BOTH the
    supplier's GSTIN and the buyer's (this pharmacy's own) GSTIN -
    verified against a real invoice (Dhanalakshmi Medical Agencies) where
    both appear in the header area. Without this filter, a page where the
    buyer's own GSTIN happens to be read before the actual supplier's
    could match this pharmacy's own registration against a supplier
    template instead of the real supplier - silently applying the wrong
    column layout. Any candidate matching exclude_gstin (within the same
    1-2 character OCR-misread tolerance used elsewhere) is skipped in
    favour of continuing to search for a different one.
    """
    exclude_gstin = (exclude_gstin or "").upper() or None

    def _is_own_gstin(candidate):
        return exclude_gstin is not None and _hamming_distance(candidate, exclude_gstin) <= 2

    for w in words:
        text = w["text"] if isinstance(w, dict) else w
        candidate = re.sub(r"[^A-Za-z0-9]", "", text)
        if len(candidate) == 15 and _GSTIN_SHAPE_RE.match(candidate):
            if not _is_own_gstin(candidate.upper()):
                return candidate.upper()
            continue
        # The printed "GSTIN:"/"GST No:" label sometimes gets fused onto
        # the value with no space by OCR (verified against a real
        # invoice: "GSTIN:33AYHPM7335D1ZK" and "No33AYHPM7335D1ZK" both
        # came back as single tokens) - requiring the WHOLE candidate to
        # be exactly 15 characters misses these entirely. Scanning every
        # 15-character window of a slightly-too-long candidate for a
        # GSTIN-shaped substring catches this regardless of the exact
        # label text, bounded to a modest extra length (12 chars - more
        # than any realistic label) so this can't end up scanning an
        # unrelated long sentence looking for a coincidental match.
        elif 15 < len(candidate) <= 15 + 12:
            for i in range(len(candidate) - 15 + 1):
                window = candidate[i:i + 15]
                if _GSTIN_SHAPE_RE.match(window) and not _is_own_gstin(window.upper()):
                    return window.upper()
    return None


def extract_header_words(words, header_cutoff_ratio=0.28, min_confidence=40):
    """
    Returns cleaned, uppercased, letters-only text for words printed near
    the top of the invoice (the supplier's letterhead area), confident
    enough to trust.

    Why this exists: extract_gstin() needs its 15-character token to
    survive OCR with its digit/letter SHAPE intact (2 digits, 5 letters,
    4 digits, ...) - a single digit misread as a letter anywhere in that
    run (very common on the small GSTIN print, e.g. "4991" OCR'd as
    "AGSI") breaks the shape match entirely, not just a character or two.
    A supplier's NAME, by contrast, is almost always printed in large,
    bold letterhead text that OCRs with much higher confidence (95+ in
    practice) than the small GSTIN line beside it. This gives
    find_template_by_name() a second, independent way to identify a
    known supplier when the GSTIN read is too damaged to use at all.

    header_cutoff_ratio is relative to the tallest word's bottom edge on
    the page (a proxy for page height, since we don't have the source
    image here) - 0.28 comfortably covers the letterhead/address block
    while staying above where the product table itself starts (verified
    against real invoice OCR dumps: table rows start around 30-35% down
    the page).
    """
    dict_words = [w for w in words if isinstance(w, dict) and "top" in w]
    if not dict_words:
        return []
    page_height = max(w["top"] + w.get("height", 0) for w in dict_words)
    if page_height <= 0:
        return []
    # A floor (not just a ratio of page_height) guards against a sparse
    # word list - e.g. a tightly-cropped photo, or a page where the
    # product table itself failed to OCR - collapsing the header window
    # to near-nothing and excluding real letterhead words.
    cutoff = max(page_height * header_cutoff_ratio, 300)

    out = []
    for w in dict_words:
        if w["top"] > cutoff:
            continue
        try:
            conf = float(w.get("conf", 0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf < min_confidence:
            continue
        text = re.sub(r"[^A-Za-z]", "", w.get("text", "")).upper()
        if len(text) >= 2:
            out.append(text)
    return out


def find_template_by_name(header_words, templates, min_ratio=0.7):
    """
    Fallback identification when extract_gstin()/find_matching_template()
    come up empty - matches a template's supplier_name against the
    invoice's letterhead text instead of its GSTIN.

    Only counts words of length >= 3 from the supplier name (skips
    "of"/"& " type filler) and tolerates a single-character OCR misread
    per word (same reasoning as find_matching_template's GSTIN
    tolerance). Requires at least min_ratio of the name's significant
    words to be found before trusting the match - a name like "Agency"
    alone is too generic to anchor on, but "Srinivasa" + "Agency" both
    matching is a strong signal.
    """
    if not header_words or not templates:
        return None

    header_set = set(header_words)
    best, best_ratio = None, 0.0
    for row in templates:
        supplier_name = row[1]
        name_tokens = [t.upper() for t in re.findall(r"[A-Za-z]+", supplier_name) if len(t) >= 3]
        if not name_tokens:
            continue

        matched = 0
        for tok in name_tokens:
            if tok in header_set:
                matched += 1
                continue
            for h in header_words:
                if len(h) == len(tok) and _hamming_distance(h, tok) <= 1:
                    matched += 1
                    break

        ratio = matched / len(name_tokens)
        if ratio > best_ratio:
            best, best_ratio = row, ratio

    return best if best_ratio >= min_ratio else None


def _hamming_distance(a, b):
    if len(a) != len(b):
        return max(len(a), len(b))
    return sum(1 for x, y in zip(a, b) if x != y)


def find_matching_template(gstin, templates, max_distance=2):
    """
    templates: list of (id, supplier_name, gstin, column_order) rows,
    e.g. from load_templates().

    Tolerates up to `max_distance` character differences from OCR
    misreads (O/0, I/1, S/5, etc.) rather than requiring an exact match -
    a GSTIN is a fixed 15-character code, so a couple of character-level
    OCR errors on an otherwise-real GSTIN is far more likely than a
    genuinely different, unrelated supplier's GSTIN coincidentally
    landing within 2 characters of a saved one.
    """
    if not gstin:
        return None
    best, best_dist = None, max_distance + 1
    for row in templates:
        stored_gstin = row[2]
        dist = _hamming_distance(gstin, stored_gstin)
        if dist < best_dist:
            best, best_dist = row, dist
    return best


def load_templates(db_name=None):
    con = sqlite3.connect(db_name or DB_NAME)
    cur = con.cursor()
    cur.execute("SELECT id, supplier_name, gstin, column_order FROM ocr_supplier_templates ORDER BY supplier_name")
    rows = cur.fetchall()
    con.close()
    return rows


def save_template(supplier_name, gstin, column_order_text, db_name=None):
    """
    Validates both the supplier name/GSTIN and the column order BEFORE
    touching the database (parse_column_order raises ValueError on a bad
    role name), so an invalid template can never reach storage and
    silently mismap a future invoice. INSERT OR REPLACE on the UNIQUE
    gstin column means re-saving the same supplier's GSTIN updates its
    existing template rather than creating a duplicate.
    """
    roles = parse_column_order(column_order_text)
    supplier_name = (supplier_name or "").strip()
    gstin = re.sub(r"[^A-Za-z0-9]", "", gstin or "").upper()

    if not supplier_name:
        raise ValueError("Supplier name can't be empty.")
    if len(gstin) != 15:
        raise ValueError(f"GSTIN must be exactly 15 characters (got {len(gstin)}: '{gstin}').")

    con = sqlite3.connect(db_name or DB_NAME)
    cur = con.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO ocr_supplier_templates(supplier_name, gstin, column_order, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (supplier_name, gstin, ",".join(roles)),
    )
    con.commit()
    con.close()


def delete_template(template_id, db_name=None):
    con = sqlite3.connect(db_name or DB_NAME)
    cur = con.cursor()
    cur.execute("DELETE FROM ocr_supplier_templates WHERE id=?", (template_id,))
    con.commit()
    con.close()


_NIL_DASH_RE = re.compile(r"^-{1,2}$")


def _clean_cell(c):
    """Reuses ocr_table_reconstruction's noise-stripping regexes rather
    than redefining them, so a future fix to what counts as OCR noise
    only has to happen in one place.

    A lone "-" (or "--") is treated as meaningful data, not noise, and
    passed through unchanged BEFORE the shared noise filters run - real
    invoice case (Dhanalakshmi Medical Agencies): a free-quantity extra
    line prints "-" for Rate and Taxable Value to mean "nil/zero", not a
    stray printed divider. The shared _CELL_NOISE_RE would otherwise
    delete it outright (it's built to catch exactly this shape of glyph
    when it IS pixel noise elsewhere), collapsing the cell count and
    corrupting every field's positional alignment after it.
    """
    stripped = c.strip()
    if _NIL_DASH_RE.match(stripped):
        return stripped
    c = ocr_table_reconstruction._EDGE_NOISE_RE.sub("", stripped).strip()
    if not c or ocr_table_reconstruction._CELL_NOISE_RE.match(c.strip()):
        return ""
    return c


_FUSED_CELL_RE = re.compile(r"^(\d[\d.]*)[|/]+(.+)$")


def _split_fused_cell(c):
    """
    Some dot-matrix invoices print two DIFFERENT columns' values pressed
    directly against each other with no space at all, joined only by a
    printed divider character - verified against a real invoice
    (Dhanalakshmi Medical Agencies) where Tesseract read "30049011|NAV"
    (HSN + Mfr) and "60.00|A1774" (MRP + Batch No.) as single words.
    Because there's no gap between them at all, table reconstruction's
    gap-based cell-splitting can never separate them - they arrive here
    as one string.

    Deliberately narrow: only splits when the cell STARTS with a purely
    numeric-looking prefix (a code like "30049011" or a price like
    "60.00") immediately followed by "|" or "/" - the two real fusion
    patterns actually seen (HSN|Mfr, MRP|Batch). A cell that starts with
    a LETTER is left completely untouched even if it contains "/" or "|"
    - real regression (2026-08-10): a genuine batch code "w/92026/-2"
    got blindly split into three meaningless pieces by an earlier,
    broader version of this function that split on every "/" or "|"
    regardless of position, since there's no reliable way to tell a
    literal printed slash inside a real value apart from a fusion join
    once the cell doesn't start with a clean numeric prefix.

    Also does not touch a cell already shaped like a genuine date
    ("08/26") or GST% ("5%") - both legitimately contain '/' and are
    anchored separately by shape elsewhere.
    """
    if _DATE_SHAPE_RE.match(c) or _GST_SHAPE_RE.match(c.replace(" ", "")):
        return [c]
    m = _FUSED_CELL_RE.match(c)
    if not m:
        return [c]
    first, rest = m.group(1), m.group(2)
    if not re.search(r"[A-Za-z0-9]", rest):
        return [c]
    return [first, rest]


_NAME_CONTINUATION_RE = re.compile(r"^\d+\s*MG$", re.IGNORECASE)

# Dosage-form / product-type words that appear as the LAST word of a
# medicine name on essentially every real invoice seen so far (INJ, CAP,
# TAB, OIN, ...) - curated from actual product names across every
# supplier calibrated this session (ALK KETROL INJ, PREGABANYL M CAP,
# MOOV OIN, FEVASTIN INJ, WYSOLONE ... TAB, ...). Deliberately a fixed,
# specific whitelist rather than "any alphabetic word" - a garbled pack
# size ("EACH" misread as "ACH") is ALSO purely alphabetic once garbled,
# and a real regression (2026-08-10) showed that guessing "no digit in
# it = must be part of the name" swallows that pack value into the name
# instead. None of these words plausibly collide with a pack size, a
# manufacturer code, or any other role's real values.
_PHARMA_NAME_SUFFIX_WORDS = {
    "TAB", "TABS", "CAP", "CAPS", "INJ", "SYP", "OIN", "OINT", "GEL",
    "CREAM", "DROP", "DROPS", "SPRAY", "LOTION", "POWDER", "SOLN",
    "SUSP", "AMP", "VIAL", "LIQUID", "ROLL", "SET", "BANDAGE", "WIPES",
    "SACHET",
}


def _looks_like_name_continuation(cell):
    if _NAME_CONTINUATION_RE.match(cell) or cell.upper() in _PHARMA_NAME_SUFFIX_WORDS:
        return True
    # BUG FIX (Aug 2026, real invoice - SRINIVASA AGENCY): a qualifier/
    # dosage phrase bucketed TOGETHER with its trailing dosage-form word
    # by table reconstruction (e.g. "0.5MG TAB", "DT 250 TAB", "MONT
    # JUNIOR TAB", "ALU/ALU TAB") arrives here as ONE multi-word cell,
    # not the bare "TAB"/"CAP" the checks above expect - but it's still
    # just the tail of the product name, not a real Pack/Mfr/HSN value.
    # Verified against this invoice's real OCR debug data
    # (M-BETSONE 0.5MG TAB): left unmerged, "0.5MG TAB" landed in the
    # Pack Size role and shifted EVERY field after it by one column for
    # that row (Mfr got the real Pack value, HSN got the real Mfr value,
    # Batch got the real HSN, MRP got the real Batch, Qty got the real
    # MRP, Rate got the real Qty, GST got the real Rate - the whole row
    # was wrong). Checking only the cell's LAST word against the same
    # suffix list used for a bare "TAB" cell catches this whole family.
    words = cell.replace("/", " ").split()
    return bool(words) and words[-1].upper() in _PHARMA_NAME_SUFFIX_WORDS


def _merge_split_name_cells(column_order, cells, max_extra=2):
    """
    Real invoice case (Dhanalakshmi Medical Agencies, also used by Ramesh
    Distributors - same invoice layout): "MOOV OIN" and "WYSOLONE 10MG
    TAB" each OCR as MULTIPLE separate reconstructed cells instead of
    one - table reconstruction's column-boundary voting has too few
    printed rows on these invoices (6-7 line items) to keep a wide
    Product Name column's internal word-gaps from being mistaken for
    real column boundaries.

    Deliberately narrow about what counts as "still part of the name":
    a bare dosage strength ("10MG" - part of the drug's own identity,
    not a pack size, same reasoning as ocr_table_reconstruction.
    extract_invoice_row_fields()'s own "MG deliberately excluded"
    comment) OR a word from the curated pharma dosage-form suffix list
    above. Does NOT treat "any alphabetic word" as automatically safe to
    absorb - see _PHARMA_NAME_SUFFIX_WORDS' docstring for the real
    regression that guarded against.
    """
    if "name" not in column_order:
        return cells
    name_idx = column_order.index("name")
    if name_idx >= len(cells):
        return cells

    merged_parts = [cells[name_idx]]
    consumed = 0
    i = name_idx + 1
    while consumed < max_extra and i < len(cells) and _looks_like_name_continuation(cells[i]):
        merged_parts.append(cells[i])
        consumed += 1
        i += 1

    if consumed == 0:
        return cells

    merged_name = " ".join(merged_parts)
    return cells[:name_idx] + [merged_name] + cells[name_idx + 1 + consumed:]


def _anchor_align(column_order, cells):
    """
    Best-effort alignment when the actual cell count doesn't exactly
    match column_order's length - a common real-world case (e.g. the
    "free" qty column only has content on rows with a free item, and is
    simply absent as its own cell on every other row).

    Unlike ocr_table_reconstruction's generic version of this same idea,
    this one already KNOWS the field identities in advance (from the
    template) rather than guessing them from shape alone, so it only
    needs shape-anchoring for the two genuinely ambiguous cases (GST%/
    date), plus positional anchoring for "amount" (always the last
    printed column on every invoice this app has been calibrated
    against) - everything else can safely fall back to matching left-to-
    right order, since a human confirmed that order once already.

    Returns None (meaning "don't trust this row, let the generic engine
    try it instead") if what's left over doesn't line up 1:1, rather
    than guessing a mapping that isn't backed by any anchor.
    """
    remaining_roles = list(column_order)
    remaining_cells = list(cells)
    pairs = []

    for role, shape_re in (("gst", _GST_SHAPE_RE), ("exp", _DATE_SHAPE_RE)):
        if role not in remaining_roles:
            continue
        for i, c in enumerate(remaining_cells):
            if shape_re.match(c.replace(" ", "")):
                pairs.append((role, c))
                remaining_roles.remove(role)
                remaining_cells.pop(i)
                break

    if "amount" in remaining_roles and remaining_cells:
        pairs.append(("amount", remaining_cells.pop()))
        remaining_roles.remove("amount")

    # A leftover count still short by 1-2 roles, after the unambiguous
    # anchors above are already placed, is overwhelmingly a printed
    # invoice's optional column being genuinely blank on this specific
    # row - a free-qty marker usually only prints at all when it's > 0,
    # and a discount% column often prints nothing when there's no
    # discount on that line. Dropping these first (rather than giving up
    # on the whole row) is what test_missing_free_qty_column_still_
    # aligns_via_anchors below guards against regressing.
    deficit = len(remaining_roles) - len(remaining_cells)
    if deficit > 0:
        # REVERTED (2026-08-09): "gst" was briefly added here to handle a
        # row where Tesseract missed the GST% glyph entirely. That fixed
        # that one row, but broke a DIFFERENT row (real regression, seen
        # live: WINSET INFUSION SET) where "gst" wasn't actually missing
        # - it was present as a garbled cell ("Ss", the "%" character
        # itself lost) that just didn't shape-match _GST_SHAPE_RE. On
        # that row the true missing column was "mfr" (dropped upstream
        # by the OCR confidence filter), not gst - but this code can't
        # tell the difference between "column genuinely not printed" and
        # "some UNRELATED column got lost to a recognition failure"
        # armed with cell count alone. Blindly dropping gst silently
        # shifted every field after it by one position instead of
        # bailing out. free/disc stay here because they're the only two
        # columns confirmed, across multiple real invoices, to
        # legitimately print blank on individual rows (not "OCR missed
        # it") - gst almost always prints something (even if garbled),
        # so its absence here is a stronger signal that something ELSE
        # is actually missing, and this row should fall back to the
        # generic engine rather than risk a confidently-wrong mapping.
        for optional_role in ("free", "disc"):
            while deficit > 0 and optional_role in remaining_roles:
                remaining_roles.remove(optional_role)
                deficit -= 1

    if len(remaining_roles) != len(remaining_cells):
        return None
    pairs.extend(zip(remaining_roles, remaining_cells))
    return pairs


def apply_template(cells, column_order):
    """
    Maps a reconstructed row's cells to fields using a supplier's known,
    pre-calibrated column order instead of guessing shapes generically.

    Returns a row_dict shaped like
    ocr_table_reconstruction.extract_invoice_row_fields()'s output, or
    None if the cell count is too far off from what this template
    expects to trust a positional mapping at all - the caller
    (bulk_import.py) falls back to the generic engine for that one row
    when this happens, so a bad template match can never be worse than
    not having a template.
    """
    expanded = []
    for c in cells:
        expanded.extend(_split_fused_cell(c))

    cleaned = [_clean_cell(c) for c in expanded]
    cleaned = [c for c in cleaned if c]
    if not cleaned:
        return None

    cleaned = _merge_split_name_cells(column_order, cleaned)

    n_expected = len(column_order)
    n_actual = len(cleaned)

    # More than 1 extra cell, or missing more than 3, is far enough off
    # that a positional guess is more likely to be wrong than the
    # generic engine's shape-based one - bail out rather than force it.
    if n_actual > n_expected + 1 or n_actual < n_expected - 3:
        return None

    if n_actual == n_expected:
        pairs = list(zip(column_order, cleaned))
    else:
        pairs = _anchor_align(column_order, cleaned)
        if pairs is None:
            return None

    fields = {role: "" for role in VALID_FIELD_ROLES}
    for role, value in pairs:
        fields[role] = value

    gst = ocr_table_reconstruction._normalize_gst(fields.get("gst", ""))
    row = {
        "name": fields.get("name") or "MEDICINE ITEM",
        "hsn": fields.get("hsn", ""),
        "batch": fields.get("batch", ""),
        "expiry": fields.get("exp", ""),
        "mrp": fields.get("mrp", ""),
        "purchase": fields.get("rate", ""),
        "qty": fields.get("qty") or "1",
        "free_qty": fields.get("free", ""),
        "company": fields.get("mfr", ""),
        "pack_size": fields.get("pack") or "1",
        "gst": gst,
        # Same signal extract_invoice_row_fields() uses, covering both a
        # garbled-but-present GST cell AND a completely missing one (no
        # gst-shaped cell in this row's positional slot at all) - neither
        # can be safely trusted, so both get flagged for review instead
        # of silently showing as a confident-looking "0%".
        "gst_uncertain": not re.search(r"\d", gst or ""),
        "amount": fields.get("amount", ""),
    }
    return ocr_table_reconstruction.validate_row(row)
