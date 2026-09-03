"""
update_check.py
Life Care Pharmacy ERP - Auto-Update Notify + Install (Sep 2026)

Why this exists: right now, the only way to know a newer LifeCareERP.exe
build exists is someone manually remembering to check - which is exactly
how old builds like LifeCareERP.exe / LifeCareERP2.exe / LifeCarePharma
Final.exe kept running side by side on this PC with no one sure which was
newest. This checks a tiny public GitHub repo's version.txt file at
startup and, if it says a newer version than APP_VERSION (app_paths.py)
exists, offers to download and install it.

2026-09-03: extended from "notify only" to "download + apply", for a
pharmacist running this SAME source folder on more than one shop/branch
computer, each with its own separate pharmacy.db - PUBG/Free Fire-style
("check server, download update, apply it") rather than a plain popup
telling a non-technical shop owner to "contact your software provider".

Still deliberately SAFE about what it will and won't touch:
  - Never overwrites pharmacy.db (or its -journal/-wal/-shm sidecars),
    the Invoices/ folder, the backups/ folder, or __pycache__/.git - the
    exact same exclude list installer.iss's [Files]/[UninstallDelete]
    sections use, for the exact same reason (see that file's comments):
    a software update must never be how a pharmacy loses its stock/sales
    history. Only .py application files are ever copied in.
  - Only ever downloads a PUBLIC repo's zip snapshot over plain HTTPS -
    no login/token/credentials of any kind are asked for, stored, or
    needed (reading a public GitHub repo needs none).
  - Never applies anything without the pharmacist explicitly clicking
    "Yes" on a plain confirmation popup first - this is not a silent
    background patch.
  - Runs the network check itself on a background thread and NEVER
    blocks app startup - a slow or completely absent internet connection
    (common on a shop PC) just means the popup never appears that run,
    same as if the check was never called at all. Any check failure (no
    internet, GitHub unreachable, malformed version.txt) is swallowed
    silently.
  - The actual file copy also runs on a background thread (after the
    pharmacist says Yes) so the UI never freezes on a slow connection;
    any failure there is reported back in plain language and leaves the
    app's files exactly as they were - a half-failed download is
    discarded, never half-applied.
  - Copying new .py files over the running app's OWN already-imported
    copies is safe (Python doesn't keep a source file "open" after
    importing it - same reason Claude editing these files by hand mid-
    session, all through Sep 2026, never once required closing the app
    first) - but the NEW code only takes effect after the app is closed
    and reopened, same as any other source-file update. This never tries
    to hot-swap code inside an already-running screen.
"""

import os
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from tkinter import messagebox

from app_paths import APP_VERSION, BASE_DIR

# Single source-of-truth update-check location (same pattern as
# APP_VERSION / IDLE_LOCK_MINUTES / BACKUP_ZIP_PASSWORD) - the public
# lifecare-erp-updates GitHub repo. To point this at a different repo
# later, change only these two lines.
GITHUB_OWNER = "drskkeyan15-sys"
GITHUB_REPO = "lifecare-erp-updates"

UPDATE_CHECK_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/version.txt"
UPDATE_ZIP_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/archive/refs/heads/main.zip"

# How long to wait for GitHub to answer before giving up for this run -
# short on purpose, this must never make app startup feel slow.
_TIMEOUT_SECONDS = 5
# The actual update download is a bigger payload (the whole source repo,
# not one small text file) - a more generous timeout, still bounded so a
# dead connection can't hang forever.
_DOWNLOAD_TIMEOUT_SECONDS = 30

# Real pharmacy data an update must NEVER touch - same exclude list as
# installer.iss's [Files] Excludes / [UninstallDelete] sections (see that
# file's own comments for the full "software that deletes this on
# uninstall is how shops lose sales history by accident" reasoning).
_NEVER_OVERWRITE_FILES = {
    "pharmacy.db", "pharmacy.db-journal", "pharmacy.db-wal", "pharmacy.db-shm",
}
_NEVER_OVERWRITE_DIRS = {"invoices", "backups", "__pycache__", ".git", "venv", "build_venv"}


def _parse_version(version_text):
    """"1.2.10" -> (1, 2, 10); returns None for anything that doesn't
    look like a plain dotted-number version, so a typo'd version.txt
    (or someone putting other text in it by mistake) just disables the
    check for that run instead of crashing it."""
    try:
        return tuple(int(part) for part in version_text.strip().split("."))
    except Exception:
        return None


def _is_newer(remote_version_text, local_version_text):
    remote = _parse_version(remote_version_text)
    local = _parse_version(local_version_text)
    if remote is None or local is None:
        return False
    return remote > local


def _download_and_apply_update():
    """Downloads the whole lifecare-erp-updates repo as a zip (public
    repo, plain HTTPS, no login needed) and copies every .py file it
    contains over this install's own copy of that same file, skipping
    anything under _NEVER_OVERWRITE_FILES/_NEVER_OVERWRITE_DIRS.

    Returns (True, files_copied_count) on success, or (False,
    error_message) - never raises, so the caller can always show a
    plain message instead of a traceback reaching a shop worker."""
    tmp_dir = tempfile.mkdtemp(prefix="lifecare_update_")
    try:
        with urllib.request.urlopen(UPDATE_ZIP_URL, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
            zip_bytes = response.read()

        zip_path = os.path.join(tmp_dir, "update.zip")
        with open(zip_path, "wb") as f:
            f.write(zip_bytes)

        extract_dir = os.path.join(tmp_dir, "extracted")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

        # GitHub's own "download zip" always wraps everything in one
        # top-level folder named "<repo>-<branch>" (e.g.
        # "lifecare-erp-updates-main") - if that's not what we got, the
        # repo/zip isn't shaped the way this function assumes, so bail
        # out rather than guess at some other layout.
        entries = os.listdir(extract_dir)
        if len(entries) != 1 or not os.path.isdir(os.path.join(extract_dir, entries[0])):
            return False, "Update package was not in the expected format."
        source_root = os.path.join(extract_dir, entries[0])

        copied = 0
        for root, dirs, files in os.walk(source_root):
            dirs[:] = [d for d in dirs if d.lower() not in _NEVER_OVERWRITE_DIRS]
            rel_dir = os.path.relpath(root, source_root)
            for fname in files:
                if fname.lower() in _NEVER_OVERWRITE_FILES:
                    continue
                if not fname.lower().endswith(".py"):
                    continue
                src_file = os.path.join(root, fname)
                dest_dir = BASE_DIR if rel_dir == "." else os.path.join(BASE_DIR, rel_dir)
                os.makedirs(dest_dir, exist_ok=True)
                dest_file = os.path.join(dest_dir, fname)
                shutil.copyfile(src_file, dest_file)
                copied += 1

        if copied == 0:
            return False, "Update package had no application files to install."
        return True, copied
    except Exception as e:
        return False, str(e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _check_and_notify(root):
    try:
        with urllib.request.urlopen(UPDATE_CHECK_URL, timeout=_TIMEOUT_SECONDS) as response:
            remote_version = response.read().decode("utf-8").strip()
    except Exception:
        # No internet, GitHub unreachable, etc. - silently skip. This
        # check happens again next time the app is opened.
        return

    if not _is_newer(remote_version, APP_VERSION):
        return

    def ask_and_update():
        try:
            if not root.winfo_exists():
                return
            wants_update = messagebox.askyesno(
                "Update Available / புது Update இருக்கு",
                "A newer version of Life Care Pharmacy ERP is available.\n"
                f"You are using: {APP_VERSION}\n"
                f"Latest version: {remote_version}\n\n"
                "Download and install it now?\n"
                "(Your medicine stock, sales, invoices and backups are "
                "never touched by this.)\n\n"
                "இப்போ நீங்க உபயோகிக்கிற version-ஐ விட புதிய version "
                "கிடைக்கிறது.\n"
                "இப்போதே Download செய்து Install செய்யவா?\n"
                "(உங்கள் Stock/Sales/Invoices/Backups எதுவும் தொடப்படாது.)"
            )
        except Exception:
            return
        if not wants_update:
            return

        def do_update():
            ok, result = _download_and_apply_update()

            def show_result():
                try:
                    if not root.winfo_exists():
                        return
                    if ok:
                        messagebox.showinfo(
                            "Update Installed / Update முடிந்தது",
                            f"Update downloaded and installed ({result} files updated).\n\n"
                            "Please CLOSE this app now and open it again to use the "
                            "new version.\n\n"
                            "Update வெற்றிகரமாக Install ஆனது.\n"
                            "இந்த App-ஐ இப்போது மூடிவிட்டு மறுபடியும் திறந்து "
                            "புதிய version-ஐ பாருங்க."
                        )
                    else:
                        messagebox.showerror(
                            "Update Failed / Update தோல்வி",
                            f"Could not complete the update:\n{result}\n\n"
                            "Nothing on your computer was changed - your app keeps "
                            "working exactly as before. Please try again later, or "
                            "check your internet connection.\n\n"
                            "Update முடியவில்லை. உங்க Computer-ல் எதுவும் மாறவில்லை. "
                            "App முன்பு போலவே வேலை செய்யும். பிறகு மறுபடியும் "
                            "முயற்சி செய்யுங்க."
                        )
                except Exception:
                    pass

            root.after(0, show_result)

        threading.Thread(target=do_update, daemon=True).start()

    root.after(0, ask_and_update)


def check_for_update(root):
    """Call once, shortly after the Dashboard is up (see dashboard.py) -
    fires the network check on a background thread and returns
    immediately, so it never delays login/dashboard startup itself."""
    threading.Thread(target=_check_and_notify, args=(root,), daemon=True).start()
