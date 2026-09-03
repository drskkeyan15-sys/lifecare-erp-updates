"""
factory_reset.py
LifeCare Pharmacy ERP - "Factory Reset / Clear Testing Data" (Aug 2026)

Built for the testing phase of the compiled .exe: lets the pharmacist
wipe every bit of dummy data entered while trying the app out, without
hand-deleting pharmacy.db or reinstalling. Two-part safety net before
anything is touched:

  1. A full timestamped copy of pharmacy.db is made first (see
     backup_reset_copy() below) - if the wrong thing gets cleared, or
     this runs against a real, non-test database by mistake, the
     pre-reset state is one file-copy away, not gone.
  2. reset_database() requires the caller to have ALREADY verified an
     Admin password via verify_admin_password() below - this module
     does no UI of its own, so every caller (dashboard.py's Factory
     Reset dialog, any future automation) must explicitly re-check
     credentials right before calling it.

Scope - what gets cleared vs kept:
  CLEARED - every real data/business table found in the database AT RUN
  TIME (Medicine Master, Purchase, Sales, Sales Items, Clinic Visits,
  Clinic Patients, Customers, Suppliers, Audit Log, ...), discovered
  dynamically from sqlite_master rather than hand-listed, so a table a
  future feature adds gets cleared automatically instead of silently
  being missed by a list nobody remembered to update (exactly the class
  of bug bulk_import.py's Aug 2026 pack_size incident already showed
  this codebase is not immune to).
  KEPT - `users` (so nobody gets locked out of their own freshly-reset
  app) and `settings` (shop name/address/GSTIN - business configuration,
  not "testing data" the way a dummy patient or test purchase is). FTS5
  shadow tables backing medicine_master_fts are never touched directly -
  they stay in sync automatically via medicine_master's own AFTER
  DELETE trigger (see database.py) the moment medicine_master's rows are
  cleared.
  A fresh audit_log entry recording the reset itself (who ran it, which
  Admin account authorized it, how many tables were cleared) is written
  immediately AFTER the wipe - so a completely empty audit trail isn't
  mistaken for "logging is broken"; it becomes that trail's first entry.

PRAGMA foreign_keys is turned OFF for the duration of the wipe. Real FK
constraints exist in this schema (e.g. clinic_visit_items.medicine_id
REFERENCES medicine_master(id), clinic_visits.patient_id REFERENCES
clinic_patients(id) - see database.py) - deleting tables in whatever
order sqlite_master happens to return them in would otherwise fail
partway through a reset with a FOREIGN KEY constraint error. It's
restored to ON before this module's own connection closes, matching
app_paths.py's app-wide "foreign_keys always ON" contract for every
other connection in the app (that monkeypatch re-applies ON to every
NEW connection regardless, so this is a courtesy for anyone reusing
this exact connection object, not a requirement for correctness).
"""

import os
import shutil
import sqlite3
from datetime import datetime

from app_paths import DB_NAME
import auth_utils
import audit_log

# Never cleared by reset_database() - see the module docstring's
# "Scope" section for why each one is kept.
PRESERVED_TABLES = {"users", "settings"}


def verify_admin_password(password, db_name=None):
    """
    Checks `password` against every user row with role='Admin' - not
    just whichever account happens to be logged into the app right now,
    since the requirement is "an Admin password", not necessarily the
    current session's own. Returns the matching admin's username (a
    truthy string) the moment one verifies; returns None (falsy) if none
    do, including the edge case of no Admin users existing at all - this
    must fail closed, never open, if that ever happens.
    """
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT username, password FROM users WHERE role='Admin'")
    rows = cur.fetchall()
    con.close()
    for username, stored_hash in rows:
        if auth_utils.verify_password(password, stored_hash):
            return username
    return None


def backup_reset_copy(db_name=None):
    """
    Copies pharmacy.db to a timestamped sibling file
    (pharmacy_backup_before_reset_YYYYMMDD_HHMMSS.db) in the SAME
    folder, before anything is cleared. Returns the backup's full path.

    Raises on failure (disk full, permissions, file locked) rather than
    swallowing the error - the caller must treat a failed backup as a
    reason to ABORT the reset, never proceed without this safety net.
    """
    db_name = db_name or DB_NAME
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(
        os.path.dirname(db_name) or ".",
        f"pharmacy_backup_before_reset_{stamp}.db"
    )
    shutil.copy2(db_name, backup_path)
    return backup_path


def _clearable_tables(cur):
    """Every real table in the database except PRESERVED_TABLES and any
    FTS5 shadow/virtual table (those stay in sync automatically via
    medicine_master's own triggers - see the module docstring)."""
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    names = [r[0] for r in cur.fetchall()]
    return sorted(
        n for n in names
        if n not in PRESERVED_TABLES and "fts" not in n.lower()
    )


def reset_database(authorized_by, db_name=None):
    """
    Clears every table in scope (see module docstring) and resets their
    AUTOINCREMENT counters, so the next row inserted anywhere starts
    fresh at id=1 again - genuinely "fresh install", not just "empty
    tables with a huge next id left over". VACUUMs afterward to actually
    shrink pharmacy.db on disk (SQLite keeps deleted rows' space as free
    pages inside the file otherwise, so the .db wouldn't visibly shrink
    without this). Returns the sorted list of table names that were
    cleared.

    `authorized_by` is the Admin username whose password gated this call
    (from verify_admin_password() above) - recorded in the audit_log
    entry this writes afterward so it's clear WHICH Admin account
    authorized the reset, even if a different (or no) user is the one
    currently logged into the app.

    Caller's responsibility, NOT this function's: verifying the Admin
    password and taking a backup (backup_reset_copy above) BEFORE
    calling this - by the time this function runs, the wipe is
    unconditional and irreversible short of restoring that backup.
    """
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    # Autocommit mode: simplest for a straight-line wipe with no
    # partial-state rollback need (the pre-wipe backup, not a DB
    # transaction, is the real undo path here), and required for VACUUM
    # below - VACUUM cannot run inside an open transaction.
    con.isolation_level = None
    cur = con.cursor()
    cur.execute("PRAGMA foreign_keys = OFF")

    cleared = _clearable_tables(cur)
    for table in cleared:
        cur.execute(f'DELETE FROM "{table}"')
        # No-op if `table` was never an AUTOINCREMENT table (it would
        # simply have no row in sqlite_sequence to begin with).
        cur.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))

    cur.execute("VACUUM")
    cur.execute("PRAGMA foreign_keys = ON")
    con.close()

    audit_log.log_action(
        "Admin", "Factory Reset",
        f"Authorized by Admin '{authorized_by}'. Cleared {len(cleared)} "
        f"table(s): {', '.join(cleared)}. Users and Settings were preserved.",
        db_name=db_name,
    )
    return cleared
