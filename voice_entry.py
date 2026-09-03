"""
voice_entry.py
LifeCare Pharmacy ERP - Voice Entry orchestration (backend only, no UI).

Wires together the pieces described in VOICE_ENTRY_WORKFLOW.md:

    faster-whisper (elsewhere) -> voice_parser.parse_voice_entry()
        -> medicine_matcher.match_invoice_row()
        -> [Preview/Confirm screen shows this to the pharmacist]
        -> save_voice_entry() writes the confirmed row to SQLite

Deliberately has NO tkinter import (same reasoning as
spreadsheet_import.py/bulk_import.py's own module docstrings) so it can
be unit-tested headlessly and so CODING_RULES.md's "UI logic must stay
separate from database logic" / "No SQL inside UI code" holds: the
Voice Entry screen should call preview_voice_entry() to get data to
display, let the pharmacist confirm/edit/pick an alternate match, then
call save_voice_entry() with the final values - it should never build
SQL itself.

QUANTITY SEMANTICS: qty here is the number of SELLABLE UNITS the
pharmacist spoke (e.g. "10 tablets" = 10, "29 bottles" = 29) - it is
added directly to medicine_master.stock, the same way
import_invoice.py's bulk_import_purchases() does it. This differs from
purchase.py's grid entry, where the operator types a pack COUNT that
then gets multiplied by the pack size - voice entry skips that
multiplication because the spoken unit ("tablets"/"bottles") already
IS the sellable unit.
"""

import sqlite3
from datetime import datetime
from typing import Optional

from app_paths import DB_NAME
import audit_log
import medicine_matcher
import voice_parser
from pricing_utils import get_unit_price


def preview_voice_entry(transcript: str, db_name: Optional[str] = None) -> dict:
    """
    Parses a raw voice transcript and looks it up against Medicine
    Master. Returns everything the Preview/Confirm screen needs to
    show - never writes to the database.

    Returns:
        {
            "parsed": {...}            # voice_parser.parse_voice_entry() output
            "match": {...} or None,    # medicine_matcher.match_invoice_row() output
            "is_new_medicine": bool,   # True if no confident match found
        }
    """
    parsed = voice_parser.parse_voice_entry(transcript)

    match = None
    is_new_medicine = True
    if parsed["medicine_name"]:
        match = medicine_matcher.match_invoice_row(
            parsed["medicine_name"], parsed.get("pack_size"), db_name=db_name
        )
        is_new_medicine = match["best_medicine"] is None

    return {
        "parsed": parsed,
        "match": match,
        "is_new_medicine": is_new_medicine,
    }


def save_voice_entry(
    medicine_name: str,
    batch: str,
    expiry: str,
    qty: int,
    mrp: float,
    pack_size: Optional[str] = None,
    purchase_rate: Optional[float] = None,
    company: Optional[str] = None,
    hsn: Optional[str] = None,
    gst: Optional[float] = None,
    generic: Optional[str] = None,
    category: Optional[str] = None,
    db_name: Optional[str] = None,
) -> dict:
    """
    Writes ONE confirmed voice-entry stock line to medicine_master.

    This must only be called with values the pharmacist has already
    confirmed on the Preview/Confirm screen (per VOICE_ENTRY_WORKFLOW.md's
    "never auto-save uncertain voice results" rule) - it performs no
    fuzzy matching or parsing itself.

    Existing (name, batch) -> stock added on top, same
    UPDATE-not-overwrite pattern as import_invoice.py.
    New (name, batch) -> INSERT with needs_review=1, same pattern as
    purchase.py's "not in Medicine Master yet" flow, since HSN/rack/
    category are rarely available from a spoken entry.

    Every call is recorded in audit_log with screen="Voice Entry" so
    voice-sourced stock changes stay traceable, matching
    VOICE_ENTRY_WORKFLOW.md's "keep an audit/source flag" requirement -
    audit_log.py's free-text `details` column is used for this rather
    than a new medicine_master column, per its own module docstring
    (avoids a schema migration for something already expressible as
    free text).

    Returns: {"action": "updated"|"inserted", "medicine_id": int}
    """
    db_name = db_name or DB_NAME
    if not medicine_name or not batch:
        raise ValueError("medicine_name and batch are required to save a voice entry")

    qty = int(qty or 0)
    mrp = float(mrp or 0)
    pack_size = pack_size or "1"
    unit_sale_price = get_unit_price(mrp, pack_size)

    con = sqlite3.connect(db_name)
    cur = con.cursor()
    try:
        cur.execute(
            "SELECT id, stock FROM medicine_master WHERE name=? AND batch=?",
            (medicine_name, batch),
        )
        existing = cur.fetchone()

        if existing:
            medicine_id, current_stock = existing
            new_stock = int(current_stock or 0) + qty
            cur.execute(
                """
                UPDATE medicine_master
                SET stock = ?,
                    expiry = COALESCE(NULLIF(?, ''), expiry),
                    pack_size = COALESCE(NULLIF(?, ''), pack_size),
                    mrp = COALESCE(NULLIF(?, 0), mrp),
                    sale = COALESCE(NULLIF(?, 0), sale),
                    purchase = COALESCE(NULLIF(?, 0), purchase)
                WHERE id = ?
                """,
                (new_stock, expiry, pack_size, mrp, unit_sale_price,
                 purchase_rate or 0, medicine_id),
            )
            action = "updated"
        else:
            cur.execute(
                """
                INSERT INTO medicine_master(
                    name, generic, company, category, hsn, gst,
                    batch, expiry, purchase, mrp, sale, stock,
                    pack_size, needs_review
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    medicine_name, generic, company, category, hsn,
                    gst or 0, batch, expiry, purchase_rate or 0, mrp,
                    unit_sale_price, qty, pack_size,
                    1,  # needs_review - matches purchase.py's new-medicine flow
                ),
            )
            medicine_id = cur.lastrowid
            action = "inserted"

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    audit_log.log_action(
        screen="Voice Entry",
        action="Stock " + ("Update" if action == "updated" else "New Medicine"),
        details=(
            f"source=VOICE_ENTRY medicine='{medicine_name}' batch='{batch}' "
            f"qty={qty} mrp={mrp} expiry={expiry}"
        ),
        db_name=db_name,
    )

    return {"action": action, "medicine_id": medicine_id}
