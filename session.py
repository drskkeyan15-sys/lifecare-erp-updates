"""
session.py
Tiny in-process "who is logged in right now" holder.

Why this exists: every screen (Purchase, Billing, Medicine Master, Stock
Adjustment, ...) is opened through dashboard.py's open_module(), which
only ever constructs a screen with (frame) or (frame, on_close=...) -
the logged-in username/role was never threaded down to individual
screens. Retrofitting that into every one of the ~20 existing module
constructors (new param + every call site + open_module() itself) is a
much bigger, riskier change than the Stock Adjustment / Audit Trail
features that actually need to know "who did this" right now.

This is a deliberate, narrow exception to the project's "avoid global
variables" rule - there is exactly one logged-in user per running
instance of this desktop app (single Windows user, single process, not
a multi-tenant server), so a single process-wide value isn't the kind
of hidden-coupling smell a shared mutable global usually is elsewhere.
Treat this the way you'd treat Flask's `g` or a request-scoped context:
write-once (login.py, right after a successful login), read-only
everywhere else.
"""

CURRENT_USER = None
CURRENT_ROLE = None


def set_current_user(username, role):
    global CURRENT_USER, CURRENT_ROLE
    CURRENT_USER = username
    CURRENT_ROLE = role


def get_current_user():
    """Returns the logged-in username, or "Unknown" if called before
    login (shouldn't normally happen, but callers should never crash
    over a missing audit-trail name)."""
    return CURRENT_USER or "Unknown"


def get_current_role():
    return CURRENT_ROLE or "Unknown"
