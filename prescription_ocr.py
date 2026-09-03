"""
prescription_ocr.py
LifeCare Pharmacy ERP - Prescription Photo -> Bill OCR (Aug 2026)

Turns a photographed doctor's prescription into a list of candidate
{name, qty, confidence} rows for the pharmacist to REVIEW before adding
to a bill (see prescription_scan_gui.py) - nothing here ever gets added
to a bill on its own. Every function below returns data, never touches
a database or a Tkinter widget, so this whole module can be unit tested
without a display or Tesseract installed (see test_prescription_ocr.py).

Deliberately NOT reusing bulk_import.py's table-reconstruction pipeline:
that pipeline is built for TABULAR distributor invoices (rows/columns of
Medicine/Batch/Qty/Rate) where a real grid structure exists. A doctor's
prescription is free-form handwriting/typed text with no table
structure at all, so this always works line-by-line instead. The image
preprocessing (grayscale/upscale/autocontrast/Otsu threshold) and
orientation correction below are exact copies of bulk_import.py's own
_preprocess_for_ocr()/_auto_correct_orientation() - kept as their own
copies rather than imported, for the same reason purchase_order.py's
own docstring gives for its own duplicated query: bulk_import.py
imports tkinter at module load time and can't be imported in a headless
test environment (see test_bulk_import_pack_size_protection.py's own
docstring for the same constraint).

Confidence handling is deliberately DIFFERENT from the invoice OCR
pipeline: bulk_import.py drops any OCR'd WORD below MIN_WORD_CONFIDENCE
(30) before it ever reaches a row - reasonable for printed invoice text,
where a low-confidence word is usually paper noise. Handwritten
prescriptions are far less reliable across the board (real doctor
handwriting can score low confidence on every word AND still be the
only version of that text there is) - filtering by confidence here
would silently drop most of a real prescription instead of surfacing it
for review. So this module keeps every non-empty line Tesseract
produces, attaches its real average confidence, and lets the review UI
decide what to show/highlight - human review is the actual safety net
here, not a confidence cutoff.
"""

import os
import re

# pytesseract/PIL are imported lazily - see bulk_import.py's
# _ensure_ocr_imports() for the exact same reasoning (paying the
# ~0.7s Tesseract subprocess check cost only when OCR is actually used,
# not on every module import / every time Billing opens).
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

pytesseract = None
Image = None
ImageOps = None
OCR_AVAILABLE = False


def _ensure_ocr_imports():
    global pytesseract, Image, ImageOps, OCR_AVAILABLE
    if OCR_AVAILABLE:
        return True
    try:
        import pytesseract as _pytesseract
        from PIL import Image as _Image, ImageOps as _ImageOps

        if os.name == "nt" and os.path.exists(TESSERACT_CMD):
            _pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

        pytesseract, Image, ImageOps = _pytesseract, _Image, _ImageOps
        OCR_AVAILABLE = True
    except Exception:
        OCR_AVAILABLE = False
    return OCR_AVAILABLE


# ==========================================
# IMAGE PREPROCESSING (exact copy of bulk_import.py's own - see module
# docstring for why it's duplicated rather than imported)
# ==========================================

def _otsu_threshold(img):
    hist = img.histogram()
    total = sum(hist)
    if total == 0:
        return 150

    sum_total = sum(i * hist[i] for i in range(256))
    sum_b, weight_b, max_variance, threshold = 0.0, 0, 0.0, 150

    for i in range(256):
        weight_b += hist[i]
        if weight_b == 0:
            continue
        weight_f = total - weight_b
        if weight_f == 0:
            break
        sum_b += i * hist[i]
        mean_b = sum_b / weight_b
        mean_f = (sum_total - sum_b) / weight_f
        variance_between = weight_b * weight_f * (mean_b - mean_f) ** 2
        if variance_between > max_variance:
            max_variance = variance_between
            threshold = i

    return threshold


def _auto_correct_orientation(img):
    try:
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        rotation = int(osd.get("rotate", 0) or 0)
        if rotation:
            img = img.rotate(-rotation, expand=True)
    except Exception:
        pass
    return img


def preprocess_for_ocr(img):
    """Same grayscale/upscale/autocontrast/Otsu-threshold pipeline as
    bulk_import.py's _preprocess_for_ocr() - a photographed prescription
    has the exact same lighting/skew problems a photographed invoice
    does, so the same fix applies."""
    img = _auto_correct_orientation(img)
    img = ImageOps.grayscale(img)

    width, height = img.size
    if width < 1800:
        scale = 1800 / width
        img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

    img = ImageOps.autocontrast(img, cutoff=1)
    threshold = _otsu_threshold(img)
    img = img.point(lambda p: 255 if p > threshold else 0)
    return img


# ==========================================
# PURE TEXT PARSING (no PIL/Tesseract needed - unit testable directly)
# ==========================================

def _clean_line_prefix(line):
    """Strips numbered/bulleted list markers a doctor's script commonly
    uses ("1. Paracetamol 650", "- Amoxicillin x 10")."""
    line = re.sub(r"^\s*\d+[\.\)]\s*", "", line)
    line = re.sub(r"^\s*[-*\u2022\u25cb]\s*", "", line)
    return line.strip()


def _parse_free_line(line):
    """"Medicine x Qty" -> separate name/qty; otherwise the whole line
    is the name with qty defaulted to 1. Same shape as bulk_import.py's
    own _parse_free_line() for its free-text fallback path."""
    m = re.match(r"^(.*?)\s*[xX]\s*(\d+)\s*$", line)
    if m and m.group(1).strip():
        return {"name": m.group(1).strip(" -\u2013:"), "qty": int(m.group(2))}
    return {"name": line, "qty": 1}


def group_words_into_lines(data):
    """
    Turns pytesseract.image_to_data()'s raw dict-of-lists into ordered
    {"text", "confidence"} line entries, grouping words by Tesseract's
    own (block_num, par_num, line_num) triple - line_num alone repeats
    across different blocks/paragraphs, so block+par+line together are
    what actually identifies one unique visual line on the page.

    Pure function (plain dict in, plain list out) so this can be unit
    tested with a synthetic `data` dict, without needing Tesseract
    installed or a real image.
    """
    lines = {}
    order = []
    n = len(data.get("text", []))
    for i in range(n):
        text = (data["text"][i] or "").strip()
        conf = data["conf"][i]
        if not text or str(conf) in ("-1", ""):
            continue
        try:
            conf_val = int(float(conf))
        except (TypeError, ValueError):
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key not in lines:
            lines[key] = {"words": [], "confs": []}
            order.append(key)
        lines[key]["words"].append(text)
        lines[key]["confs"].append(conf_val)

    result = []
    for key in order:
        entry = lines[key]
        text = " ".join(entry["words"])
        confidence = round(sum(entry["confs"]) / len(entry["confs"]), 1)
        result.append({"text": text, "confidence": confidence})
    return result


def parse_prescription_rows(line_entries):
    """
    Takes group_words_into_lines()'s output and turns it into candidate
    {name, qty, confidence} rows - stripping list markers and splitting
    "Name x Qty" where present. Every non-blank line is kept (see module
    docstring on why this doesn't drop low-confidence lines) - the
    review UI decides what to show/discard.
    """
    rows = []
    for entry in line_entries:
        cleaned = _clean_line_prefix(entry["text"])
        if not cleaned:
            continue
        parsed = _parse_free_line(cleaned)
        parsed["confidence"] = entry["confidence"]
        rows.append(parsed)
    return rows


# ==========================================
# FULL PIPELINE (needs Tesseract + PIL - not unit tested directly, see
# module docstring; this is a thin glue layer over the pure functions
# above, which ARE unit tested)
# ==========================================

def extract_prescription_lines(image_path):
    """
    Photo path -> list of {name, qty, confidence} candidate rows.
    Must be called off the Tkinter main thread for anything but a tiny
    test image - see prescription_scan_gui.py's threading, mirroring
    bulk_import.py's own run_ocr()/_ocr_worker() pattern.
    """
    if not _ensure_ocr_imports():
        raise RuntimeError(
            "OCR is not available - pytesseract/Pillow isn't installed, "
            "or Tesseract-OCR isn't installed on this computer."
        )

    img = Image.open(image_path)
    processed = preprocess_for_ocr(img)
    # PSM 6 ("uniform block of text") - same mode bulk_import.py's own
    # OCR uses; reasonable default for both printed and handwritten
    # short lines on a prescription pad.
    data = pytesseract.image_to_data(processed, config="--psm 6", output_type=pytesseract.Output.DICT)
    line_entries = group_words_into_lines(data)
    return parse_prescription_rows(line_entries)
