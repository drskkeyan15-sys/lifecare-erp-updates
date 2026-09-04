"""
Shared path helper for Life Care Pharmacy ERP.

Every module should import DB_NAME from here instead of hardcoding
"pharmacy.db" - this guarantees the database is always found next to
the actual .exe (or next to main.py in development), never in a
PyInstaller temp extraction folder or whatever the current working
directory happens to be when the app is launched.
"""

import sys
import os
import sqlite3

# ─── Foreign key enforcement + WAL's fast-write pragma, applied to
# every connection app-wide ──────────────────────────────────────────
# SQLite ships with foreign key checks OFF by default, and unlike
# journal_mode this is NOT a persistent per-file setting - it has to be
# re-issued on every single connection, every time. The app opens
# sqlite3.connect() directly in ~90 places across purchase.py, billing.py,
# customer.py, supplier.py, reports.py, etc., so touching every call site
# individually would be a large, error-prone diff for very little benefit.
#
# Instead: every one of those modules already does
# `from app_paths import DB_NAME` before it ever calls sqlite3.connect(),
# so patching sqlite3.connect() once, right here, guarantees
# PRAGMA foreign_keys=ON is active on every connection the moment this
# module is first imported - without editing any of those ~90 call sites.
# Without this, medicine_master.py could delete/renumber a medicine row
# while purchase_items/sales_items rows still pointed at that old id, and
# SQLite would silently allow it.
#
# PRAGMA synchronous=NORMAL (Sep 2026 addition) rides along on the exact
# same patch for the exact same reason: database.py's create_database()
# already issues this once at startup, alongside PRAGMA journal_mode=WAL
# (see that file's own comment on why WAL was enabled), but synchronous
# is a per-connection setting like foreign_keys, NOT a persistent
# per-file one like journal_mode - so that single startup connection was
# the only one in the whole app actually running in NORMAL mode. Every
# other connection (purchase.py, billing.py, every repository module -
# whichever of the ~216 sqlite3.connect() call sites ran) was silently
# falling back to SQLite's own default of FULL on every write, all app
# long. Not a safety bug - FULL is the safer/slower of the two, so
# nothing was ever put at risk - just the write-speed benefit WAL+NORMAL
# was meant to buy never actually applied outside that one connection.
# Patching it in here closes that gap the same way it was already closed
# for foreign_keys, with the same one-time, zero-call-site-edits reach.
#
# Note: a handful of standalone one-off maintenance scripts (check_db.py,
# fix_db.py, migrate_generics.py, setup_tables.py, etc.) call
# sqlite3.connect("pharmacy.db") directly without importing app_paths -
# those are run manually by hand outside the ERP app itself and are
# unaffected by this patch. The live app (main.py -> login.py ->
# dashboard.py -> every screen) always imports app_paths first.
_real_sqlite_connect = sqlite3.connect


def _connect_with_foreign_keys(*args, **kwargs):
    conn = _real_sqlite_connect(*args, **kwargs)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


sqlite3.connect = _connect_with_foreign_keys

if getattr(sys, "frozen", False):
    # Running as a PyInstaller-built .exe
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # Running as a normal .py script during development
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ─── Single source-of-truth app version (Sep 2026) ─────────────────────
# Every place that shows a version number to a human - main.py's window
# title, login.py's small "v1.x" footer label, and installer.iss's
# MyAppVersion - should read from here (or be kept in sync with it by
# hand for installer.iss, which is a separate Inno Setup file Python
# can't import) instead of each hardcoding its own copy. Before this,
# login.py had "v1.0" typed directly into its label - so an EXE could be
# rebuilt with real fixes inside and still show the same old "v1.0" on
# screen, which is exactly how LifeCareERP.exe vs LifeCareERP2.exe vs
# LifeCarePharmaFinal.exe ended up impossible to tell apart just by
# looking at the running app.
#
# Bump this (and installer.iss's MyAppVersion, by hand) every time you
# rebuild the EXE for real pharmacy use - even for a small bug fix - so
# the running app itself always tells you which build you're looking at.
APP_VERSION = "1.2.0"


# ─── Single source-of-truth database (this shop's one PC) ──────────────
# Life Care Pharmacy currently runs on ONE computer (this one) - the
# real, live pharmacy.db has always lived at D:\05-08-2026\pharmacy.db,
# and the separate Web Dashboard (D:\Pharmacy_Advanced\db_reader.py) is
# hardcoded to read that exact file. Without the check below, running
# the built LifeCareERP.exe from build\, dist\, or an installed copy -
# each a different folder, on purpose, for build/test/install stages -
# each created its OWN empty pharmacy.db right next to itself, so the
# ERP kept showing 0 stock while the Web Dashboard (always reading the
# one real file) correctly showed the real medicines - two different
# files, same computer, not actually a bug, just confusing.
#
# _PRIMARY_DB, if it exists on THIS machine, always wins over the normal
# next-to-the-exe location - so no matter where LifeCareERP.exe is
# launched from on this PC, it opens the one real database and always
# matches the Web Dashboard.
#
# On a genuinely DIFFERENT computer (a fresh install elsewhere, where
# D:\05-08-2026 was never copied over) this path simply won't exist, so
# DB_NAME falls straight back to "next to the exe" exactly as before - a
# brand new install there correctly starts with its own fresh, empty
# database instead of trying to reach across to this PC's D: drive.
_PRIMARY_DB = r"D:\05-08-2026\pharmacy.db"
if os.path.exists(_PRIMARY_DB):
    DB_NAME = _PRIMARY_DB
else:
    DB_NAME = os.path.join(BASE_DIR, "pharmacy.db")

# Also useful for billing.py's "Invoices" folder and any other
# app-relative folders you create at runtime.
def app_path(*parts):
    return os.path.join(BASE_DIR, *parts)

# ─── கூடுதல் பாதுகாப்பு: இன்வாய்ஸ் ஃபோல்டர் ரன்-டைமில் எரர் தராமல் இருக்க ───
INVOICES_DIR = app_path("Invoices")
if not os.path.exists(INVOICES_DIR):
    os.makedirs(INVOICES_DIR)