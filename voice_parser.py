"""
voice_parser.py
LifeCare Pharmacy ERP - Voice Entry text parser (offline, no LLM/API).

Turns a faster-whisper transcript like:
    "Nicip Plus, batch 26105, expiry July 2026, 10 tablets, MRP 67.12"
into a structured dict:
    {
        "medicine_name": "Nicip Plus",
        "batch": "26105",
        "expiry": "07/26",
        "qty": 10,
        "pack_size": None,
        "mrp": 67.12,
        "purchase_rate": None,
        "warnings": [],
    }

DESIGN
------
Whisper output for these short, structured purchase-entry phrases is
predictable enough that a keyword-anchored regex parser is more
reliable (and fully offline/free) than reaching for an LLM. See
VOICE_ENTRY_WORKFLOW.md for the overall workflow this plugs into
(parser -> medicine_matcher.match_invoice_row -> Preview/Confirm ->
SQLite, per CODING_RULES.md's "no SQL inside UI code" - this module
does no database access at all, it only turns text into a dict).

Anything the parser is NOT confident about is left as None and noted
in the returned "warnings" list - the calling Preview/Confirm screen
must show these to the pharmacist rather than silently guessing,
matching the "never auto-save uncertain voice results" rule in
VOICE_ENTRY_WORKFLOW.md.
"""

import re
from typing import Optional


_MONTHS = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
}

# Number-word spoken quantities Whisper sometimes leaves as words instead
# of digits ("ten tablets" instead of "10 tablets") for small numbers.
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

_QTY_UNITS = (
    r"(?:tablets?|tabs?|capsules?|caps?|strips?|bottles?|vials?|"
    r"units?|pieces?|pcs?|nos?|boxes?|ampoules?|amps?)"
)

# Keyword markers, in the order we search for them. Each maps a field
# name to the regex that matches the KEYWORD itself (not the value) -
# the value is whatever text sits between this keyword's end and the
# next marker's start (see _split_on_markers).
_KEYWORD_PATTERNS = [
    ("batch", re.compile(r"\bbatch(?:\s*(?:no\.?|number))?\s*[:\-]?\s*", re.I)),
    ("expiry", re.compile(r"\bexp(?:iry)?(?:\s*date)?\s*[:\-]?\s*", re.I)),
    ("pack_size", re.compile(r"\bpack\s*(?:size|of)?\s*[:\-]?\s*", re.I)),
    ("mrp", re.compile(r"\bm\.?\s*r\.?\s*p\.?\s*[:\-]?\s*", re.I)),
    ("purchase_rate", re.compile(r"\b(?:purchase\s*rate|cost\s*price|rate)\s*[:\-]?\s*", re.I)),
]

# Quantity is "<number> <unit word>" rather than a keyword-then-value
# pair, so it gets its own pass.
_QTY_PATTERN = re.compile(
    r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")\s*" + _QTY_UNITS + r"\b", re.I
)


def _word_to_number(token: str) -> Optional[int]:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def _clean_value(text: str) -> str:
    return text.strip(" ,.-;:\t\n")


def _parse_expiry(raw: str) -> Optional[str]:
    """Normalizes to MM/YY (the format already used across this ERP,
    e.g. import_invoice.py's "12/27") from either a spoken month name
    ("July 2026") or an already-numeric date ("07/2026", "07/26",
    "7-2026"). Returns None if nothing recognizable is found."""
    if not raw:
        return None
    raw = raw.strip()

    # "July 2026" / "July, 2026"
    m = re.search(r"([a-zA-Z]+)\.?\s*,?\s*(\d{4}|\d{2})", raw)
    if m:
        month_word = m.group(1).lower()[:9]
        year = m.group(2)
        month_num = None
        for name, num in _MONTHS.items():
            if month_word.startswith(name) or name.startswith(month_word):
                month_num = num
                break
        if month_num:
            year_short = year[-2:] if len(year) == 4 else year
            return f"{month_num}/{year_short}"

    # "07/2026" / "07/26" / "7-2026" / "07.26"
    m = re.search(r"(\d{1,2})\s*[/\-.]\s*(\d{2,4})", raw)
    if m:
        month_num = m.group(1).zfill(2)
        year = m.group(2)
        year_short = year[-2:] if len(year) == 4 else year
        return f"{month_num}/{year_short}"

    return None


def _parse_money(raw: str) -> Optional[float]:
    """Pulls the first decimal number out of a value string, ignoring
    filler words like "rupees" ("rupees 67.12" -> 67.12)."""
    if not raw:
        return None
    m = re.search(r"\d+\.?\d*", raw)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


def parse_voice_entry(text: str) -> dict:
    """
    Parses one transcribed voice-entry phrase into a structured dict.

    Returns a dict with keys: medicine_name, batch, expiry, qty,
    pack_size, mrp, purchase_rate, warnings (list[str]).
    Any field the parser could not confidently extract is left as
    None - the caller must surface `warnings` to the pharmacist on the
    Preview/Confirm screen, never silently save.
    """
    result = {
        "medicine_name": None,
        "batch": None,
        "expiry": None,
        "qty": None,
        "pack_size": None,
        "mrp": None,
        "purchase_rate": None,
        "warnings": [],
    }

    if not text or not text.strip():
        result["warnings"].append("Empty transcript - nothing to parse")
        return result

    text = text.strip()

    # --- Quantity: "<number> <unit>" pulled out first, since it's not
    # a keyword-then-value pair like the others.
    qty_match = _QTY_PATTERN.search(text)
    if qty_match:
        result["qty"] = _word_to_number(qty_match.group(1))
    else:
        result["warnings"].append("Quantity not found (expected e.g. '10 tablets')")

    # --- Collect keyword marker spans (start of keyword, end of
    # keyword = start of its value) for every field present.
    markers = []
    for field, pattern in _KEYWORD_PATTERNS:
        m = pattern.search(text)
        if m:
            markers.append((m.start(), m.end(), field))

    # Also mark the qty phrase's span so it doesn't leak into a
    # neighbouring field's value when slicing between markers.
    if qty_match:
        markers.append((qty_match.start(), qty_match.end(), "qty"))

    markers.sort(key=lambda t: t[0])

    # --- Medicine name = everything before the first marker.
    if markers:
        result["medicine_name"] = _clean_value(text[: markers[0][0]]) or None
    else:
        result["medicine_name"] = _clean_value(text) or None

    if not result["medicine_name"]:
        result["warnings"].append("Medicine name not found (nothing before batch/expiry/MRP)")

    # --- Slice out each keyword field's value: from its own end to
    # the start of the next marker (or end of string).
    for idx, (start, end, field) in enumerate(markers):
        if field == "qty":
            continue  # already handled above
        value_end = markers[idx + 1][0] if idx + 1 < len(markers) else len(text)
        raw_value = _clean_value(text[end:value_end])

        if field == "expiry":
            parsed = _parse_expiry(raw_value)
            result["expiry"] = parsed
            if raw_value and not parsed:
                result["warnings"].append(f"Could not parse expiry from '{raw_value}'")
        elif field in ("mrp", "purchase_rate"):
            parsed = _parse_money(raw_value)
            result[field] = parsed
            if raw_value and parsed is None:
                result["warnings"].append(f"Could not parse {field} from '{raw_value}'")
        elif field == "batch":
            result["batch"] = raw_value or None
        elif field == "pack_size":
            result["pack_size"] = raw_value or None

    if result["batch"] is None:
        result["warnings"].append("Batch number not found")
    if result["expiry"] is None:
        result["warnings"].append("Expiry date not found")
    if result["mrp"] is None:
        result["warnings"].append("MRP not found")
    # purchase_rate and pack_size are genuinely optional per
    # VOICE_ENTRY_WORKFLOW.md ("if spoken") - no warning for those.

    return result
