"""
audit_log.py
LifeCare Pharmacy ERP - shared "who did what, when" logger.

One tiny helper (log_action) that any screen can call after a real
change (edit, delete, stock adjustment, ...) - kept deliberately generic
(free-text `details`, not a column per possible field) since new screens
will keep needing to log new kinds of actions, and a rigid schema would
need a migration every time. Pairs with session.py (which supplies the
"who" - the currently logged-in username) and audit_log_gui.py (the
screen that lets an Admin browse this table).

Never raises into the caller - a logging failure must not block or
crash whatever real database change it was recording, so log_action()
swallows its own errors after printing a console warning.
"""

import sqlite3
from datetime import datetime

from app_paths import DB_NAME
import session


def log_action(screen, action, details="", db_name=None):
    """
    Records one audit entry: who (from session.get_current_user()),
    when (now), which screen, what action (short label like "Update",
    "Delete", "Stock Adjustment"), and a free-text details string
    (e.g. "Deleted medicine 'Dolo 650' (id=42)").
    """
    db_name = db_name or DB_NAME
    try:
        con = sqlite3.connect(db_name)
        cur = con.cursor()
        cur.execute(
            "INSERT INTO audit_log(log_time, username, screen, action, details) VALUES (?,?,?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session.get_current_user(), screen, action, details)
        )
        con.commit()
        con.close()
    except Exception as e:
        # Deliberately swallowed - see module docstring. Printed instead
        # of silently vanishing, so a broken audit_log table (e.g. a
        # future migration typo) is still discoverable in the console.
        print(f"audit_log warning: could not record '{action}' on '{screen}': {e}")


def get_recent_entries(limit=500, db_name=None):
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute(
        "SELECT log_time, username, screen, action, details "
        "FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    con.close()
    return rows


def search_entries(search_text, limit=500, db_name=None):
    """Simple in-memory filter over get_recent_entries() - same
    'filter what's already loaded' pattern used by every other search
    box in this app (medicine_master.py's search_data(), etc.)."""
    text = (search_text or "").strip().lower()
    rows = get_recent_entries(limit, db_name)
    if not text:
        return rows
    return [row for row in rows if text in " ".join(str(v or "") for v in row).lower()]
