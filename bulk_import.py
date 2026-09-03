"""
Bulk Purchase Import (GST Synchronized)
--------------------------------------
Parses copied rows from Excel/Google Sheets including GST% to ensure
accurate landed cost and profit margin calculations.
"""

import csv
import glob
import io
import math
import os
import queue
import random
import re
import sqlite3
import statistics
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import medicine_matcher
import ocr_table_reconstruction
import ocr_supplier_templates
import spreadsheet_import
import ui_style
import theme

from app_paths import DB_NAME
from pricing_utils import get_pack_multiplier
import ui_popups

TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def _resolve_poppler_path():
    r"""
    Finds Poppler's bin\ folder under C:\poppler\ regardless of exact
    version number (Aug 2026, installer support). The old code hardcoded
    C:\poppler\poppler-26.02.0\Library\bin - fine as long as every install
    happens to use that exact Poppler build, fragile the moment a fresh
    install (a different pharmacy's PC, via the Windows installer) ships
    a newer/older Poppler release with a different version folder name,
    which would silently break PDF import with no obvious reason why.

    Globs for any poppler-*\Library\bin under C:\poppler\ and returns the
    first match. Falls back to the exact original hardcoded path if no
    match is found, so THIS PC's behaviour (which already has
    poppler-26.02.0 installed) is completely unchanged.
    """
    matches = sorted(glob.glob(r"C:\poppler\poppler-*\Library\bin"))
    if matches:
        return matches[0]
    return r"C:\poppler\poppler-26.02.0\Library\bin"

# pytesseract/PIL are imported lazily (see _ensure_ocr_imports below)
# rather than at module load time. bulk_import.py gets imported just by
# opening the Purchase screen (purchase.py does "from bulk_import import
# BulkImportWindow"), and pytesseract's own import does a real subprocess
# check of the Tesseract binary (~0.7s measured) - paying that cost just
# to open Purchase, before the user has ever touched OCR, was the actual
# reason Purchase opened noticeably slower than other sidebar screens.
# Deferring it to the moment the Bulk Import window's OCR tab is actually
# built keeps that cost where it belongs, instead of on every Purchase click.
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


# openpyxl is imported lazily too, same reasoning as pytesseract/PIL
# above - not every pharmacist will ever use "Import from File", so its
# import cost shouldn't be paid just for opening Purchase.
openpyxl = None
OPENPYXL_AVAILABLE = False


def _ensure_openpyxl_import():
    global openpyxl, OPENPYXL_AVAILABLE
    if OPENPYXL_AVAILABLE:
        return True
    try:
        import openpyxl as _openpyxl
        openpyxl = _openpyxl
        OPENPYXL_AVAILABLE = True
    except Exception:
        OPENPYXL_AVAILABLE = False
    return OPENPYXL_AVAILABLE


UNIT_WORDS = r"(?:strips?|tabs?|tablets?|units?|nos?|pcs?|caps?|capsules?)"

# Tesseract's own confidence score is only -1 (no detection at all) vs. a
# real 0-100 value - it does NOT mean "0 confidence = definitely noise".
# In practice, on lower-quality scans/photocopies, Tesseract hallucinates
# short garbage "words" (stray marks, paper texture, torn edges) in blank
# areas of the page with real-but-very-low confidence (0-25%), not -1.
# Verified against a real invoice export where 27% of all detected words
# were under 30% confidence, clustered entirely in the blank gap between
# the product table and the totals box - those fake words were getting
# treated as extra product rows AND were corrupting the column-position
# voting for the real rows around them (wrong Purchase/MRP/Qty even on
# rows that otherwise matched correctly). Dropping them before they ever
# reach row clustering fixes both problems at the source, rather than
# trying to patch around their effects downstream.
MIN_WORD_CONFIDENCE = 30

# Row-level average OCR confidence (0-100, from pytesseract's own word
# confidences - see ocr_table_reconstruction.rows_to_tsv()'s
# row_confidences return value) below which a row's READ is likely to
# contain a wrong character even though every individual field parsed
# cleanly (a clean-looking "45" that Tesseract itself was only 40% sure
# about is still a guess, not a confirmed read). This was already being
# computed and attached to row["confidence"] by run_ocr() but silently
# never looked at anywhere - parse_and_add() below now uses it to flag
# the row for a manual check, same as an existing "new medicine"/GST-
# assumed/validation-error flag already does. 65 is a practical middle
# ground: real invoice photos calibrated this session mostly landed
# 80-95% on clean rows, while rows containing an actual misread digit
# consistently pulled the row average well under 65.
LOW_ROW_CONFIDENCE = 65

# Below this tilt, cluster_into_rows()'s existing row tolerance
# (median_height * 0.9) already absorbs the vertical drift on its own -
# not worth the cost of a second OCR pass. Above it, a photographed
# invoice's rows drift enough pixels from left edge to right edge that
# a single logical row gets cut into 2-3 reconstructed rows.
# Verified against a real invoice photographed at ~1.24 degrees of tilt:
# uncorrected, row-clustering produced 24 fragments for 6 real line
# items; re-running OCR on the same photo rotated level produced exactly
# 6 clean rows.
MIN_SKEW_DEGREES = 0.4

# medicine_matcher.find_medicine_matches() is called below with a
# deliberately loose min_score=0.50 so it can still surface a best-guess
# candidate out of garbled OCR text. That's fine for SHOWING a candidate,
# but too loose to silently treat as ground truth: a fuzzy string-
# similarity score in the 0.50-0.75 range can easily land on a
# completely different, unrelated medicine that just happens to share a
# name prefix (verified against real data: "ALDIGESIC-SP ALU/ALU TAB"
# scored 0.63 against an already-existing, unrelated "ALDIGESIC P").
# Only scores at or above this line get auto-labelled "Matched"; anything
# below is shown as "Possible Match - Verify" instead, so a look-alike
# name doesn't silently add stock to the wrong medicine.
CONFIDENT_MATCH_SCORE = 0.90

# Used when a row's GST% couldn't be read from the invoice AT ALL (blank,
# not even a garbled attempt) and no already-known medicine's stored rate
# is available to fall back on either. 5% is the standard slab the large
# majority of pharma retail items fall under - a far safer default than
# silently showing "0%", which looks like a confirmed reading rather than
# an unknown one and could understate tax due. Always paired with a
# visible "GST Assumed" status flag so the pharmacist reviews it, never
# shown as if it were confirmed.
DEFAULT_GST_RATE = 5.0


class BulkImportWindow:

    def __init__(self, purchase_ref, parent):
        self.purchase_ref = purchase_ref

        self.win = tk.Toplevel(parent)
        self.win.title("Bulk Purchase Import")
        ui_style.center_window(self.win, 1050, 680, parent=parent)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self._close_window)
        # Esc key also closes this window (same as its Close button).
        # NOTE: same as the Close button, this has no "discard unsaved
        # rows?" confirmation - a tksheet cell actively being edited
        # consumes its own Escape first (cancels just that edit), so this
        # only closes the whole window when no cell edit is in progress.
        self.win.bind("<Escape>", lambda event: self._close_window())

        self._known_names_lower = set()
        self._refresh_known_names()

        # Original, pre-fuzzy-match invoice text for every row currently
        # in the review grid, index-aligned with self.reviewTable's own
        # rows (see parse_and_add()/remove_selected_row()/
        # clear_review_rows() - the only three places that add/remove
        # grid rows). Lets "Force New Item" below restore the real
        # invoice name if a fuzzy match linked this row to the WRONG
        # existing medicine (see medicine_matcher.py's dosage-form
        # guardrail for the matching-side half of this same fix).
        self._row_raw_names = []

        self.create_ui()

        # Focus set AFTER create_ui() builds its widgets - doing this
        # earlier gets silently overridden once the notebook/search
        # widgets inside create_ui() are created and packed.
        self.win.focus_force()

    def _close_window(self):
        """
        Closes this Toplevel and hands focus back to the Purchase window
        cleanly. self.win.grab_set() (below) makes this window modal -
        destroy() releases that grab automatically, but on Windows the
        underlying Purchase window doesn't always reliably regain click
        focus straight after, so rows there can look "unclickable" until
        the user alt-tabs or clicks the taskbar icon. Explicitly releasing
        the grab first and forcing focus back onto the parent avoids that
        gap instead of relying on it working out on its own.
        """
        try:
            self.win.grab_release()
        except Exception:
            pass
        self.win.destroy()
        try:
            parent_top = self.purchase_ref.frame.winfo_toplevel()
            parent_top.lift()
            parent_top.focus_force()
        except Exception:
            pass

    def _refresh_known_names(self):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT name FROM medicine_master")
        self._known_names_lower = {
            (r[0] or "").strip().lower() for r in cur.fetchall()
        }
        con.close()

    def create_ui(self):
        tk.Label(
            self.win,
            text="Bulk Purchase Import",
            bg="#1565C0", fg="white",
            font=("Segoe UI", 16, "bold"),
            pady=8
        ).pack(fill="x")

        notebook = ttk.Notebook(self.win)
        notebook.pack(fill="x", padx=10, pady=10)

        excel_tab = tk.Frame(notebook)
        text_tab = tk.Frame(notebook)
        ocr_tab = tk.Frame(notebook)

        notebook.add(excel_tab, text="📋 Paste from Excel / Google Sheets")
        notebook.add(text_tab, text="📱 Paste from WhatsApp / Text")
        notebook.add(ocr_tab, text="📷 Scan Invoice (OCR)")

        self.build_excel_tab(excel_tab)
        self.build_text_tab(text_tab)
        self.build_ocr_tab(ocr_tab)

        review_frame = tk.LabelFrame(
            self.win,
            text='Review before adding (nothing is saved until you click "Add All to Bill")',
            font=("Segoe UI", 10, "bold")
        )
        review_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self._review_cols = ("Medicine", "HSN", "Batch", "Expiry", "Purchase", "MRP", "Qty", "Company", "Pack Size", "GST%", "Status")

        # 2026-08-30: switched from make_excel_sheet() (tksheet) to
        # make_plain_sheet() (plain ttk.Treeview) - see medicine_master.py's
        # ui_style.PlainSheet docstring for the full rationale. The
        # extra calls this screen makes beyond the original three
        # (get_row_data/set_row_data/highlight_cells) are answered by
        # PlainSheet too - see its own docstring for the highlight_cells
        # approximation (whole-row color instead of a true single-cell
        # color, since ttk.Treeview has no per-cell background).
        self.reviewTable = ui_style.make_plain_sheet(
            review_frame, self._review_cols, {},
            text_columns=("Medicine", "HSN", "Batch", "Expiry", "Company", "Pack Size", "Status"),
        )
        self.reviewTable.pack(fill="both", expand=True, padx=5, pady=5)
        # 2026-08-31 real bug fix: NOT ui_style.READONLY_BINDINGS as-is -
        # that tuple includes "sort_columns" (click a header to re-sort),
        # which is exactly what corrupted a real purchase invoice import:
        # PlainSheet._sort_by() reorders the Treeview's rows in place
        # (self.move(iid, "", index)) but has no way to know this screen
        # ALSO keeps a second, position-indexed list alongside it -
        # self._row_raw_names, each row's ORIGINAL pre-match invoice text
        # (see parse_and_add()'s comment). After a header-click sort,
        # position N in the visible grid no longer lines up with
        # _row_raw_names[N] - "Force New Item"/"Edit Selected Row" on a
        # row showing "PANTOSEC 40MG TAB" then pulled back "AMLIP AT TAB"
        # instead, an unrelated row's original text left behind at its
        # PRE-sort position. commit_rows() itself was never affected (it
        # reads straight from the grid's own current values, not this
        # list - see its own comment), but the mismatch it caused inside
        # "Force New Item" was serious enough (proposing to create a
        # brand-new Medicine Master item under the WRONG name) that
        # sorting is simply turned off here instead of trying to keep two
        # separately-indexed structures in sync through every possible
        # reorder. Every other READONLY_BINDINGS entry (select/arrowkeys/
        # resize/copy) is unaffected by row order and stays enabled.
        self.reviewTable.enable_bindings(*(b for b in ui_style.READONLY_BINDINGS if b != "sort_columns"))
        ui_style.enable_row_highlight_on_select(self.reviewTable)

        rowbtns = tk.Frame(review_frame)
        rowbtns.pack(fill="x", padx=5, pady=(0, 5))

        tk.Button(
            rowbtns, text="Edit Selected Row", width=18,
            command=self.edit_selected_row
        ).pack(side="left", padx=5)

        tk.Button(
            rowbtns, text="← Shift Left", width=13,
            command=lambda: self.shift_row_columns(-1)
        ).pack(side="left", padx=5)

        tk.Button(
            rowbtns, text="Shift Right →", width=13,
            command=lambda: self.shift_row_columns(1)
        ).pack(side="left", padx=5)

        tk.Button(
            rowbtns, text="Remove Selected Row", bg="#C62828", fg="white",
            width=18, command=self.remove_selected_row
        ).pack(side="left", padx=5)

        tk.Button(
            rowbtns, text="Clear All Rows", width=15,
            command=self.clear_review_rows
        ).pack(side="left", padx=5)

        # "Force New Item" (Aug 2026, real bug fix): for a row shown as
        # "~ Possible Match - Verify", this breaks the fuzzy-matched link
        # to the existing medicine and restores the row's ORIGINAL
        # invoice text so it commits as a brand new Medicine Master
        # entry instead of updating (and potentially corrupting) the
        # wrongly-matched one. See force_new_item()'s docstring for the
        # real "OMEE SYP" -> "OMEE CAP 20'S" incident this exists for.
        tk.Button(
            rowbtns, text="Force New Item", bg="#EF6C00", fg="white",
            width=15, command=self.force_new_item
        ).pack(side="left", padx=5)

        bottom = tk.Frame(self.win)
        bottom.pack(fill="x", padx=10, pady=10)

        self.summary_label = tk.Label(bottom, text="", fg="gray")
        self.summary_label.pack(side="left")

        tk.Button(
            bottom, text="Cancel", width=12,
            command=self._close_window
        ).pack(side="right", padx=5)

        tk.Button(
            bottom, text="Add All to Bill", bg="#2E7D32", fg="white",
            font=("Segoe UI", 10, "bold"), width=20,
            command=self.commit_rows
        ).pack(side="right", padx=5)

    def build_excel_tab(self, tab):
        tk.Label(
            tab,
            text=(
                "Copy rows from Excel or Google Sheets (Product Name, Pack, Mfr, HSN, Batch, Exp, MRP, Qty, Free, Rate, Disc, GST%) "
                "and paste them directly below:"
            ),
            justify="left", wraplength=950, fg="gray"
        ).pack(anchor="w", padx=10, pady=(10, 5))

        self.excel_text = tk.Text(tab, height=8)
        self.excel_text.pack(fill="x", padx=10, pady=5)

        pastebar = tk.Frame(tab)
        pastebar.pack(anchor="w", padx=10, pady=5)

        tk.Button(
            pastebar, text="Parse Pasted Rows", bg="#1565C0", fg="white",
            command=lambda: self.parse_and_add(
                parse_tsv_text(self.excel_text.get("1.0", tk.END))
            )
        ).pack(side="left")

        tk.Label(tab, text=(
            "OR: if a supplier can email/WhatsApp their invoice as a file "
            "(their own billing software's export, e.g. VarthagamSoft) "
            "instead of a paper copy - import it directly, no OCR needed:"
        ), justify="left", wraplength=950, fg="gray").pack(anchor="w", padx=10, pady=(10, 0))

        tk.Button(
            pastebar, text="📂 Import from File (.csv / .xlsx)", bg="#00695C", fg="white",
            command=self.import_from_file
        ).pack(side="left", padx=(10, 0))

    def import_from_file(self):
        """
        Header-aware import for a spreadsheet a SUPPLIER exported
        directly from their own billing software - see
        spreadsheet_import.py's module docstring for why this reads
        column headers BY NAME instead of assuming parse_tsv_text()'s
        fixed column order (a supplier's own export can be laid out
        however their software happens to produce it).
        """
        path = filedialog.askopenfilename(
            filetypes=[("Spreadsheet files", "*.csv *.xlsx *.xls"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            if path.lower().endswith(".csv"):
                with open(path, "rb") as f:
                    raw = f.read()
                rows = spreadsheet_import.parse_csv_bytes(raw, _to_float, _to_int)
                header_row = None
                if not rows:
                    # Re-read just the header row to give a specific
                    # error (empty file vs. unrecognised headers are
                    # different problems the pharmacist needs to act on
                    # differently).
                    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
                    header_row = next((r for r in reader if any((c or "").strip() for c in r)), None)
            else:
                if not _ensure_openpyxl_import():
                    ui_popups.show_error(self.win, 
                        "Missing Library",
                        "openpyxl library is not installed. Run 'pip install openpyxl' in terminal."
                    )
                    return
                wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
                sheet = wb.active
                all_rows = [list(r) for r in sheet.iter_rows(values_only=True)
                            if any(c is not None and str(c).strip() for c in r)]
                wb.close()
                # find_header_row() scans for the header instead of
                # assuming row 0 - same reasoning as parse_csv_text()'s
                # own fix (see spreadsheet_import.py), needed for an
                # invoice-style export (shop info/Invoice No above the
                # real "SI, Medicine, ..." header row) to import correctly.
                header_idx, mapping = spreadsheet_import.find_header_row(all_rows) if all_rows else (None, {})
                header_row = all_rows[header_idx] if header_idx is not None else None
                rows = (
                    spreadsheet_import.rows_from_mapped_columns(all_rows[header_idx + 1:], mapping, _to_float, _to_int)
                    if header_idx is not None else []
                )
        except Exception as e:
            ui_popups.show_error(self.win, "Import Error", str(e))
            return

        if not rows:
            if header_row:
                ui_popups.show_warning(self.win, 
                    "No Product Name Column Found",
                    "Couldn't find a column that looks like a medicine/product name in this "
                    "file's headers.\n\nHeaders found:\n" + ", ".join(str(h) for h in header_row if h) +
                    "\n\nRename that column to something like \"Product Name\" or \"Description\", "
                    "or use the Paste tab instead."
                )
            else:
                ui_popups.show_warning(self.win, "Empty File", "No data rows were found in this file.")
            return

        self.parse_and_add(rows)

    def build_text_tab(self, tab):
        tk.Label(
            tab,
            text='Paste a loosely formatted list - e.g. "Dolo 650 x30".',
            justify="left", wraplength=950, fg="gray"
        ).pack(anchor="w", padx=10, pady=(10, 5))

        self.text_box = tk.Text(tab, height=8)
        self.text_box.pack(fill="x", padx=10, pady=5)

        tk.Button(
            tab, text="Parse Pasted List", bg="#1565C0", fg="white",
            command=lambda: self.parse_and_add(
                parse_free_text(self.text_box.get("1.0", tk.END))
            )
        ).pack(anchor="w", padx=10, pady=5)

    def build_ocr_tab(self, tab):
        if not _ensure_ocr_imports():
            tk.Label(tab, text="OCR not available.", fg="#C62828").pack(padx=10, pady=20)
            return
        tk.Label(tab, text="Choose invoice image.").pack(padx=10, pady=5)
        pathbar = tk.Frame(tab)
        pathbar.pack(fill="x", padx=10, pady=5)
        self.ocr_path_var = tk.StringVar()
        tk.Entry(pathbar, textvariable=self.ocr_path_var, width=70, state="readonly").pack(side="left", padx=(0, 5))
        tk.Button(pathbar, text="Choose Image...", command=self.choose_ocr_image).pack(side="left")
        # Optional pre-processing step - letterhead/footer text outside the
        # product table is exactly what confuses row/column detection on a
        # cluttered invoice (see ocr_table_reconstruction.py's header/
        # footer keyword trimming, which already tries to do this
        # automatically but can miss on an invoice whose header text OCRs
        # too poorly for any keyword to survive). Letting the pharmacist
        # manually box out just the table removes that guesswork entirely
        # for the image OCR actually runs on.
        tk.Button(pathbar, text="✂ Crop to Table...", command=self._open_crop_dialog).pack(side="left", padx=(5, 0))

        runbar = tk.Frame(tab)
        runbar.pack(anchor="w", fill="x", padx=10, pady=5)
        self.btn_run_ocr = tk.Button(runbar, text="Run OCR & Parse", bg="#1565C0", fg="white", command=self.run_ocr)
        self.btn_run_ocr.pack(side="left")
        # Runs on the Tkinter main thread would otherwise freeze the whole
        # window ("Not Responding") for as long as the two Tesseract
        # subprocess calls + skew-slope math take - which can be a long
        # time for a large/dense invoice photo, with zero feedback that
        # anything is happening. This label is the only visible sign of
        # progress while run_ocr() hands the real work to a background
        # thread (see run_ocr/_ocr_worker/_poll_ocr_queue below).
        self.ocr_status_label = tk.Label(runbar, text="", fg="#1565C0")
        self.ocr_status_label.pack(side="left", padx=10)

        extrabar = tk.Frame(tab)
        extrabar.pack(anchor="w", fill="x", padx=10, pady=(0, 5))
        tk.Button(extrabar, text="💾 Export OCR Debug Data", bg="#607D8B", fg="white", command=self.export_ocr_debug_data).pack(side="left")
        tk.Button(extrabar, text="🏷 Manage Supplier Templates", bg="#00695C", fg="white", command=self.open_supplier_templates).pack(side="left", padx=(8, 0))

        self.ocr_raw_text = tk.Text(tab, height=6, state="disabled", bg="#F5F5F5")
        self.ocr_raw_text.pack(fill="x", padx=10, pady=(0, 10))
        self._ocr_result_queue = queue.Queue()

    def choose_ocr_image(self):
        path = filedialog.askopenfilename(
            filetypes=[("Image or PDF files", "*.jpg *.jpeg *.png *.pdf")]
        )
        if not path:
            return

        if path.lower().endswith(".pdf"):
            converted_path = self._convert_pdf_to_image(path)
            if converted_path:
                self.ocr_path_var.set(converted_path)
        else:
            self.ocr_path_var.set(path)

        # Every fresh choice starts a new "original" for _open_crop_dialog()
        # to crop from - without resetting this here, cropping a SECOND,
        # different invoice would keep re-cropping the FIRST invoice's
        # original file instead (a stale attribute left over from before).
        chosen = self.ocr_path_var.get()
        if chosen:
            self._ocr_original_path = chosen

    def _convert_pdf_to_image(self, pdf_path):
        """
        PDF-ஐ முதல் page-ஐ high-res JPG-ஆ convert பண்ணி, அந்த JPG path-ஐ
        திருப்பி தரும். Tesseract PDF-ஐ நேரடியா படிக்காது - image வேணும்.
        """
        try:
            from pdf2image import convert_from_path
        except ImportError:
            ui_popups.show_error(self.win, 
                "Missing Library",
                "PDF support needs the 'pdf2image' package.\n\n"
                "Run: pip install pdf2image\n"
                "and install Poppler (see setup docs)."
            )
            return None

        try:
            pages = convert_from_path(
                pdf_path, dpi=400,
                poppler_path=_resolve_poppler_path()
            )
            if not pages:
                ui_popups.show_error(self.win, "PDF Error", "No pages found in PDF.")
                return None

            out_path = os.path.splitext(pdf_path)[0] + "_page1.jpg"
            pages[0].save(out_path, "JPEG")
            return out_path
        except Exception as e:
            ui_popups.show_error(self.win, 
                "PDF Conversion Error",
                f"Could not convert PDF to image.\n\n{e}\n\n"
                "Make sure Poppler is installed and in your PATH."
            )
            return None

    def _open_crop_dialog(self):
        """
        Lets the pharmacist drag a box around just the printed product
        table on the chosen invoice photo before OCR runs, so letterhead
        text above (shop name/address/GSTIN) and totals/signature text
        below never reach Tesseract at all. ocr_table_reconstruction.py's
        find_table_bounds() already tries to trim these automatically
        from keywords, but that's a best-effort guess that can miss on a
        cluttered or poorly-printed invoice - a manual crop removes the
        guesswork entirely for the specific image about to be scanned.

        Always crops from self._ocr_original_path (the untouched photo as
        first chosen), never from a PREVIOUS crop's output - re-opening
        this dialog a second time must re-crop the full photo, not crop an
        already-cropped image down even further.
        """
        path = self.ocr_path_var.get()
        if not path or not os.path.exists(path):
            ui_popups.show_warning(self.win, "No Image", "Choose an invoice image first.")
            return
        if not _ensure_ocr_imports():
            return
        source_path = getattr(self, "_ocr_original_path", None) or path

        try:
            from PIL import ImageTk
            img = Image.open(source_path)
            # Phone photos frequently carry an EXIF rotation tag instead of
            # actually rotating the pixel data - without correcting for it
            # here, the crop box the pharmacist drags (against the
            # correctly-rotated preview) would land on the wrong region of
            # the raw, still-sideways pixel data underneath.
            img = ImageOps.exif_transpose(img)
        except Exception as e:
            ui_popups.show_error(self.win, "Image Error", f"Could not open image.\n\n{e}")
            return

        win = tk.Toplevel(self.win)
        win.title("Crop to Product Table")
        win.grab_set()
        win.bind("<Escape>", lambda event: win.destroy())
        win.focus_force()

        tk.Label(
            win,
            text=(
                "Drag a box around JUST the product table (skip the shop letterhead "
                "at the top and totals/signature at the bottom) - this stops OCR "
                "from getting confused by text outside the table."
            ),
            wraplength=760, justify="left", fg="gray"
        ).pack(padx=10, pady=(10, 5))

        # Scales the PREVIEW only - img itself (full resolution) is what
        # actually gets cropped below, so OCR still runs on a full-quality
        # image regardless of how small the on-screen preview is shown.
        max_w, max_h = 900, 650
        orig_w, orig_h = img.size
        scale = min(max_w / orig_w, max_h / orig_h, 1.0)
        disp_w, disp_h = max(1, int(orig_w * scale)), max(1, int(orig_h * scale))
        display_img = img.resize((disp_w, disp_h), Image.LANCZOS) if scale < 1.0 else img.copy()

        canvas = tk.Canvas(win, width=disp_w, height=disp_h, cursor="cross", bg="black")
        canvas.pack(padx=10, pady=5)
        tk_img = ImageTk.PhotoImage(display_img)
        canvas.create_image(0, 0, anchor="nw", image=tk_img)
        canvas.image = tk_img  # keep a reference - tkinter drops the image otherwise

        sel = {"start": None, "rect": None}

        def _on_press(event):
            sel["start"] = (event.x, event.y)
            if sel["rect"]:
                canvas.delete(sel["rect"])
                sel["rect"] = None

        def _on_drag(event):
            if not sel["start"]:
                return
            if sel["rect"]:
                canvas.delete(sel["rect"])
            x0, y0 = sel["start"]
            sel["rect"] = canvas.create_rectangle(x0, y0, event.x, event.y, outline="#00E676", width=2)

        canvas.bind("<ButtonPress-1>", _on_press)
        canvas.bind("<B1-Motion>", _on_drag)

        btns = tk.Frame(win)
        btns.pack(fill="x", padx=10, pady=10)

        def _apply_crop():
            if not sel["rect"]:
                ui_popups.show_warning(win, "No Selection", "Drag a box around the table first.")
                return
            x0, y0, x1, y1 = canvas.coords(sel["rect"])
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))
            if (x1 - x0) < 20 or (y1 - y0) < 20:
                ui_popups.show_warning(win, "Selection Too Small", "Drag a larger box around the table.")
                return

            # Map the ON-SCREEN (possibly scaled-down for display) box back
            # to the ORIGINAL image's own pixel coordinates before
            # cropping - cropping display_img directly would hand OCR a
            # low-resolution image, undoing _preprocess_for_ocr()'s own
            # upscale-if-too-small step for no reason.
            crop_box = (
                int(x0 / scale), int(y0 / scale),
                int(x1 / scale), int(y1 / scale),
            )
            cropped = img.crop(crop_box)
            base, _ext = os.path.splitext(source_path)
            out_path = base + "_cropped.jpg"
            cropped.convert("RGB").save(out_path, "JPEG", quality=95)

            self._ocr_original_path = source_path
            self.ocr_path_var.set(out_path)
            win.destroy()
            ui_popups.show_info(self.win, "Cropped", 'Cropped image ready - now click "Run OCR & Parse".')

        ui_style.flat_button(btns, "Use This Crop", theme.STATUS_SUCCESS, _apply_crop, width=16).pack(side="left")
        ui_style.flat_button(
            btns, "Cancel", theme.TEXT_MUTED,
            win.destroy, width=12
        ).pack(side="left", padx=(8, 0))

    def open_supplier_templates(self):
        """
        Small admin dialog for calibrating a supplier's known column
        order once (see ocr_supplier_templates.py's module docstring for
        why this exists). Plain ttk.Treeview here, not tksheet - this is
        a short admin list (a handful of regular suppliers, not hundreds
        of billing rows), so Treeview's simplicity is a better fit than
        pulling in Excel-grid styling for a screen that isn't one.
        """
        win = tk.Toplevel(self.win)
        win.title("Manage Supplier Templates")
        ui_style.center_window(win, 640, 520, parent=self.win)
        win.grab_set()
        # Esc key also closes this popup (same as Close/the window's X).
        win.bind("<Escape>", lambda event: win.destroy())
        win.focus_force()

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring) - already modal
        # (grab_set() above), so only the look changes here.
        body = ui_style.popup_header(win, "Supplier OCR Templates", icon="🧾")

        listframe = tk.Frame(body, bg=theme.SURFACE_WHITE)
        listframe.pack(fill="both", expand=True, padx=10, pady=10)

        cols = ("Supplier", "GSTIN", "Column Order")
        tree = ttk.Treeview(listframe, columns=cols, show="headings", height=8)
        for c, w in zip(cols, (160, 140, 260)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(listframe, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _refresh_list():
            tree.delete(*tree.get_children())
            for row_id, supplier_name, gstin, column_order in ocr_supplier_templates.load_templates():
                tree.insert("", "end", iid=str(row_id), values=(supplier_name, gstin, column_order))
        _refresh_list()

        def _delete_selected():
            sel = tree.selection()
            if not sel:
                ui_popups.show_info(win, "Select a Row", "Select a template to delete first.")
                return
            if not ui_popups.show_confirmation(win, "Confirm Delete", "Delete this supplier template?"):
                return
            ocr_supplier_templates.delete_template(int(sel[0]))
            _refresh_list()

        ui_style.flat_button(body, "Delete Selected", theme.STATUS_DANGER, _delete_selected).pack(anchor="w", padx=10)

        form = tk.LabelFrame(
            body, text="Add / Update Template", font=("Segoe UI", 10, "bold"),
            bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL,
        )
        form.pack(fill="x", padx=10, pady=10)

        def _field_kwargs():
            return dict(
                font=("Segoe UI", 10), bg=theme.SURFACE_FIELD, relief="flat",
                highlightthickness=1, highlightbackground=theme.BORDER_DEFAULT,
                highlightcolor=theme.BORDER_FOCUS,
            )

        tk.Label(form, text="Supplier Name", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL).grid(row=0, column=0, sticky="w", padx=5, pady=5)
        name_var = tk.StringVar()
        tk.Entry(form, textvariable=name_var, width=40, **_field_kwargs()).grid(row=0, column=1, padx=5, pady=5, sticky="w", ipady=2)

        tk.Label(form, text="GSTIN (15 characters)", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL).grid(row=1, column=0, sticky="w", padx=5, pady=5)
        gstin_var = tk.StringVar()
        tk.Entry(form, textvariable=gstin_var, width=40, **_field_kwargs()).grid(row=1, column=1, padx=5, pady=5, sticky="w", ipady=2)

        tk.Label(form, text="Column Order (comma-separated)", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL).grid(row=2, column=0, sticky="nw", padx=5, pady=5)
        order_var = tk.StringVar()
        tk.Entry(form, textvariable=order_var, width=60, **_field_kwargs()).grid(row=2, column=1, padx=5, pady=5, sticky="w", ipady=2)

        tk.Label(
            form,
            text="Valid roles:\n" + ocr_supplier_templates.ROLE_LEGEND,
            bg=theme.SURFACE_WHITE, justify="left", fg=theme.TEXT_MUTED, font=("Segoe UI", 8)
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 5))

        def _save():
            try:
                ocr_supplier_templates.save_template(name_var.get(), gstin_var.get(), order_var.get())
            except ValueError as e:
                ui_popups.show_error(win, "Invalid Template", str(e))
                return
            name_var.set(""); gstin_var.set(""); order_var.set("")
            _refresh_list()
            ui_popups.show_info(win, "Saved", "Supplier template saved.")

        ui_style.flat_button(form, "Save Template", theme.STATUS_SUCCESS, _save, width=14).grid(
            row=4, column=1, sticky="w", padx=5, pady=(0, 8)
        )

        ui_style.flat_button(body, "Close", theme.PRIMARY, win.destroy).pack(pady=10)

    @staticmethod
    def _auto_correct_orientation(img):
        """
        Detects and corrects 90/180/270-degree rotated invoice photos
        using Tesseract's own Orientation & Script Detection (OSD),
        before the main OCR pass runs.

        Verified against a real invoice photographed upside-down: this
        isn't a case of "lower accuracy" - Tesseract cannot read text
        rotated that far off-axis at all, and produces completely
        unreadable garbage for the whole page (every field wrong, not
        just some). OSD correctly identified "Rotate: 180" for it, and
        applying that correction turned the same image into a mostly-
        correct read. This is the "handle rotated invoices" gap already
        tracked in BUG_LOG.md.

        Falls back to the original image untouched if OSD can't
        determine an orientation (common on sparse/low-contrast images)
        rather than guessing and risking rotating an already-correct image.
        """
        try:
            osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
            rotation = int(osd.get("rotate", 0) or 0)
            if rotation:
                img = img.rotate(-rotation, expand=True)
        except Exception:
            pass
        return img

    @staticmethod
    def _estimate_skew_degrees(words, high_conf=60, min_high_conf_words=10, cap_points=800):
        """
        Estimates the invoice photo's small in-plane tilt (a few degrees,
        NOT the 90/180/270 rotation _auto_correct_orientation handles)
        directly from OCR word positions - no extra image processing.

        A photographed invoice is one flat sheet, so every word on the
        page shares the same tilt angle. Computes the slope between
        EVERY pair of word centers (capped at cap_points words for
        performance) and takes the MEDIAN of all pairwise slopes
        (Theil-Sen style), which is robust to the handful of misplaced/
        garbled word boxes any real OCR pass has - a plain two-point or
        least-squares fit would be thrown off by those outliers.

        Prefers only high-confidence (>=60) words when there are enough
        of them, falling back to the full word list otherwise. Verified
        against a real invoice that the full, unfiltered word list can
        give an unstable/wrong-signed estimate depending on exactly
        which low-confidence noise words happen to survive the caller's
        confidence filter (e.g. +1.0 vs the correct -1.2 degrees, purely
        from a handful of stray low-confidence words shifting the
        median) - restricting to well-detected words first removes that
        instability. A RANDOM SAMPLE of pairs was tried first and
        rejected: it reintroduced the same instability from sampling
        noise, whereas using every pair among a smaller high-confidence
        set is both more stable and still fast (well under cap_points).

        Pairs closer than 80px apart horizontally are skipped because
        their slope is dominated by pixel-level box-detection noise
        rather than the actual page tilt.
        """
        strong = [w for w in words if w.get("conf", 0) >= high_conf]
        pool = strong if len(strong) >= min_high_conf_words else words
        pts = [(w["left"] + w["width"] / 2, w["top"] + w["height"] / 2) for w in pool]
        if len(pts) < min_high_conf_words:
            return 0.0

        if len(pts) > cap_points:
            pts = random.Random(0).sample(pts, cap_points)  # fixed seed - deterministic per image

        slopes = []
        n = len(pts)
        for i in range(n):
            x1, y1 = pts[i]
            for j in range(i + 1, n):
                x2, y2 = pts[j]
                dx = x2 - x1
                if abs(dx) < 80:
                    continue
                slopes.append((y2 - y1) / dx)

        if not slopes:
            return 0.0
        return math.degrees(math.atan(statistics.median(slopes)))

    def _preprocess_for_ocr(self, img):
        """
        Auto-orient + upscale + contrast-stretch + adaptive (Otsu)
        black/white threshold. Otsu picks the cutoff from each image's
        own brightness histogram instead of a hardcoded number - a fixed
        threshold that works for one invoice photo can wash out a
        darker or lighter one taken in different lighting.
        """
        img = self._auto_correct_orientation(img)
        img = ImageOps.grayscale(img)

        width, height = img.size
        if width < 1800:
            scale = 1800 / width
            img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

        img = ImageOps.autocontrast(img, cutoff=1)
        threshold = self._otsu_threshold(img)
        img = img.point(lambda p: 255 if p > threshold else 0)
        return img

    @staticmethod
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

    @staticmethod
    def _extract_words(data):
        """
        Turns pytesseract.image_to_data()'s raw dict-of-lists into our
        word-dict list, applying the MIN_WORD_CONFIDENCE filter. Shared
        by run_ocr()'s first (skew-detection) and, when needed, second
        (deskewed) OCR pass so both apply identical filtering.
        """
        words = []
        for i in range(len(data.get("text", []))):
            text = data["text"][i].strip()
            conf = data["conf"][i]
            if not text or str(conf) in ("-1", ""):
                continue
            conf_val = int(float(conf))
            # See MIN_WORD_CONFIDENCE above - a real-but-very-low
            # confidence score is Tesseract's way of saying "I'm not
            # sure this is even text"; letting it through as if it
            # were a real word pollutes row clustering and column
            # detection for every genuine row on the page.
            if conf_val < MIN_WORD_CONFIDENCE:
                continue
            words.append({
                "text": text, "conf": conf_val,
                "left": data["left"][i], "top": data["top"][i],
                "width": data["width"][i], "height": data["height"][i],
            })
        return words

    def export_ocr_debug_data(self):
        path = self.ocr_path_var.get()
        if not path or not os.path.exists(path):
            ui_popups.show_warning(self.win, "No Image", "Choose an invoice image first.")
            return
        try:
            img = Image.open(path)
            processed = self._preprocess_for_ocr(img)
            data = pytesseract.image_to_data(processed, config="--psm 6", output_type=pytesseract.Output.DICT)

            out_path = os.path.splitext(path)[0] + "_ocr_debug.txt"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("text\tleft\ttop\twidth\theight\tconf\n")
                for i in range(len(data.get("text", []))):
                    text = data["text"][i].strip()
                    conf = data["conf"][i]
                    if not text or str(conf) in ("-1", ""):
                        continue
                    f.write(f"{text}\t{data['left'][i]}\t{data['top'][i]}\t{data['width'][i]}\t{data['height'][i]}\t{conf}\n")

            ui_popups.show_info(self.win, "Exported", f"Raw OCR word data saved to:\n{out_path}")
        except Exception as e:
            ui_popups.show_error(self.win, "Export Error", str(e))

    def run_ocr(self):
        path = self.ocr_path_var.get()
        if not path or not os.path.exists(path):
            return
        # BUG FIX (blank/"Not Responding" freeze): the whole OCR pipeline
        # below - two full Tesseract subprocess passes (original +
        # skew-corrected) plus _estimate_skew_degrees()'s O(n^2) slope
        # math over up to 800 word-pairs - used to run directly on this
        # call, i.e. on the Tkinter main thread. For a large/dense
        # invoice photo that can take a long time, and since Tkinter
        # can't repaint while its main thread is busy, the whole window
        # looked identically frozen whether it was "still working" or
        # genuinely stuck - no way to tell them apart, no progress shown.
        # Moving the real work to a background thread keeps the window
        # responsive regardless of how long OCR takes. The thread (see
        # _ocr_worker) must NOT touch any Tkinter widget directly - it
        # only computes and hands the result to _ocr_result_queue;
        # _poll_ocr_queue(), scheduled via self.win.after() so it always
        # runs on the main thread, is the only place allowed to update
        # the UI with that result.
        if self.btn_run_ocr["state"] == "disabled":
            return  # already running - ignore a double-click
        self.btn_run_ocr.config(state="disabled")
        self.ocr_status_label.config(text="Processing image... this can take a while for large photos.")
        threading.Thread(target=self._ocr_worker, args=(path,), daemon=True).start()
        self.win.after(100, self._poll_ocr_queue)

    @staticmethod
    def _get_own_gstin():
        """
        Reads this pharmacy's own GSTIN from Settings, so
        ocr_supplier_templates.extract_gstin() can exclude it from
        consideration - a properly formatted GST invoice prints BOTH the
        supplier's GSTIN and the buyer's (this pharmacy's own), and
        without this exclusion a page where the buyer's GSTIN happens to
        be read first could get matched against a supplier template
        instead of the real supplier's GSTIN. Runs on the OCR worker
        thread, so this is a plain read-only sqlite3 query, not routed
        through any Tkinter-owned connection. Returns "" (never raises)
        if Settings hasn't been filled in yet or the table doesn't exist
        - extract_gstin() treats an empty exclude_gstin as "nothing to
        exclude", so this fails safe.
        """
        try:
            con = sqlite3.connect(DB_NAME)
            cur = con.cursor()
            cur.execute("SELECT gstin FROM settings LIMIT 1")
            row = cur.fetchone()
            con.close()
            return (row[0] or "") if row else ""
        except sqlite3.Error:
            return ""

    def _ocr_worker(self, path):
        """
        Runs on a background thread - see run_ocr()'s comment above for
        why. Pure computation only: reads the image file, calls Tesseract,
        reconstructs the table. Must never touch a Tkinter widget (not
        thread-safe); the only communication back to the UI is putting a
        plain-data result tuple on self._ocr_result_queue.
        """
        try:
            img = Image.open(path)
            processed = self._preprocess_for_ocr(img)
            config = "--psm 6"

            # Real invoices are tables. Plain image_to_string just emits
            # Tesseract's best-guess reading order with no column
            # awareness, which scrambles genuinely tabular layouts. This
            # reconstructs the actual table structure from word
            # positions first (letterhead/footer excluded, columns found
            # by cross-row consensus), and only falls back to naive
            # line-by-line text if that doesn't work out.
            data = pytesseract.image_to_data(processed, config=config, output_type=pytesseract.Output.DICT)
            words = self._extract_words(data)

            # Photographed invoices are rarely perfectly level. A small
            # tilt (a couple of degrees) is invisible to the eye but
            # means a single printed row's words drift several pixels
            # from left edge to right edge - enough to exceed
            # cluster_into_rows()'s tolerance and split one real row
            # into two or three reconstructed ones. _auto_correct_
            # orientation() above only fixes gross 90/180/270 rotation,
            # not this. Detected from the word positions we already
            # have, so this costs nothing when the photo is already
            # level (the common case).
            skew = self._estimate_skew_degrees(words)
            if abs(skew) >= MIN_SKEW_DEGREES:
                deskewed = img.rotate(skew, expand=True, resample=Image.BICUBIC, fillcolor="white")
                deskewed_processed = self._preprocess_for_ocr(deskewed)
                deskewed_data = pytesseract.image_to_data(deskewed_processed, config=config, output_type=pytesseract.Output.DICT)
                deskewed_words = self._extract_words(deskewed_data)
                # Only trust the deskewed pass if it actually found at
                # least as much text as the original - guards against a
                # bad angle estimate (e.g. from a sparse/low-text image)
                # making things worse instead of better.
                if len(deskewed_words) >= len(words):
                    words = deskewed_words

            # Supplier template lookup - see ocr_supplier_templates.py's
            # module docstring for the reasoning. Looks at the FULL word
            # list (letterhead included), not just the product-table
            # rows reconstruct_table_rows() below extracts, since the
            # GSTIN is printed above the table. A matching template
            # means known suppliers get exact positional column mapping
            # instead of the generic engine's shape-based guessing;
            # no match (new/unknown supplier) falls through to the
            # generic engine completely unchanged, per-row below.
            gstin = ocr_supplier_templates.extract_gstin(words, exclude_gstin=self._get_own_gstin())
            all_templates = ocr_supplier_templates.load_templates()
            template = None
            if gstin:
                template = ocr_supplier_templates.find_matching_template(gstin, all_templates)
            if not template:
                # GSTIN is small print - a single digit misread as a
                # letter (e.g. "4991" -> "AGSI") breaks its SHAPE match
                # entirely, not just a character or two, so extract_gstin
                # can come back empty even for a known supplier. The
                # supplier's own name is printed large in the letterhead
                # and OCRs far more reliably - try that next before
                # giving up and falling through to the generic engine.
                header_words = ocr_supplier_templates.extract_header_words(words)
                template = ocr_supplier_templates.find_template_by_name(header_words, all_templates)

            table_result = ocr_table_reconstruction.reconstruct_table_rows(words)

            if table_result and len(table_result[0]) >= 2:
                tsv_lines, row_confidences = table_result
                preview_text = "\n".join(tsv_lines)
                mode_note = f"[Table mode: {len(tsv_lines)} row(s) reconstructed from layout]\n\n"
                if template:
                    mode_note += f"[Matched supplier template: {template[1]}]\n\n"

                parsed_rows = []
                for i, line in enumerate(tsv_lines):
                    cells = line.split("\t")
                    # A real product row always has at least one price/qty/
                    # code number in it somewhere, even if the medicine name
                    # itself got badly garbled. A row with zero digits
                    # anywhere is leftover noise (stray marks/paper texture
                    # Tesseract mistook for a word, e.g. "fe", "Me", "es")
                    # that survived the confidence filter above - not a
                    # missed medicine, so it's safe to drop rather than
                    # show the user an empty row to manually delete.
                    if not any(re.search(r"\d", c) for c in cells):
                        continue

                    fields = None
                    if template:
                        column_order = template[3].split(",")
                        # apply_template() returns None when this
                        # particular row's cell count is too far off
                        # from what the template expects to trust a
                        # positional guess - falling back to the generic
                        # engine for just that one row means a template
                        # match can never make a row WORSE than not
                        # having one, only better for rows it can handle.
                        fields = ocr_supplier_templates.apply_template(cells, column_order)
                    if not fields:
                        fields = ocr_table_reconstruction.extract_invoice_row_fields(cells)
                    if not fields or not fields.get("name"):
                        continue
                    row = {
                        "name": fields["name"],
                        "hsn": fields.get("hsn", ""),
                        "batch": fields.get("batch", ""),
                        "expiry": fields.get("expiry", ""),
                        "purchase": _to_float(fields.get("purchase", "0")),
                        "mrp": _to_float(fields.get("mrp", "0")),
                        "qty": _to_int(fields.get("qty", "1"), default=1),
                        "company": fields.get("company", ""),
                        "pack_size": fields.get("pack_size", "1"),
                        "gst": _to_float(fields.get("gst", "0")),
                        "gst_uncertain": fields.get("gst_uncertain", False),
                        # extract_invoice_row_fields() already runs validate_row()
                        # internally (RATE_GT_MRP / AMOUNT_MISMATCH checks) - it was
                        # being computed and then silently discarded here.
                        "errors": fields.get("errors", []),
                    }
                    if row_confidences and i < len(row_confidences) and row_confidences[i] is not None:
                        row["confidence"] = row_confidences[i]
                    parsed_rows.append(row)
            else:
                raw_text = pytesseract.image_to_string(processed, config=config)
                preview_text = raw_text
                mode_note = "[Free-text mode: no clear table structure detected]\n\n"
                parsed_rows = parse_free_text(raw_text)

            self._ocr_result_queue.put(("ok", mode_note, preview_text, parsed_rows))

        except Exception as e:
            self._ocr_result_queue.put(("error", e))

    def _poll_ocr_queue(self):
        """
        Scheduled repeatedly via self.win.after() until the background
        worker's result shows up - this is the only place allowed to
        touch Tkinter widgets with that result, since it always runs on
        the main thread. Guards against the window having been closed
        (Cancel/X) while OCR was still running in the background: the
        worker thread itself is harmless to leave running (daemon=True,
        touches no widgets), but polling must stop instead of calling
        .after() on a destroyed window.
        """
        if not self.win.winfo_exists():
            return
        try:
            result = self._ocr_result_queue.get_nowait()
        except queue.Empty:
            self.win.after(100, self._poll_ocr_queue)
            return

        self.btn_run_ocr.config(state="normal")
        self.ocr_status_label.config(text="")

        if result[0] == "error":
            e = result[1]
            if "tesseract is not installed" in str(e).lower() or "TesseractNotFoundError" in type(e).__name__:
                ui_popups.show_error(self.win, 
                    "Tesseract Not Found",
                    "The Tesseract OCR engine itself isn't installed or its path is wrong.\n\n"
                    f"Expected location: {TESSERACT_CMD}\n\n"
                    "Download it from https://github.com/UB-Mannheim/tesseract/wiki"
                )
            else:
                ui_popups.show_error(self.win, "OCR Error", str(e))
            return

        _, mode_note, preview_text, parsed_rows = result
        self.ocr_raw_text.config(state="normal")
        self.ocr_raw_text.delete("1.0", tk.END)
        self.ocr_raw_text.insert("1.0", mode_note + preview_text)
        self.ocr_raw_text.config(state="disabled")

        self.parse_and_add(parsed_rows)

    def parse_and_add(self, parsed_rows):
        if not parsed_rows:
            ui_popups.show_info(self.win, "Nothing found", "No rows could be parsed.")
            return

        current_data = self.reviewTable.get_sheet_data()
        start_row = len(current_data)
        flagged_rows = []
        # (row_idx, column_idx, bg, fg) - specific CELLS to colour on top
        # of the plain yellow row-highlight above, so the pharmacist's eye
        # goes straight to the one or two values actually worth checking
        # instead of having to re-read every column of a flagged row.
        # Column indices match self._review_cols: 0 Medicine, 1 HSN,
        # 2 Batch, 3 Expiry, 4 Purchase, 5 MRP, 6 Qty, 7 Company,
        # 8 Pack Size, 9 GST%, 10 Status.
        cell_highlights = []

        for row in parsed_rows:
            raw_name = (row.get("name") or "").strip()
            if not raw_name: continue

            # ─── Fuzzy Match & Smart Generic Linking ───
            raw_company = (row.get("company") or row.get("mfr") or "").strip()
            matches = medicine_matcher.find_medicine_matches(raw_name, ocr_company=raw_company, top_n=1, min_score=0.50)

            if matches:
                match_score = matches[0][-1]
                name = matches[0][0]
                # A silent "✓ Matched" is a real risk below here - e.g. a
                # real invoice row "ALDIGESIC-SP ALU/ALU TAB" scored 0.63
                # against an unrelated, already-existing "ALDIGESIC P" and
                # would have silently added stock to the wrong medicine.
                # min_score=0.50 above is deliberately loose (finds the
                # best candidate even from garbled OCR text), so anything
                # below CONFIDENT_MATCH_SCORE gets surfaced for a manual
                # look instead of being auto-accepted.
                if match_score >= CONFIDENT_MATCH_SCORE:
                    # மிகச் சரியாகப் பொருந்தியexisting மருந்துப் பெயர்
                    status_label = "✓ Matched"
                    tag = ()
                else:
                    status_label = "≈ Possible Match - Verify"
                    tag = ("new",)
            else:
                # முற்றிலும் புதிய மருந்து
                name = raw_name
                status_label = "⚠ New"
                tag = ("new",)

            # GST% couldn't be trusted from this invoice's OCR (garbled or
            # completely missing - see gst_uncertain's definition). Rather
            # than leave it showing the misleadingly-confident "0%" that
            # _to_float("") already produced, try a known-good source
            # before resorting to a flagged guess:
            #   1. If this row confidently matched an EXISTING medicine
            #      (CONFIDENT_MATCH_SCORE+), that medicine's own stored
            #      GST rate (from whenever it was first entered/verified)
            #      is far more trustworthy than re-reading a small percent
            #      sign off a fresh photo every single time.
            #   2. Otherwise (a genuinely new medicine, or an existing one
            #      with no GST recorded yet) fall back to DEFAULT_GST_RATE
            #      - the common pharma slab - but ALWAYS with a visible
            #      "Assumed" flag, since this is a guess, not a reading.
            if row.get("gst_uncertain"):
                master_gst = matches[0][3] if matches and match_score >= CONFIDENT_MATCH_SCORE else None
                if master_gst is not None:
                    row["gst"] = float(master_gst)
                    status_label += " | GST from Master"
                else:
                    row["gst"] = DEFAULT_GST_RATE
                    status_label += f" | ⚠ GST Assumed {DEFAULT_GST_RATE:g}% - Verify"
                    tag = ("new",)
            # -------------------------------------------

            # Rate > MRP or Qty*Rate != Amount almost always means a field
            # landed in the wrong column (OCR) or a typo (Excel paste) -
            # this was already being computed (validate_row) but never
            # shown to the user, so bad rows went straight into stock
            # without a second look.
            errors = row.get("errors") or []
            if errors:
                status_label += " | ⚠ " + ", ".join(errors)
                tag = ("new",)

            # Row-level average OCR confidence (see LOW_ROW_CONFIDENCE's
            # comment near the top of this file) - another signal that
            # run_ocr() was already computing and attaching to
            # row["confidence"] but nothing downstream ever looked at.
            row_conf = row.get("confidence")
            if row_conf is not None and row_conf < LOW_ROW_CONFIDENCE:
                status_label += f" | ⚠ Low OCR Confidence (~{row_conf:.0f}%) - Recheck this row"
                tag = ("new",)

            # Each of these signals already points at a SPECIFIC column,
            # not just "something on this row is off" - turning them into
            # individually-coloured cells (applied after set_sheet_data()
            # below) means the pharmacist's eye goes straight to the one
            # or two values worth checking instead of re-reading all 10
            # columns of a flagged row. Column indices match
            # self._review_cols (see comment near cell_highlights above).
            row_cell_flags = []
            if row.get("gst_uncertain"):
                row_cell_flags.append((9, "#FFE0B2", "black"))          # GST% - assumed, not read
            if "RATE_GT_MRP" in errors or "INVALID_PRICE" in errors:
                row_cell_flags += [(4, "#FFCDD2", "#B71C1C"), (5, "#FFCDD2", "#B71C1C")]   # Purchase, MRP
            if "AMOUNT_MISMATCH" in errors or "INVALID_AMOUNT" in errors:
                row_cell_flags += [(4, "#FFCDD2", "#B71C1C"), (6, "#FFCDD2", "#B71C1C")]   # Purchase, Qty

            current_data.append([
                name,
                row.get("hsn", "") or "",
                row.get("batch", "") or "",
                row.get("expiry", "") or "",
                row.get("purchase", 0) or 0,
                row.get("mrp", 0) or 0,
                row.get("qty", 1) or 1,
                row.get("company", "") or "",
                # BUG FIX (Aug 2026, caught by testing Purchase Entry's
                # own CSV re-import): this used to default a blank Pack
                # Size to "1" for display - looked friendly, but by the
                # time commit_rows() reads this same grid cell, "1" is
                # indistinguishable from a genuine single-unit pack and
                # was UNCONDITIONALLY overwriting medicine_master.pack_size
                # (e.g. a real "15'S" strip silently corrupted down to
                # "1", breaking that medicine's stock-multiplier math on
                # every future purchase). Left blank now - commit_rows()
                # only updates the stored pack_size when this is genuinely
                # non-blank (same COALESCE(NULLIF(...)) protection HSN
                # already had). The pharmacist can still fill it in here
                # via "Edit Selected Row" if they know it.
                row.get("pack_size", "") or "",
                row.get("gst", 0) or 0,
                status_label
            ])
            # Kept index-aligned with current_data (and therefore with
            # self.reviewTable's rows once set_sheet_data() below runs) -
            # this is the ORIGINAL text before any fuzzy-match renamed it
            # to an existing medicine, so "Force New Item" can restore it.
            self._row_raw_names.append(raw_name)
            if tag:  # tag == ("new",) marks a row that needs a second look
                flagged_rows.append(len(current_data) - 1)
            if row_cell_flags:
                new_row_idx = len(current_data) - 1
                for col_idx, bg, fg in row_cell_flags:
                    cell_highlights.append((new_row_idx, col_idx, bg, fg))

        # reset_col_positions=False keeps our custom column widths.
        # reset_row_positions must stay True - tksheet draws
        # len(row_positions)-1 rows, not len(data); False here would
        # leave every OCR/paste row invisible even though it's in the data.
        self.reviewTable.set_sheet_data(current_data, reset_col_positions=False, reset_row_positions=True, reset_highlights=False)
        if flagged_rows:
            # "new"/warning rows must stay yellow regardless of the
            # plain zebra striping every other row gets automatically
            # (tksheet's alternate_color) - a warning row should never
            # blend in.
            self.reviewTable.highlight_rows(rows=flagged_rows, bg="#FFF3CD", fg="black")
        if cell_highlights:
            # Applied AFTER highlight_rows() above so these more specific
            # cell colours draw on top of the plain yellow row background
            # instead of being hidden underneath it.
            for row_idx, col_idx, bg, fg in cell_highlights:
                self.reviewTable.highlight_cells(row=row_idx, column=col_idx, bg=bg, fg=fg)

        self.summary_label.config(text=f"{len(current_data)} row(s) in review grid")
        self.update_bulk_summary()
    def update_bulk_summary(self):
        rows = self.reviewTable.get_sheet_data()
        total_items = len(rows)
        total_qty = 0
        subtotal = 0.0

        for v in rows:
            # Columns: ("Medicine", "HSN", "Batch", "Expiry", "Purchase", "MRP", "Qty", "Company", "Pack Size", "GST%", "Status")
            # Index 6 -> Qty, Index 4 -> Purchase Rate
            try:
                qty = int(float(v[6]))
            except (ValueError, TypeError):
                qty = 0

            try:
                purchase = float(v[4])
            except (ValueError, TypeError):
                purchase = 0.0

            total_qty += qty
            subtotal += round(purchase * qty, 2)

        tax_amount = round(subtotal * 0.05, 2)  # 5% GST கணக்கீடு
        net_amount = round(subtotal + tax_amount, 2)

        # Total Items மற்றும் Total Qty சேர்த்து முழுமையான வரி வடிவம்
        summary_text = (
            f"Total Items: {total_items}  |  Total Qty: {total_qty}  |  "
            f"Sub Total: ₹ {subtotal:,.2f}  |  GST (5%): ₹ {tax_amount:,.2f}  |  "
            f"Net Amount: ₹ {net_amount:,.2f}"
        )
        self.summary_label.config(text=summary_text, fg="blue", font=("Segoe UI", 10, "bold"))

    def edit_selected_row(self):
        current = self.reviewTable.get_currently_selected()
        if not current or current.row is None:
            ui_popups.show_error(self.win, "Error", "Select a row first")
            return
        row_idx = current.row

        values = list(self.reviewTable.get_row_data(row_idx))
        edit_win = tk.Toplevel(self.win)
        edit_win.title("Edit Bulk Import Row")
        edit_win.resizable(False, False)
        edit_win.grab_set()

        def _close_edit(event=None):
            # Same fix as BulkImportWindow._close_window() (see its
            # docstring) applied to this nested dialog - grab_set() above
            # makes it modal, and destroy() alone doesn't reliably hand
            # click-focus back to the parent Bulk Import window on
            # Windows, leaving "Add All to Bill" and everything else in
            # it unresponsive until the user alt-tabs. Bound to both the
            # Save button and the window's own close (X) button so
            # there's no path that skips releasing the grab.
            try:
                edit_win.grab_release()
            except Exception:
                pass
            edit_win.destroy()
            try:
                self.win.lift()
                self.win.focus_force()
            except Exception:
                pass

        edit_win.protocol("WM_DELETE_WINDOW", _close_edit)
        # Esc key also closes this popup (same as Close/the window's X).
        edit_win.bind("<Escape>", _close_edit)
        edit_win.focus_force()

        # Aug 2026 visual refresh: same colored-header / white-body /
        # flat-button look as every other hand-built popup app-wide
        # (see ui_style.popup_header()'s docstring) - already modal
        # (grab_set() above), so only the look changes here.
        body = ui_style.popup_header(edit_win, "Edit Bulk Import Row", icon="✏")

        fields = ["Medicine", "HSN", "Batch", "Expiry", "Purchase", "MRP", "Qty", "Company", "Pack Size", "GST%"]
        PURCHASE_IDX, QTY_IDX = 4, 6
        vars_ = []

        for i, label in enumerate(fields):
            tk.Label(
                body, text=label, bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL, font=("Segoe UI", 10, "bold"),
            ).grid(row=i, column=0, padx=10, pady=5, sticky="w")
            v = tk.StringVar(value=values[i])
            tk.Entry(
                body, textvariable=v, width=25, font=("Segoe UI", 10), bg=theme.SURFACE_FIELD,
                relief="flat", highlightthickness=1, highlightbackground=theme.BORDER_DEFAULT,
                highlightcolor=theme.BORDER_FOCUS,
            ).grid(row=i, column=1, padx=10, pady=5, ipady=2)
            vars_.append(v)

        # Purchase and Qty drive a live Amount preview - changing either
        # one recalculates it immediately, so a typo (wrong rate, wrong
        # qty) is obvious here before saving, instead of only surfacing
        # as an AMOUNT_MISMATCH warning after the row is already back in
        # the review grid.
        amount_var = tk.StringVar(value="0.00")

        def recalc_amount(*_args):
            try:
                purchase = float(vars_[PURCHASE_IDX].get() or 0)
            except ValueError:
                purchase = 0.0
            try:
                qty = float(vars_[QTY_IDX].get() or 0)
            except ValueError:
                qty = 0.0
            amount_var.set(f"{purchase * qty:,.2f}")

        vars_[PURCHASE_IDX].trace_add("write", recalc_amount)
        vars_[QTY_IDX].trace_add("write", recalc_amount)
        recalc_amount()

        tk.Label(
            body, text="Amount", bg=theme.SURFACE_WHITE, fg=theme.TEXT_LABEL, font=("Segoe UI", 10, "bold"),
        ).grid(row=len(fields), column=0, padx=10, pady=5, sticky="w")
        tk.Label(
            body, textvariable=amount_var, bg=theme.SURFACE_WHITE, font=("Segoe UI", 10, "bold"), fg=theme.PRIMARY,
        ).grid(row=len(fields), column=1, padx=10, pady=5, sticky="w")

        def save_edit():
            new_values = [v.get() for v in vars_]
            name = new_values[0].strip()
            is_new = name.lower() not in self._known_names_lower
            new_values.append("⚠ New" if is_new else "✓ Known")

            self.reviewTable.set_row_data(row_idx, values=new_values)
            if is_new:
                self.reviewTable.highlight_rows(rows=[row_idx], bg="#FFF3CD", fg="black")
            else:
                # dehighlight_rows clears back to the plain zebra stripe -
                # a row that was flagged and got fixed shouldn't stay
                # stuck yellow forever.
                self.reviewTable.dehighlight_rows(rows=[row_idx])
            _close_edit()
            self.update_bulk_summary()

        btn_row = tk.Frame(body, bg=theme.SURFACE_WHITE)
        btn_row.grid(row=len(fields) + 1, column=0, columnspan=2, pady=15)

        ui_style.flat_button(
            btn_row, "Save Changes", theme.STATUS_SUCCESS, save_edit, width=15,
        ).pack(side="left", padx=5)

        ui_style.flat_button(
            btn_row, "Cancel", theme.ACCENT_NEUTRAL, _close_edit, width=12,
        ).pack(side="left", padx=5)

        # No explicit width/height (was a fixed 400x490 guess) - see
        # ui_style.center_window()'s own docstring for why sizing to
        # real packed content is safer.
        ui_style.center_window(edit_win, parent=self.win)

    def shift_row_columns(self, direction):
        """
        OCR column misalignment-ஐ ஒரே click-ல சரி பண்ண.
        direction: -1 = Shift Left, +1 = Shift Right
        Medicine (index 0) மற்றும் Status (கடைசி) column-ஐ தொடாது -
        HSN, Batch, Expiry, Purchase, MRP, Qty, Company, Pack Size, GST%
        (index 1 to 9) மட்டும் shift ஆகும்.
        """
        current = self.reviewTable.get_currently_selected()
        if not current or current.row is None:
            ui_popups.show_error(self.win, "Error", "Select a row first")
            return
        row_idx = current.row

        values = list(self.reviewTable.get_row_data(row_idx))
        # values structure: [Medicine, HSN, Batch, Expiry, Purchase, MRP, Qty, Company, Pack Size, GST%, Status]
        SHIFT_START, SHIFT_END = 1, 9  # HSN முதல் GST% வரைக்கும் (inclusive)

        middle = values[SHIFT_START:SHIFT_END + 1]

        if direction == -1:
            # Shift Left: முதல் value கைவிடப்படும், மீதி ஒரு படி இடதுபுறம் நகரும்,
            # கடைசி காலியா ஆகும்
            shifted = middle[1:] + [""]
        else:
            # Shift Right: கடைசி value கைவிடப்படும், மீதி ஒரு படி வலதுபுறம் நகரும்,
            # முதல் காலியா ஆகும்
            shifted = [""] + middle[:-1]

        new_values = [values[0]] + shifted + [values[-1]]
        self.reviewTable.set_row_data(row_idx, values=new_values)
        self.update_bulk_summary()

    def remove_selected_row(self):
        current = self.reviewTable.get_currently_selected()
        if not current or current.row is None:
            ui_popups.show_error(self.win, "Error", "Select a row first")
            return
        self.reviewTable.del_rows(rows=current.row)
        if current.row < len(self._row_raw_names):
            del self._row_raw_names[current.row]
        self.summary_label.config(text=f"{len(self.reviewTable.get_sheet_data())} row(s) in review grid")
        self.update_bulk_summary()

    def clear_review_rows(self):
        self.reviewTable.set_sheet_data([], reset_col_positions=False, reset_row_positions=True)
        self._row_raw_names = []
        self.summary_label.config(text="0 row(s) in review grid")
        self.update_bulk_summary()

    def force_new_item(self):
        """"Force New Item" - the review-grid escape hatch for a fuzzy
        match that linked this row to the WRONG existing medicine.

        Real incident this fixes (Aug 2026): a purchase invoice line
        "OMEE SYP" (a Syrup) fuzzy-matched to the already-existing
        "OMEE CAP 20'S" (a Capsule) at a "~ Possible Match" score,
        purely because both names share the "OMEE" brand prefix. The
        pharmacist committed it anyway, and commit_rows()'s UPDATE
        overwrote OMEE CAP 20'S's master pack_size with the Syrup's
        "170ML" - corrupting that Capsule's per-unit pricing on every
        screen (Billing/Stock/Purchase/Clinic Ledger) that reads
        pricing_utils.get_pack_multiplier(). medicine_matcher.py's new
        dosage-form guardrail stops this specific case from matching in
        the first place, but a fuzzy matcher can never be made
        perfectly safe against every possible name collision - this
        button is the manual override for whatever guardrail doesn't
        catch: it restores the row's ORIGINAL invoice text (recorded
        before any match renamed it) as the Medicine name and flags it
        "New (Forced)", so commit_rows() creates it as a brand new
        Medicine Master entry via create_new_medicines() instead of
        UPDATE-ing whatever it was matched to.
        """
        current = self.reviewTable.get_currently_selected()
        if not current or current.row is None:
            ui_popups.show_error(self.win, "Error", "Select a row first")
            return
        row_idx = current.row
        if row_idx >= len(self._row_raw_names):
            ui_popups.show_error(self.win, 
                "Error",
                "No original invoice text was recorded for this row - "
                'use "Edit Selected Row" to retype the correct name instead.'
            )
            return

        original_name = self._row_raw_names[row_idx]
        if not ui_popups.show_confirmation(self.win, 
            "Force New Item",
            f'Ignore the matched medicine and create "{original_name}" as a '
            f"brand new Medicine Master item instead?\n\n"
            f"Use this when this row was wrongly linked to a DIFFERENT "
            f"existing medicine (e.g. a Syrup matched to a Capsule of the "
            f"same brand)."
        ):
            return

        values = list(self.reviewTable.get_row_data(row_idx))
        values[0] = original_name
        values[-1] = "⚠ New (Forced)"
        self.reviewTable.set_row_data(row_idx, values=values)
        self.reviewTable.highlight_rows(rows=[row_idx], bg="#FFF3CD", fg="black")
        self.update_bulk_summary()

    def commit_rows(self):
        rows = self.reviewTable.get_sheet_data()
        if not rows:
            ui_popups.show_error(self.win, "Error", "Review grid is empty")
            return

        parsed = []
        for v in rows:
            # Columns: ("Medicine", "HSN", "Batch", "Expiry", "Purchase", "MRP", "Qty", "Company", "Pack Size", "GST%", "Status")
            parsed.append({
                "name": str(v[0]).strip(),
                "hsn": str(v[1]) if len(v) > 1 else "",
                "batch": str(v[2]) if len(v) > 2 else "",
                "expiry": str(v[3]) if len(v) > 3 else "",
                "purchase": v[4] if len(v) > 4 else 0,
                "mrp": v[5] if len(v) > 5 else 0,
                "qty": v[6] if len(v) > 6 else 1,
                "company": str(v[7]) if len(v) > 7 else "",
                # Blank (not "1") when the grid cell is genuinely empty -
                # see parse_and_add()'s matching comment. commit_rows()'s
                # UPDATE below now protects the STORED pack_size with
                # COALESCE(NULLIF(...)), same as hsn already had; the "1"
                # fallback used further down (pack_raw) is only for THIS
                # transaction's own stock-multiplier math, never written
                # back to medicine_master.
                "pack_size": str(v[8]).strip() if len(v) > 8 else "",
                "gst": v[9] if len(v) > 9 else 0
            })

        new_names = sorted({p["name"] for p in parsed if p["name"].lower() not in self._known_names_lower})
        if new_names:
            if not ui_popups.show_confirmation(self.win, "New Medicines Found", "Add new medicines and continue?"):
                return
            self.create_new_medicines(parsed, new_names)

        added = 0
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()

        # purchase.py's purchaseTable is a tksheet Sheet now (see
        # add_item() there) - fetch its data once, append every commit
        # row to it in Python, then write it back with ONE
        # set_sheet_data() call after the loop (instead of a
        # get/set round trip per row, which would also re-trigger a
        # redraw for every single item on a big invoice).
        purchase_data = self.purchase_ref.purchaseTable.get_sheet_data()

        for p in parsed:
            try:
                qty_val = float(p.get("qty", 0))
            except (ValueError, TypeError):
                qty_val = 0.0

            try: purchase_val = float(p["purchase"])
            except: purchase_val = 0.0

            try: mrp_val = float(p["mrp"])
            except: mrp_val = 0.0

            try: gst_val = float(p["gst"])
            except: gst_val = 0.0

            pack_raw = str(p.get("pack_size") or "1")
            pack_multiplier = get_pack_multiplier(pack_raw)

            # Purchase Table-ல் லைன் டோட்டல் (Purchase * Qty) சரியாக வருவதற்கு
            line_total = round(purchase_val * qty_val, 2)

            # FLIPPED (Aug 2026, real incident): this used to be
            # COALESCE(NULLIF(?, ''), col) - fill from the invoice
            # whenever it had a non-blank value, only falling back to
            # the existing master value when the invoice read was
            # blank. That still let a WRONGLY MATCHED row (a fuzzy
            # match linking one medicine's invoice line to a different,
            # already-correct medicine's master record) overwrite good
            # data with the wrong item's details - exactly what
            # corrupted "OMEE CAP 20'S"'s pack_size to "170ML" (that
            # value came from an "OMEE SYP" invoice line that had
            # fuzzy-matched to the Capsule). A purchase invoice string
            # is supplier-typed and typo-prone, and can now be the WRONG
            # item's data entirely thanks to a bad match - it must never
            # overwrite an already-populated master value, only fill in
            # a field that's still genuinely blank (e.g. a brand new
            # medicine that had no Company/Pack Size/HSN recorded yet).
            # `name` (the Medicine identity itself) is never written by
            # this UPDATE at all - it's only ever used in the WHERE
            # clause below - so Item Name can never be overwritten by a
            # purchase import either. gst stays unconditional, per the
            # existing reasoning: 0 is a valid, meaningful GST rate
            # (exempt items), not a "missing data" signal like blank
            # text is for the other columns - if a wrong match still
            # sets a wrong GST%, medicine_matcher.py's dosage-form
            # guardrail plus "Force New Item" above are the real
            # defenses against a wrong match happening at all.
            cur.execute("""
                UPDATE medicine_master
                SET company = COALESCE(NULLIF(company, ''), ?),
                    pack_size = COALESCE(NULLIF(pack_size, ''), ?),
                    gst = ?,
                    hsn = COALESCE(NULLIF(hsn, ''), ?),
                    needs_review = 0
                WHERE name = ?
            """, (p["company"], p["pack_size"], gst_val, p.get("hsn", ""), p["name"]))

            # BUG FIX (Aug 2026 invoice-export work): purchase.py's item
            # grid gained HSN + GST% columns, then Pack Size (see
            # purchase.py's add_item() for why - the old grid threw GST
            # away after computing Total, had no HSN column at all, and
            # Purchase Entry never captured Pack Size at all). This
            # method already parses hsn/gst_val/pack_raw from the review
            # grid above (used to update medicine_master) but was
            # discarding them here instead of passing them through -
            # meaning every Bulk Import-created purchase row landed in
            # Purchase Entry with these blank, and worse, once the
            # grid's column order changed this row would have misaligned
            # into the WRONG columns entirely had this not been fixed in
            # lockstep. Order must exactly match add_item()'s: Medicine,
            # Batch, Expiry, HSN, GST%, Purchase, MRP, Pack Size, Qty,
            # Total. Uses p["pack_size"] AS-IS (blank if this row's
            # source never provided one) - NOT the "or '1'" defaulted
            # pack_raw used further below purely for THIS transaction's
            # stock-multiplier math - so a genuinely blank pack size
            # still shows blank in the grid and stays COALESCE-protected
            # on save (see purchase.py's save_purchase()) instead of a
            # fabricated "1" silently overwriting Medicine Master later.
            purchase_data.append([
                p["name"],
                p["batch"],
                p["expiry"],
                p.get("hsn", ""),
                gst_val,
                purchase_val,
                mrp_val,
                p.get("pack_size", "") or "",
                qty_val,
                line_total,
                # Batch-wise Expired/Expiring Soon/OK Status column (Aug
                # 2026) - purchase.py's add_item() computes this via
                # _purchase_item_status() for rows typed in by hand;
                # bulk-imported rows need the exact same 11th value or
                # they'd be one column short and desync from every
                # manually-added row below them.
                self.purchase_ref._purchase_item_status(p["expiry"])
            ])
            added += 1

        # reset_col_positions=False keeps the column widths add_item()
        # relies on. reset_row_positions must stay True or the newly
        # committed rows go into self.data but never get drawn (tksheet
        # draws len(row_positions)-1 rows, not len(data)).
        self.purchase_ref.purchaseTable.set_sheet_data(
            purchase_data, reset_col_positions=False, reset_row_positions=True, reset_highlights=True
        )
        self.purchase_ref._highlight_purchase_status_rows(purchase_data)

        con.commit()
        con.close()

        self.purchase_ref.calculate_grand_total()
        self.purchase_ref.load_medicines()
        
        ui_popups.show_info(self.win, "Bulk Import", f"Added {added} item(s) to purchase bill.")
        self._close_window()


    def create_new_medicines(self, parsed, new_names):
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        try:
            cur.execute("ALTER TABLE medicine_master ADD COLUMN needs_review INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass 

        # --- NEW CODE: ஒரே பெயரில் வெவ்வேறு பேட்ச் வந்தாலும் சரியாக ஏற்றுவதற்கான லாஜிக் ---
        added_combos = set()
        
        for p in parsed:
            name = p["name"]
            batch = p.get("batch", "")
            combo = (name, batch)
            
            if name in new_names and combo not in added_combos:
                added_combos.add(combo)
                
                try: purchase_val = float(p["purchase"]) if p["purchase"] not in ("", None) else 0
                except ValueError: purchase_val = 0

                try: mrp_val = float(p["mrp"]) if p["mrp"] not in ("", None) else 0
                except ValueError: mrp_val = 0

                try: gst_val = float(p["gst"]) if p["gst"] not in ("", None) else 0
                except ValueError: gst_val = 0

                company_val = (p.get("company") or "").strip()
                pack_size_val = str(p.get("pack_size") or "1").strip()
                hsn_val = (p.get("hsn") or "").strip()

                cur.execute("""
                    INSERT INTO medicine_master(
                        name, company, hsn, batch, expiry, purchase, mrp, sale, gst,
                        stock, pack_size, free_qty, needs_review
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    name, company_val, hsn_val, batch, p["expiry"], purchase_val,
                    mrp_val, mrp_val, gst_val, 0, pack_size_val, 0, 1
                ))
        # ------------------------------------------------------------------

        con.commit()
        con.close()
        self._refresh_known_names()


def parse_tsv_text(raw_text):
    """
    Parses tab-separated rows copied from Excel or Google Sheets matching the exact invoice columns:
    Product Name | Pack | Mfr | HSN | Batch | Exp | MRP | Qty | Free | Rate | Disc | GST% | Amount
    """
    rows = []
    lines = [l for l in raw_text.splitlines() if l.strip()]

    if not lines:
        return rows

    if "product name" in lines[0].lower() or "sno" in lines[0].lower():
        lines = lines[1:]

    for line in lines:
        parts = [p.strip() for p in line.split("\t")]

        if not parts:
            continue
        
        if parts[0].isdigit() and len(parts) > 1:
            parts = parts[1:]

        if not parts or not parts[0]:
            continue
        
        name = parts[0]
        pack_size = parts[1] if len(parts) > 1 else "1"
        company = parts[2] if len(parts) > 2 else ""
        hsn = parts[3] if len(parts) > 3 else ""
        batch = parts[4] if len(parts) > 4 else (parts[3] if len(parts) > 3 else "")
        expiry = parts[5] if len(parts) > 5 else ""
        mrp = _to_float(parts[6]) if len(parts) > 6 else 0
        qty = _to_int(parts[7], default=1) if len(parts) > 7 else 1
        raw_rate_str = parts[9] if len(parts) > 9 else (parts[8] if len(parts) > 8 else "0")
        if "free" in raw_rate_str.lower():
            purchase = 0.0
        else:
            purchase = _to_float(raw_rate_str)
        amount = _to_float(parts[12]) if len(parts) > 12 else (_to_float(parts[-1]) if parts else 0)

        # இன்வாய்ஸில் GST% பொதுவாக 11 அல்லது 12-வது பத்தியாக வரும் (உதாரணமாக '5%')
        gst = 0
        if len(parts) > 11:
            gst = _to_float(parts[11])
        elif len(parts) > 10:
            gst = _to_float(parts[10])
        else:
            # ஒருவேளை வேறு பத்தியில் இருந்தால் தேடி எடுப்பது - ஆனா '%'
            # symbol உள்ள token மட்டும் பார்க்கணும், இல்லனா Pack Size
            # அல்லது Qty (எ.கா "10", "20") தப்பா GST-ஆ புரிஞ்சுக்கும்
            # (இரண்டும் <=28 range-ல் இருக்கும்).
            for p in parts:
                if "%" not in p:
                    continue
                clean_p = p.replace("%", "").strip()
                if clean_p.replace(".", "", 1).isdigit():
                    gst = _to_float(clean_p)
                    break

        errors = ocr_table_reconstruction.validate_row({
            "mrp": mrp, "purchase": purchase, "qty": qty, "amount": amount,
        })["errors"]

        rows.append({
            "name": name,
            "hsn": hsn,
            "batch": batch,
            "expiry": expiry,
            "purchase": purchase,
            "mrp": mrp,
            "qty": qty,
            "company": company,
            "pack_size": pack_size,
            "gst": gst,
            "errors": errors,
        })

    return rows

def parse_free_text(raw_text):
    rows = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line: continue
        line = re.sub(r"^\s*\d+[\.\)]\s*", "", line)
        line = re.sub(r"^\s*[-*•○]\s*", "", line)
        if not line: continue
        parsed = _parse_free_line(line)
        if parsed: rows.append(parsed)
    return rows


def _parse_free_line(line):
    m = re.match(r"^(.*?)\s*[xX]\s*(\d+)\s*$", line)
    if m and m.group(1).strip():
        return {"name": m.group(1).strip(" -\u2013:"), "qty": int(m.group(2)), "gst": 0}
    return {"name": line, "qty": 1, "gst": 0}


def _extract_embedded_number(text):
    """
    Fallback for _to_float()/_to_int() when the cell isn't a CLEAN
    number - pulls out the first embedded numeric substring instead of
    giving up entirely.

    Real recurring bug (seen across multiple invoice photos): a stray
    OCR artifact (Tesseract mis-boxing a checkmark/tick glyph in the
    Free Qty column as a short garbage word, e.g. "Va" or "Lo") sometimes
    lands in the SAME reconstructed cell as a genuine number because its
    x-position happens to fall inside that column's bucket - producing
    a cell like "30 Lo" instead of a clean "30". Requiring an EXACT
    numeric match on the whole string silently discarded a perfectly
    good, correctly-read quantity/price back to a wrong default (Qty
    silently becoming 1 instead of the real 30) instead of recovering
    it. Chasing every possible artifact shape/size upstream in row
    reconstruction is a losing battle - different photos produce
    differently-sized artifacts - so this fixes it once, downstream,
    for every numeric field at once.
    """
    m = re.search(r"-?\d+(?:\.\d+)?", str(text or ""))
    return m.group() if m else None


def _to_float(text, default=0):
    try:
        return float(text.replace("₹", "").replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        pass
    embedded = _extract_embedded_number(text)
    return float(embedded) if embedded is not None else default


def _to_int(text, default=1):
    try:
        # டெசிமல் புள்ளியுடன் ('12.0') வந்தாலும் எரர் அடிக்காமல் இருக்க float ஆக்கி பின் int ஆக்குவது
        return int(float(str(text).replace(",", "").strip()))
    except (TypeError, ValueError):
        pass
    embedded = _extract_embedded_number(text)
    return int(float(embedded)) if embedded is not None else default