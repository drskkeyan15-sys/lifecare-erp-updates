"""
license_reminders.py
LifeCare Pharmacy ERP - FSSAI license expiry check.

Unlike a customer refill reminder, this is a self-reminder for the
pharmacy's OWN license - a missed renewal is a real compliance risk,
not just a lost sale. Checked automatically once per app session
(dashboard.py calls check_and_warn() right after login) instead of only
being visible if someone happens to open Settings and notice the date.

NOTE on DL20/DL21: deliberately NOT checked here. The pharmacist's own
Form 21 licence (verified from the actual issued certificate, Tamil
Nadu, Nov 2021) reads: "The licence unless sooner suspended or
cancelled, shall remain valid perpetually" - drug licences were moved
to perpetual validity by a 2024 amendment to Rule 63 of the Drugs and
Cosmetics Rules, 1945 (a periodic compliance ASSESSMENT still happens,
"not less than once in three years", but that's the Drug Control
Department inspecting on their own schedule, not a renewal deadline the
pharmacist tracks and applies for). A fixed "expiry date" reminder
would be actively wrong for a licence that doesn't expire. Settings
still has dl20_expiry/dl21_expiry fields (harmless, and this is generic
software - a pharmacy in a state/licence-era still on the older 5-year
explicit-renewal rule could use them) but they're excluded from this
check. FSSAI licences are unaffected by that Drugs Rules amendment and
still carry a real fixed expiry, so that one stays.

Reuses whatsapp_integration.py's open_whatsapp_message() so the "Send
WhatsApp Reminder" button behaves identically to every other WhatsApp
send in this app (same phone-normalization, same wa.me deep-link
limitation - it opens a compose window, a human still clicks Send).
"""

import sqlite3
from datetime import datetime

from app_paths import DB_NAME

WARNING_DAYS = 30  # flag as "expiring soon" this many days out or fewer

LICENSE_FIELDS = [
    ("fssai_expiry", "FSSAI License"),
]


def get_expiring_licenses(db_name=None, warning_days=WARNING_DAYS):
    """
    Returns a list of (label, expiry_date_str, days_left) for every
    configured license expiry that's already passed or within
    `warning_days`. days_left is negative for an already-expired
    license. Skips blank/unset fields (not every pharmacy fills in all
    three) and anything that fails to parse - a malformed date must
    never crash the Dashboard startup check.
    """
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    try:
        cur.execute("SELECT dl20_expiry, dl21_expiry, fssai_expiry FROM settings LIMIT 1")
        row = cur.fetchone()
    except sqlite3.OperationalError:
        row = None
    con.close()

    if not row:
        return []

    values = dict(zip([f for f, _ in LICENSE_FIELDS], row))
    today = datetime.now().date()
    results = []

    for field_name, label in LICENSE_FIELDS:
        raw = (values.get(field_name) or "").strip()
        if not raw:
            continue
        try:
            exp_date = datetime.strptime(raw, "%Y-%m-%d").date()
        except Exception:
            continue
        days_left = (exp_date - today).days
        if days_left <= warning_days:
            results.append((label, raw, days_left))

    results.sort(key=lambda r: r[2])
    return results


def get_shop_phone(db_name=None):
    db_name = db_name or DB_NAME
    con = sqlite3.connect(db_name)
    cur = con.cursor()
    cur.execute("SELECT phone FROM settings LIMIT 1")
    row = cur.fetchone()
    con.close()
    return (row[0] or "").strip() if row else ""


def build_reminder_message(expiring):
    lines = ["Life Care Pharmacy - License Renewal Reminder", ""]
    for label, expiry, days_left in expiring:
        if days_left < 0:
            status = f"EXPIRED {abs(days_left)} day(s) ago"
        elif days_left == 0:
            status = "expires TODAY"
        else:
            status = f"expires in {days_left} day(s)"
        lines.append(f"- {label}: {expiry} ({status})")
    return "\n".join(lines)
