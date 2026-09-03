# backup_manager.py
import shutil
import os
import sqlite3
from datetime import datetime, timedelta
from app_paths import DB_NAME

# Sep 2026 - encrypted backups (see BACKUP_ZIP_PASSWORD below). pyzipper
# is a small, pure-Python third-party library (stdlib's own zipfile can
# only READ a password-protected zip, never CREATE one) - wrapped in
# try/except the same defensive way dashboard.py's matplotlib import is,
# so a machine where "pip install pyzipper" hasn't been run yet still
# backs up successfully as a plain, unencrypted .db copy (today's backup
# is more important than today's backup being encrypted) instead of
# main.py's unconditional startup backup_now() call crashing the whole
# app over a missing library.
try:
    import pyzipper
    PYZIPPER_AVAILABLE = True
except ImportError:
    PYZIPPER_AVAILABLE = False

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(DB_NAME)), "backups")
KEEP_DAYS = 30  # இந்த நாட்களுக்கு மேற்பட்ட backups auto-delete ஆகும்

# Single source-of-truth password (same pattern as app_paths.py's
# APP_VERSION / idle_lock.py's IDLE_LOCK_MINUTES) protecting every
# encrypted backup .zip this PC creates from here on. This does NOT
# touch the LIVE pharmacy.db at all - the running app reads/writes it
# exactly as before, with zero performance or compatibility risk. It
# only protects backup COPIES: if a backup .zip is ever copied off this
# PC (a stolen/lost USB drive, a synced cloud folder, an old backup
# emailed to someone by mistake), whoever has that file still can't open
# it without this password. Change this any time - it only affects
# backups made AFTER the change; older backups keep needing their own
# password (see restore_backup()'s docstring for what that means during
# a restore).
BACKUP_ZIP_PASSWORD = "Lifecarepharma2026@"


def _checkpoint_wal():
    """
    Forces any pending WAL-mode writes into pharmacy.db itself before a
    raw file copy - see database.py's PRAGMA journal_mode=WAL comment.
    In WAL mode, the most recent transactions can sit in a separate
    pharmacy.db-wal file until a checkpoint happens; copying pharmacy.db
    alone without this could silently produce a backup that's missing
    the last few bills/purchases. Safe to call even if WAL mode isn't
    active (TRUNCATE on a non-WAL database is a harmless no-op).
    Best-effort - never blocks a backup over this failing.
    """
    try:
        con = sqlite3.connect(DB_NAME)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
    except Exception:
        pass


def _write_encrypted_zip(src_db_path, zip_path, password):
    """AES-256 encrypts src_db_path into a single-file zip at zip_path,
    password-protected with `password`. Raises on any failure - callers
    (backup_now()) decide what "encryption failed" should fall back to,
    this function itself never silently produces a half-written zip."""
    with pyzipper.AESZipFile(
        zip_path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.write(src_db_path, arcname=os.path.basename(src_db_path))


def _extract_encrypted_zip(zip_path, dest_db_path, password):
    """Reverse of _write_encrypted_zip() - decrypts the one .db entry
    inside zip_path and writes it to dest_db_path. Raises (wrong
    password, corrupted zip, etc.) rather than silently producing a
    partial/empty database - restore_backup() is the one operation that
    deliberately overwrites the live database, so a failure here MUST
    stop before touching pharmacy.db, not fail halfway through it."""
    with pyzipper.AESZipFile(zip_path, "r") as zf:
        zf.setpassword(password.encode("utf-8"))
        inner_name = zf.namelist()[0]
        with zf.open(inner_name) as src, open(dest_db_path, "wb") as dst:
            dst.write(src.read())


def get_secondary_backup_folder():
    """
    Reads Settings' optional second backup folder (USB/network drive, or
    a Google Drive/OneDrive Desktop sync folder mounted locally - see
    settings.py's own comment on why this was chosen over a real Google
    Drive API integration). Defensive on purpose: this runs from
    backup_now(), which main.py calls unconditionally on EVERY app
    startup, before the Settings screen has necessarily ever been opened
    on a fresh install - so a missing settings table/column must return
    "" (feature off) rather than raise, or the very first backup_now()
    call of a fresh install would fail.
    """
    try:
        con = sqlite3.connect(DB_NAME)
        cur = con.cursor()
        cur.execute("SELECT secondary_backup_folder FROM settings LIMIT 1")
        row = cur.fetchone()
        con.close()
        return (row[0] or "").strip() if row else ""
    except Exception:
        return ""


def mirror_to_secondary(backup_path):
    """
    Best-effort copy of an already-written backup file to the configured
    second folder. Deliberately never raises past this function - a
    disconnected USB drive or an unmapped network path must never fail
    the PRIMARY backup that already succeeded, it should just skip the
    mirror copy silently (returns False). Returns True only on a
    confirmed successful copy.
    """
    folder = get_secondary_backup_folder()
    if not folder:
        return False
    try:
        if not os.path.isdir(folder):
            return False
        dest = os.path.join(folder, os.path.basename(backup_path))
        shutil.copy2(backup_path, dest)
        return True
    except Exception:
        return False


def backup_now():
    """
    pharmacy.db-ஐ dated filename-ஆ backups/ folder-ல copy பண்ணும்.
    ஒரே நாளில் ஒரு backup மட்டும் (already இருந்தா skip பண்ணும்).
    ஒரு 2nd backup folder Settings-ல configure பண்ணி இருந்தா, அதே backup
    file அங்கயும் mirror ஆகும் (best-effort - disconnected drive backup-ஐ
    fail பண்ணாது).

    Sep 2026: pyzipper இருந்தா, இந்த backup ஒரு plain .db file-ஆ இல்லாம,
    BACKUP_ZIP_PASSWORD-ஆல் encrypt பண்ணப்பட்ட .zip file-ஆ save ஆகும்
    (plain .db copy உடனே delete ஆகிடும் - encrypted .zip மட்டும் தான்
    disk-ல மிச்சம் இருக்கும்). pyzipper இல்லாத பழைய setup-ல, முன்பு போலவே
    plain .db backup தான் தொடரும் - encryption இல்லாம backup நடக்காம
    போவதை விட, encryption இல்லாம ஆனாலும் backup நடப்பது தான் முக்கியம்.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)

    today_str = datetime.now().strftime("%Y-%m-%d")
    base_filename = f"pharmacy_backup_{today_str}"
    plain_path = os.path.join(BACKUP_DIR, base_filename + ".db")
    zip_path = os.path.join(BACKUP_DIR, base_filename + ".zip")

    # இன்று ஏற்கனவே backup ஆகி இருக்கான்னு, plain (.db) அல்லது encrypted
    # (.zip) - எது இருந்தாலும் பாக்கும்.
    existing_path = zip_path if os.path.exists(zip_path) else (
        plain_path if os.path.exists(plain_path) else None
    )
    if existing_path:
        # இன்று ஏற்கனவே backup ஆயிடுச்ச, மறுபடி தேவை இல்ல - ஆனாலும் 2nd
        # folder mirror இன்னும் ஆகலைன்னா (அன்று backup ஆன பிறகு தான்
        # 2nd folder configure பண்ணிருக்கலாம்) அத தவற விடாம try பண்ணும்.
        mirror_to_secondary(existing_path)
        return existing_path, False

    _checkpoint_wal()
    shutil.copy2(DB_NAME, plain_path)

    backup_path = plain_path
    if PYZIPPER_AVAILABLE:
        try:
            _write_encrypted_zip(plain_path, zip_path, BACKUP_ZIP_PASSWORD)
            os.remove(plain_path)
            backup_path = zip_path
        except Exception:
            # Encryption தோத்தாலும், இன்னைக்கு backup இல்லாம போற அளவுக்கு
            # அது ஒரு பெரிய பிரச்சனை இல்ல - already எடுத்த plain .db
            # backup-ஐ அப்படியே வெச்சிடும்.
            backup_path = plain_path

    _cleanup_old_backups()
    mirror_to_secondary(backup_path)
    return backup_path, True


def _cleanup_old_backups():
    """KEEP_DAYS-க்கு மேற்பட்ட பழைய backup files-ஐ நீக்கும் - பழைய plain
    .db backups-உம், புது encrypted .zip backups-உம் சேர்த்து."""
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)

    for fname in os.listdir(BACKUP_DIR):
        if not fname.startswith("pharmacy_backup_") or not (
            fname.endswith(".db") or fname.endswith(".zip")
        ):
            continue
        fpath = os.path.join(BACKUP_DIR, fname)
        file_time = datetime.fromtimestamp(os.path.getmtime(fpath))
        if file_time < cutoff:
            os.remove(fpath)


def list_backups():
    """இருக்கிற backups-ஐ ஒரு list-ஆ திருப்பும் (latest முதலில்) - பழைய
    plain .db backups-உம், புது encrypted .zip backups-உம் சேர்த்து."""
    if not os.path.exists(BACKUP_DIR):
        return []
    files = [
        f for f in os.listdir(BACKUP_DIR)
        if f.startswith("pharmacy_backup_") and (f.endswith(".db") or f.endswith(".zip"))
    ]
    files.sort(reverse=True)
    return files


def restore_backup(filename):
    """
    Restores pharmacy.db from a backup file inside BACKUP_DIR - `filename`
    can be an older plain .db backup OR a newer encrypted .zip backup
    (see backup_now()'s docstring); this function tells them apart by
    extension and decrypts with BACKUP_ZIP_PASSWORD automatically for a
    .zip, so callers (settings.py's Restore dialog) don't need to care
    which kind a given backup is.

    Takes a safety copy of the CURRENT (about-to-be-overwritten)
    database first, prefixed "pre_restore_" - this is the one operation
    in the whole app that deliberately replaces the live database
    wholesale, so it gets its own extra safety net beyond the normal
    daily backup_now() rotation: a restore triggered by mistake, or a
    restore of the wrong file, is itself still recoverable afterwards.

    Returns the path of that pre-restore safety copy. Raises
    FileNotFoundError if `filename` doesn't exist in BACKUP_DIR.
    """
    backup_path = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(backup_path):
        raise FileNotFoundError(f"Backup file not found: {filename}")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safety_path = os.path.join(BACKUP_DIR, f"pre_restore_{timestamp}.db")
    _checkpoint_wal()
    shutil.copy2(DB_NAME, safety_path)

    if filename.endswith(".zip"):
        _extract_encrypted_zip(backup_path, DB_NAME, BACKUP_ZIP_PASSWORD)
    else:
        shutil.copy2(backup_path, DB_NAME)
    return safety_path
