"""
ocr_table_reconstruction.py
LifeCare Pharmacy ERP - OCR Table Reconstruction (pure logic, no UI/OCR deps)

BUG CLASS THIS FILE EXISTS TO PREVENT: Field Mapping Error, also called
Column Mapping Error / Incorrect Field Assignment / Column Misalignment.
This is NOT an OCR recognition error (Tesseract misreading a character,
e.g. "5%" -> "S%") - it's Tesseract reading every character correctly,
but the reconstruction logic assigning a correctly-read value to the
WRONG column (e.g. the manufacturer "CIP" landing in the HSN field).
If you're debugging a wrong value showing up in bulk import, check
which category it is first: a garbled/wrong character is a recognition
problem (Tesseract config/preprocessing); a correct value in the wrong
field is a mapping problem (this file - row clustering, header
detection, or column boundary assignment).

Extracted from bulk_import.py so this logic - which is genuinely fiddly
and was calibrated against real invoice OCR data - can be unit tested
without needing a live Tkinter window or a working Tesseract install.
bulk_import.py calls reconstruct_table_rows() with the word list it gets
from pytesseract.image_to_data(); everything below is plain data in,
plain data out.

Every threshold/heuristic here was tuned against a real invoice's raw
OCR output (see test_ocr_table_reconstruction.py for that exact data as
a regression fixture) - changes to this file should be re-verified
against that fixture, not just against synthetic examples.
"""

import re

# Used to find where the actual product table starts/ends within the
# full page, so the company letterhead above and the totals/signature
# block below don't get fed into row reconstruction at all.
TABLE_HEADER_KEYWORDS = {
    "product", "pack", "mfr", "hsn", "batch", "exp", "mrp", "qty",
    "free", "rate", "disc", "gst", "amount", "sno", "particulars", "item"
}
TABLE_FOOTER_KEYWORDS = {
    "subtotal", "sub", "total", "terms", "conditions", "signatory",
    "outstanding", "roundoff", "round", "cgst", "sgst", "netamount", "net",
    "discount", "credit", "debit", "irn", "off",
}

# Printed "|" column-divider characters, and similar single-glyph OCR
# noise, get detected as their own word tokens by Tesseract. Verified
# against real invoice data that these dilute column-boundary voting
# between tightly-printed columns (Mfr/HSN specifically).
# {}[] added after a real invoice's printed vertical divider lines got
# OCR'd as brace/bracket characters and fused onto the adjacent HSN code
# ("{30049082") instead of surviving as their own separator token -
# _EDGE_NOISE_RE below strips these when fused onto a real value, but a
# cell that is PURELY one of these glyphs needs dropping here too, same
# as "|" already was.
_SEPARATOR_NOISE_RE = re.compile(r"[|_\-~=,.\u2013\u2014{}\[\]]+")


def _row_text(row):
    return " ".join(w["text"] for w in sorted(row, key=lambda x: x["left"])).lower()


def _keyword_hits(text, keywords):
    return sum(1 for kw in keywords if kw in text)


def looks_like_data_row(row):
    """
    A real invoice product row has a GST% token AND is numerically dense
    (several separate price/qty/code fields). Used as a fallback signal
    for where the table starts when keyword-based header detection fails
    outright (e.g. the column-header text itself is illegible to OCR).

    Verified against real invoice OCR data that a plain "several numeric
    cells" check isn't specific enough - letterhead lines combining DL/
    registration numbers with a due date and a "Cases: 0.00" field are
    numerically dense too, and were getting misidentified as the table
    start. A literal percent sign is specific to line-item GST rates and
    essentially never appears in letterhead text, which is a much more
    reliable signal (S%/5% OCR misreads are tolerated too).

    A second, independent signal is used alongside the percent check:
    2+ "X.XX" decimal-price-shaped tokens (e.g. MRP and Amount). Added
    after a real invoice's first product row had its "5%" OCR'd as
    "sn." - no percent character survived at all, so has_percent alone
    missed the row entirely and the table was detected as starting one
    row too late. Verified against the same invoice's letterhead/bank-
    details lines that none of them contain even one decimal-price
    token, so this doesn't reopen the false-positive problem above.
    """
    text_blob = " ".join(w["text"] for w in row)
    numeric_tokens = re.findall(r"\d+\.\d{2}\b|\d+%|\b\d{1,4}\b", text_blob)
    currency_tokens = re.findall(r"\d+\.\d{2}\b", text_blob)
    has_percent = bool(re.search(r"\d+\s*%", text_blob)) or bool(re.search(r"\b[sS5]\s*%", text_blob))
    has_two_prices = len(currency_tokens) >= 2
    return (has_percent or has_two_prices) and len(numeric_tokens) >= 4


def cluster_into_rows(words):
    """
    Groups OCR'd words into physical rows by vertical position.

    Uses the MEDIAN word height across the page as the row tolerance,
    not a single word's height, plus a running row-center average
    rather than a fixed anchor - both guard against one physical row
    splitting into two reconstructed rows because different cells in
    the same row (a short "5" vs a tall "CPL60075") sit at slightly
    different pixel heights.
    """
    if not words:
        return [], 20

    words = sorted(words, key=lambda w: w["top"])
    heights = sorted(w["height"] for w in words)
    median_height = heights[len(heights) // 2]

    # A word's bounding box occasionally comes back 2-3x taller than
    # normal text - not a real multi-line cell, but Tesseract mis-boxing
    # a stray mark (a checkmark/tick glyph in a "Free Qty" column, a
    # smudge, a scan artifact). Verified against a real invoice: one such
    # word (height 64 against a page median of 22) had its center
    # (top + height/2) dragged ~25px below where it visually sits,
    # exceeding the row tolerance below and splitting one physical
    # product row into three reconstructed rows. Capping the height used
    # for the center calculation - not the word's real height, which is
    # left untouched everywhere else - keeps a normal-sized outlier
    # (still within the existing 0.9 tolerance band) unaffected while
    # neutralising this specific failure mode.
    def _center(w):
        capped_height = min(w["height"], median_height * 1.5)
        return w["top"] + capped_height / 2

    rows = []
    current_row = [words[0]]
    row_center = _center(words[0])
    for w in words[1:]:
        center = _center(w)
        if abs(center - row_center) <= median_height * 0.9:
            current_row.append(w)
            row_center = sum(_center(x) for x in current_row) / len(current_row)
        else:
            rows.append(current_row)
            current_row = [w]
            row_center = center
    rows.append(current_row)

    return rows, median_height


def _row_center(row):
    return sum(w["top"] + w["height"] / 2 for w in row) / len(row)


def _find_large_gap_footer(prelim_rows, header_idx, median_height, gap_multiplier=4):
    """
    A vertical whitespace gap between two consecutive row clusters much
    bigger than the invoice's normal row-to-row spacing is a strong,
    OCR-confidence-INDEPENDENT signal that the product table has ended
    and a visually separate block (a totals/footer section, printed
    lower down with a clear gap above it) begins after it.

    Added because the keyword-based footer detection above can miss a
    footer entirely when its text OCRs too poorly for any keyword to
    survive recognisably - verified against a real invoice where "SGST
    Payable" was read as "S0STavapie" (the 'g' misread as '0'), which
    doesn't contain "sgst" as a substring anymore. Left undetected, an
    isolated leftover number from that garbled footer (e.g. a payable
    amount, "45.48") was getting treated as its own bogus product row.

    Starts scanning gaps from header_idx + 2 (the gap between the FIRST
    and SECOND real data rows), not header_idx + 1 - the header row
    itself is often visually taller/differently spaced than the data
    rows below it, and comparing across that boundary risks a false
    trigger that would wipe out the entire table after just one row.
    """
    if header_idx is None or len(prelim_rows) <= header_idx + 2:
        return None
    centers = [_row_center(r) for r in prelim_rows]
    for i in range(header_idx + 2, len(prelim_rows)):
        if centers[i] - centers[i - 1] > median_height * gap_multiplier:
            return i
    return None


def find_table_bounds(prelim_rows, median_height=20):
    """
    Finds (header_idx, footer_idx, has_real_header) - the row range that
    is the actual product table, excluding letterhead above and totals/
    signature block below.

    Three passes, most to least reliable:
      1. A single row matches 4+ header keywords (the common case).
      2. The header got split across 2 adjacent rows by row-clustering
         (common when the header is bold/differently sized than the
         data rows) - checks merged pairs before giving up.
      3. No keyword match at all (header text illegible to OCR) - falls
         back to the first row that structurally looks like product
         data. Can't anchor columns (no real header labels), but keeps
         the letterhead out even without perfect column alignment.
    """
    header_idx = None
    has_real_header = False

    for idx, row in enumerate(prelim_rows):
        if _keyword_hits(_row_text(row), TABLE_HEADER_KEYWORDS) >= 4:
            header_idx = idx
            has_real_header = True
            break

    if header_idx is None:
        for idx in range(len(prelim_rows) - 1):
            merged_text = _row_text(prelim_rows[idx]) + " " + _row_text(prelim_rows[idx + 1])
            if _keyword_hits(merged_text, TABLE_HEADER_KEYWORDS) >= 4:
                header_idx = idx
                has_real_header = True
                break

    if header_idx is None:
        for idx, row in enumerate(prelim_rows):
            if looks_like_data_row(row):
                header_idx = idx
                break

    footer_idx = len(prelim_rows)
    if header_idx is not None:
        for idx in range(header_idx + 1, len(prelim_rows)):
            if _keyword_hits(_row_text(prelim_rows[idx]), TABLE_FOOTER_KEYWORDS) >= 1:
                footer_idx = idx
                break

    gap_footer_idx = _find_large_gap_footer(prelim_rows, header_idx, median_height)
    if gap_footer_idx is not None:
        footer_idx = min(footer_idx, gap_footer_idx)

    return header_idx, footer_idx, has_real_header


def _collect_row_boundaries(row, median_height):
    """
    Returns the LEFT EDGE of each word that starts a new column within
    this row (i.e. has a wide gap before it) - not the midpoint of that
    gap. This matters because a column's START position is what's
    actually fixed in a real table, while the gap's WIDTH varies with
    however long the preceding field's content happens to be (e.g. a
    medicine name can be 10 or 30 characters). Verified against real
    invoice data: the Pack column's start position was consistently
    508-510px across 23 rows of wildly different name lengths, while
    the gap MIDPOINT (this function's previous approach) scattered
    across a much wider range and never accumulated enough votes in any
    single bucket to be recognised as a real column boundary.
    """
    row_sorted = sorted(row, key=lambda w: w["left"])
    boundaries = []
    for i in range(1, len(row_sorted)):
        prev = row_sorted[i - 1]
        gap = row_sorted[i]["left"] - (prev["left"] + prev["width"])
        if gap > median_height * 0.8:
            boundaries.append(row_sorted[i]["left"])
    return boundaries


def find_consensus_boundaries(table_rows, median_height, bucket_px=25):
    """
    Collects candidate column-boundary x-positions from EVERY row in the
    table and keeps only the ones enough rows agree on, instead of
    trusting a single row's (usually the header's) word segmentation to
    define every column boundary - which breaks if OCR merges tightly-
    spaced header labels into fewer words than there are real columns,
    collapsing everything after that point. Numeric data cells (Qty/
    Rate/MRP/GST%) are usually cleanly space-separated even when header
    labels get merged, so they still vote correctly.
    """
    votes = {}
    for row in table_rows:
        for b in _collect_row_boundaries(row, median_height):
            key = round(b / bucket_px)
            votes[key] = votes.get(key, 0) + 1

    min_votes = max(2, len(table_rows) // 4)
    # Subtract a small buffer from each bucket's boundary value: voting
    # rounds a real position (e.g. 673) up to its nearest bucket (675),
    # but the real word position that cast that vote is still 673 -
    # checking 673 >= 675 during final column assignment would
    # incorrectly fail and merge it into the previous column. Shifting
    # the threshold earlier than the rounded bucket center fixes this.
    return sorted(k * bucket_px - (bucket_px // 2) for k, v in votes.items() if v >= min_votes)


def _assign_column(x, boundaries, num_cols):
    col = 0
    for b in boundaries:
        if x >= b:
            col += 1
        else:
            break
    return min(col, num_cols - 1)


def rows_to_tsv(table_rows, median_height):
    """
    Converts table_rows into (tsv_lines, row_confidences). Tries
    consensus-boundary column detection first (see
    find_consensus_boundaries); falls back to per-row gap-based
    splitting if voting doesn't find enough consistent structure.
    """
    tsv_lines = []
    row_confidences = []

    consensus_boundaries = find_consensus_boundaries(table_rows, median_height)

    if len(consensus_boundaries) >= 3:
        num_cols = len(consensus_boundaries) + 1
        for row in table_rows:
            row_sorted = sorted(row, key=lambda w: w["left"])
            buckets = [[] for _ in range(num_cols)]
            for w in row_sorted:
                buckets[_assign_column(w["left"], consensus_boundaries, num_cols)].append(w["text"])
            cell_texts = [" ".join(b) for b in buckets if b]
            tsv_lines.append("\t".join(cell_texts))
            confs = [w["conf"] for w in row_sorted]
            row_confidences.append(sum(confs) / len(confs) if confs else None)
        return tsv_lines, row_confidences

    # Per-row gap-based fallback.
    for row in table_rows:
        row_sorted = sorted(row, key=lambda w: w["left"])

        if len(row_sorted) == 1:
            tsv_lines.append(row_sorted[0]["text"])
            row_confidences.append(row_sorted[0]["conf"])
            continue

        gaps = []
        for i in range(1, len(row_sorted)):
            prev = row_sorted[i - 1]
            gaps.append(row_sorted[i]["left"] - (prev["left"] + prev["width"]))
        positive_gaps = sorted(g for g in gaps if g > 0)
        median_gap = positive_gaps[len(positive_gaps) // 2] if positive_gaps else 10
        column_gap_threshold = max(median_gap * 2.5, median_height * 1.2)

        columns = [[row_sorted[0]]]
        for w in row_sorted[1:]:
            prev = columns[-1][-1]
            gap = w["left"] - (prev["left"] + prev["width"])
            if gap > column_gap_threshold:
                columns.append([w])
            else:
                columns[-1].append(w)

        cell_texts = [" ".join(word["text"] for word in col) for col in columns]
        tsv_lines.append("\t".join(cell_texts))
        confs = [word["conf"] for word in row_sorted]
        row_confidences.append(sum(confs) / len(confs) if confs else None)

    return tsv_lines, row_confidences


_GST_RE = re.compile(r"^[sS]?\s*\d{0,2}\s*%$")
# Tolerates a stray trailing character fused directly onto the date by
# OCR (e.g. "02/28]", "12/27|") - verified against real invoice data
# that Tesseract sometimes merges a punctuation glyph into the same
# bounding box as the date, and an exact-match pattern was rejecting
# the date entirely, breaking the anchor the rest of the row is
# positioned relative to.
_DATE_RE = re.compile(r"^(\d{1,2}/\d{2,4})\W?$")
_HSN_RE = re.compile(r"^\d{6,9}[\W]?$")
# {}[] added alongside the existing pipe/slash noise glyphs - verified
# against real invoice data that a printed vertical divider line next to
# an HSN code sometimes OCRs as a brace/bracket character fused onto the
# code ("{30049082") rather than surviving as its own token. Left
# unhandled, _HSN_RE above can never match (it requires the cell to
# START with a digit), the code silently falls through the column-
# shift logic, and the leaked "{30049082" ends up mislabelled as the
# Company field instead of being recognised as the HSN.
_CELL_NOISE_RE = re.compile(r"^[/\\_\-~|={}\[\]]+$")
# Strips noise characters fused onto the EDGES of an otherwise-real
# value (e.g. "30049093 /" -> "30049093", "|62171020|" -> "62171020",
# "{30049082" -> "30049082"). _CELL_NOISE_RE above only catches a cell
# that's PURELY noise; this catches the more common real-world case
# where a stray printed pipe/slash/brace column-divider got merged into
# the same reconstructed cell as genuine data, verified against real
# invoice output where this was leaking into the Company field.
_EDGE_NOISE_RE = re.compile(r"^[/\\_\-~|={}\[\]\s]+|[/\\_\-~|={}\[\]\s]+$")


_LEADING_SNO_RE = re.compile(r"^\d{1,2}\s*")
_MULTIWORD_NAME_RE = re.compile(r"[A-Za-z0-9]{3,}\s+[A-Za-z0-9]{2,}")


def _looks_like_product_name(text):
    """
    True if `text` looks like real printed product text rather than a
    short leaked column (an HSN code, or a Type/Category marker like
    "INJEC"/"OTHE" that some invoices print in its own column ahead of
    the Name column - a different layout than the one this file was
    originally calibrated against, where Name is the very first real
    column).

    Verified against a real invoice where that Type column ("INJEC" for
    injectables, "OTHE" for other items, etc.) was landing in the Name
    field, and the actual product name was either lost or mis-assigned
    into Batch by the positional fallback further down.

    Two signals, matching how these invoices actually print names:
      - Multi-word text ("ALK KETROL", "BABY WIPES") - two separate
        word-shaped tokens is strong evidence of real printed text,
        regardless of length.
      - A single word, but long enough (6+ alnum characters) and
        mostly LETTERS rather than digits - real one-word product
        codes in this data (DOLO650, ECOSPRIN) run 6+ characters and
        are letter-dominant, unlike a leaked HSN code (all digits) or
        a short 4-5 character category marker.
    A possible leaked Sno digit prefix ("1 [INJEC") is stripped first
    so it can't inflate the length count for the single-word case.
    """
    stripped = _LEADING_SNO_RE.sub("", text, count=1)
    if _MULTIWORD_NAME_RE.search(stripped):
        return True
    alnum = re.sub(r"[^A-Za-z0-9]", "", stripped)
    digits = sum(ch.isdigit() for ch in alnum)
    letters = len(alnum) - digits
    return len(alnum) >= 6 and letters > digits


def _normalize_gst(gst_text):
    """OCR frequently misreads '5%' as 'S%' (visually similar glyphs in
    many invoice fonts) - since 5% is by far the most common pharmacy
    GST slab and the S-prefix pattern is otherwise meaningless, this is
    a safe, targeted normalization rather than a guess. Without this,
    a correctly-IDENTIFIED GST cell still silently becomes 0 downstream
    when converted to a number, since "S" isn't numeric."""
    if not gst_text:
        return gst_text
    return re.sub(r"^[sS]", "5", gst_text.strip())


def extract_invoice_row_fields(cells):
    """
    Final Robust Extractor: Automatically filters out stray slashes and noise 
    to prevent column shifts and aligns every field to its correct column.
    """
    # Clean noise and completely remove stray slash '/' tokens that cause column shifts
    cleaned_cells = []
    for c in cells:
        c_str = _EDGE_NOISE_RE.sub("", c.strip()).strip()
        if not c_str or _CELL_NOISE_RE.match(c.strip()):
            continue
        if c_str == "/":
            continue  # Skip stray slash tokens entirely
        cleaned_cells.append(c_str)

    if not cleaned_cells:
        return {}

    # If the first cell is a serial number (e.g., '1', '2', etc.), remove it.
    # OCR sometimes garbles that same Sno position into short noise instead
    # of a clean digit (verified against a real invoice: '~*' for '1', 'bd'
    # for '4') - if left alone, that noise token steals the name slot below
    # and the real medicine name gets silently dropped. Medicine names in
    # these invoices are always printed/read in uppercase and are
    # essentially never <=3 characters, so treating any short, non-clean-
    # uppercase leading cell as Sno noise is safe without needing a
    # dictionary of real product names to compare against. The digit case
    # is still stripped unconditionally (matches prior behaviour even for
    # a lone serial-number-only row); the garbled-noise case additionally
    # requires something to actually be left afterwards, so a genuinely
    # short real name never gets discarded down to nothing.
    first = cleaned_cells[0]
    first_is_digit_sno = first.isdigit() and len(first) <= 3
    first_is_garbled_sno = (
        len(cleaned_cells) > 1
        and len(first) <= 3
        and not (first.isalpha() and first.isupper())
    )
    if first_is_digit_sno or first_is_garbled_sno:
        cleaned_cells.pop(0)

    if not cleaned_cells:
        return {}

    raw_name = cleaned_cells.pop(0)
    remaining = cleaned_cells

    # Some invoices print a short Type/Category column (INJEC/OTHE) or
    # the HSN code in the column immediately after Sno, ahead of the
    # actual product Name column - the opposite order this file was
    # originally calibrated against. If the cell that would become the
    # name doesn't look like real product text, but a cell shortly
    # after it does, shift forward - otherwise the leaked cell becomes
    # the "name" shown to the user and the real name is lost or lands
    # in the wrong field entirely. Bounded to 3 shifts so a row that's
    # illegible throughout can't consume the whole cell list.
    leaked_hsn = ""
    shifts = 0
    while remaining and not _looks_like_product_name(raw_name) and shifts < 3:
        if not leaked_hsn and _HSN_RE.match(raw_name):
            leaked_hsn = raw_name
        raw_name = remaining.pop(0)
        shifts += 1

    # Pack size extraction from medicine name or next token.
    # MG deliberately excluded here (unlike ML/GM) - verified against real
    # invoice data that "<n>MG" directly after the brand name is the drug's
    # own dosage strength (e.g. "PANTOSEC 40MG", "PARACIP 250MG"), part of
    # the product's identity, not a pack size. ML/GM in this position
    # genuinely are pack/volume indicators (a 60ML bottle, a 10GM tube).
    # Stripping "40MG" out here was mangling the name into "PANTOSEC  TAB"
    # and mislabelling the drug's strength as Pack Size.
    pack_size = "1"
    pack_match = re.search(r"\b(\d+[\'\u2019]?S|\d+\s*(?:ML|GM))\b", raw_name, re.IGNORECASE)
    if pack_match:
        pack_size = pack_match.group(1).upper()
        name = raw_name.replace(pack_match.group(0), "").strip().rstrip("-/ ")
    else:
        name = raw_name
        if remaining and re.match(r"^\d+['’]?[sS]$|^\d+\s*(?:ml|gm|mg)$", remaining[0], re.IGNORECASE):
            pack_size = remaining.pop(0).upper()

    # 1. Amount is always anchored at the very end
    amount = remaining.pop() if remaining else ""

    # 2. GST% is anchored by percentage pattern from right side
    gst, gst_idx = "", None
    for i in range(len(remaining) - 1, -1, -1):
        if _GST_RE.match(remaining[i].replace(" ", "")):
            gst, gst_idx = remaining[i], i
            break
    if gst_idx is not None:
        del remaining[gst_idx]
    gst = _normalize_gst(gst)
    # True whenever there's no usable digit to trust - covers BOTH a
    # garbled-but-present cell ("S%" with the digit itself lost) and a
    # cell that's completely missing (no percent-shaped token anywhere in
    # the row at all, e.g. a printed "%" too faint for Tesseract to see).
    # Previously only the first case was flagged - a fully blank gst
    # silently became a confident-looking "0%" downstream in
    # bulk_import.py instead of prompting the pharmacist to check it.
    gst_uncertain = not re.search(r"\d", gst or "")

    # 3. Purchase Rate is the last remaining token before GST
    rate = remaining.pop() if remaining else ""

    # 4. Expiry (MM/YY) is anchored by date pattern
    # BUG FIX: was storing the raw cell `c` instead of the regex's own
    # captured group - _DATE_RE already tolerates one stray trailing
    # character fused on by OCR (e.g. "10/27,", "02/28}") specifically
    # so the date is still recognised, but the trailing noise character
    # was then flowing straight into the final "expiry" value shown to
    # the user instead of being stripped, e.g. "10/27," staying on
    # screen with the comma still attached.
    expiry, expiry_idx = "", None
    for i, c in enumerate(remaining):
        m = _DATE_RE.match(c)
        if m:
            expiry, expiry_idx = m.group(1), i
            break

    mrp = qty = free_qty = ""
    batch = company = ""
    hsn = leaked_hsn

    if expiry_idx is not None:
        before, after = remaining[:expiry_idx], remaining[expiry_idx + 1:]

        if len(after) >= 1:
            mrp = after[0]
        if len(after) >= 2:
            qty = after[1]
        if len(after) >= 3:
            free_qty = after[2]

        if before:
            batch = before.pop()
        if not hsn and before and _HSN_RE.match(before[-1]):
            hsn = before.pop()
        if before:
            company = before.pop()
    else:
        if len(remaining) >= 1:
            mrp = remaining[-1]

    # Fix decimal errors ONLY for MRP and Purchase Rate
    mrp_val = mrp or "0"
    mrp = fix_decimal(mrp, mrp_val)
    rate = fix_decimal(rate, mrp)

    row_dict = {
        "name": name or "MEDICINE ITEM", 
        "pack_size": pack_size, 
        "company": company, 
        "hsn": hsn,
        "batch": batch, 
        "expiry": expiry, 
        "mrp": mrp, 
        "qty": qty,
        "free_qty": free_qty, 
        "purchase": rate, 
        "gst": gst, 
        "gst_uncertain": gst_uncertain,
        "amount": amount,
    }

    return validate_row(row_dict)


def validate_row(row):
    errors = []

    try:
        mrp = float(row["mrp"])
        rate = float(row["purchase"])

        if rate > mrp:
            errors.append("RATE_GT_MRP")

    except:
        errors.append("INVALID_PRICE")

    try:
        qty = int(row["qty"] or 0)
        rate = float(row["purchase"] or 0)
        amount = float(row["amount"] or 0)

        expected = round(qty * rate, 2)

        if abs(expected - amount) > 1:
            errors.append("AMOUNT_MISMATCH")

    except:
        errors.append("INVALID_AMOUNT")

    row["errors"] = errors
    return row


def fix_decimal(value, mrp):
    try:
        v = float(value)
        m = float(mrp)

        if v > m:
            if v/100 <= m:
                return f"{v/100:.2f}"

            if v/10 <= m:
                return f"{v/10:.2f}"

        return value

    except:
        return value


def _drop_outlier_glyphs(words, median_height):
    """
    Occasionally Tesseract boxes a stray printed mark (a checkmark/tick
    in a "Free Qty" column, a smudge, a scan artifact) as its own "word"
    with a bounding box 2-3x taller than the real text around it, and
    reads a couple of garbage characters out of it.

    Verified against a real invoice: one such artifact ("Va", height 64
    against a page median of 22) polluted BOTH row clustering (its
    inflated height dragged its vertical center - see cluster_into_rows'
    height cap - into the wrong row entirely) AND column-boundary
    bucketing (its x-position happened to fall inside the Qty column's
    bucket, corrupting that cell's text to "30| Va"). Dropping it here,
    before either stage runs, fixes both at once.

    A short (<=3 alnum character), grossly oversized word is essentially
    never real invoice text - genuine short tokens on these invoices (a
    bare qty "5", a sno "1", a unit "PH") all sit within normal text
    height - so this is safe to drop outright rather than merely
    dampen its effect on clustering.
    """
    return [
        w for w in words
        if not (
            w["height"] > median_height * 2.5
            and len(re.sub(r"[^A-Za-z0-9]", "", w["text"])) <= 3
        )
    ]


def reconstruct_table_rows(words):
    """
    Main entry point. words: list of dicts with keys
    text/left/top/width/height/conf (exactly what pytesseract's
    image_to_data gives per detected word, already filtered to drop
    empty text and confidence -1).

    Returns (tsv_lines, row_confidences), or None if there's too little
    text to be a real invoice table.
    """
    words = [w for w in words if not _SEPARATOR_NOISE_RE.fullmatch(w["text"])]

    if len(words) < 4:
        return None

    heights = sorted(w["height"] for w in words)
    prelim_median_height = heights[len(heights) // 2]
    words = _drop_outlier_glyphs(words, prelim_median_height)

    prelim_rows, median_height = cluster_into_rows(words)
    header_idx, footer_idx, has_real_header = find_table_bounds(prelim_rows, median_height)

    table_rows = prelim_rows[header_idx:footer_idx] if header_idx is not None else prelim_rows

    return rows_to_tsv(table_rows, median_height)