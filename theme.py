"""
theme.py
Single source of truth for LifeCare Pharmacy ERP's color palette.

Before this file, the same handful of colors (the brand blue #1565C0,
the field background #F8F9FB, the muted grey label/version text, ...)
were written as raw hex string literals independently in ui_style.py,
login.py, dashboard.py, and elsewhere. That worked, but it means a
future rebrand or a dark-mode toggle (the icons/moon.png and
icons/sun.png already sitting in the icons folder suggest one was
planned) would mean grep-and-replacing the same hex codes across a dozen
files by hand, with no guarantee every occurrence was caught.

Every screen should import its colors from here instead of writing a
new "#1565C0" by hand. This file makes NO visual change on its own -
every value below is copied from the colors already in use (ui_style.py,
login.py) - except the two marked "FIXED", which were failing WCAG AA
contrast checks (see the Aug 2026 accessibility pass) and are corrected
here to the nearest visually-similar shade that actually passes.
"""

# ─── Brand ───────────────────────────────────────────────────────────
PRIMARY = "#1565C0"          # main brand blue - headers, primary buttons
PRIMARY_HOVER = "#1976D2"    # button hover/active state
PRIMARY_SUBTLE = "#CFE0F7"   # header subtitle text, light brand tint

# ─── Surfaces ────────────────────────────────────────────────────────
SURFACE_WHITE = "#FFFFFF"
SURFACE_FIELD = "#F8F9FB"    # input field / card background

# ─── Text ────────────────────────────────────────────────────────────
TEXT_PRIMARY = "#222222"     # entry/body text
TEXT_LABEL = "#555555"       # form field labels
# FIXED (was #9AA3B0, 2.55:1 on white - failed WCAG AA even for large
# text). Same grey family, darkened until it actually clears 4.5:1.
TEXT_MUTED = "#6B7280"
# A second, separately-chosen muted grey (billing.py's "no sales yet"
# empty-state text) that already passed WCAG AA on its own (4.61:1) -
# kept as its OWN token rather than folded into TEXT_MUTED above, since
# the two are visually close but not identical; merging them would be a
# real (if subtle) visual change, not the zero-change token extraction
# this pass is meant to be.
TEXT_MUTED_ALT = "#757575"

# ─── Borders ─────────────────────────────────────────────────────────
BORDER_FOCUS = PRIMARY
# FIXED (was #D0D5DB, 1.40:1 on SURFACE_FIELD - an unfocused input's
# edge was nearly invisible, failing WCAG 1.4.11's 3:1 minimum for UI
# component boundaries, not just text). Darkened to a soft slate that
# clears 3:1 without reading as an error/active state.
BORDER_DEFAULT = "#7A8694"

# ─── Tables (Treeview / tksheet - see ui_style.py) ──────────────────
TABLE_HEADER_BG = PRIMARY
TABLE_HEADER_FG = SURFACE_WHITE
TABLE_ROW_EVEN = "#F7F9FA"
TABLE_ROW_ODD = SURFACE_WHITE
TABLE_SELECT_BG = "#BBDEFB"
TABLE_SELECT_FG = "#0D47A1"
TABLE_GRID = "#9E9E9E"

# ─── Status colors (shared meaning across Stock/Smart Alerts/etc.) ──
STATUS_DANGER = "#C62828"    # expired / out of stock
STATUS_WARNING = "#F9A825"   # low stock / expiring soon
STATUS_SUCCESS = "#2E7D32"   # in stock / healthy

# ─── Feature accent buttons (billing.py / reports.py / gst_reports.py) ─
# Unlike the tokens above, these are each used in exactly ONE place for
# one specific action - kept as separate named colors (not merged into
# each other or into STATUS_*) precisely because forcing two visually
# different shades onto the same token WOULD be a real visual change,
# not a no-op refactor. Every value here is copied byte-for-byte from
# the hex literal it replaces.
ACCENT_SUBSTITUTE = "#EF6C00"     # billing.py "View Substitutes" warning button
ACCENT_NEUTRAL = "#607D8B"        # billing.py "Clear Selection" / Quick Picks "Refresh"
ACCENT_PRESCRIPTION = "#6A1B9A"   # billing.py "Scan Prescription" button
ACCENT_PRINT = "#FF9800"          # billing.py "Print Bill" button
WARNING_BANNER_BG = "#FFF3E0"     # billing.py Schedule H1 prescription-required banner
WARNING_BANNER_FG = "#E65100"     # ...that banner's text
QUICK_PICK_BG = "#E3F2FD"         # billing.py Quick Pick button resting background
# (Quick Pick's hover/active state already reuses TABLE_SELECT_BG above -
# it was already the exact same hex, #BBDEFB, before this file existed.)

ACCENT_SCHEDULE_X = "#B71C1C"     # reports.py "Schedule X Register" button
ACCENT_RX_REGISTER = "#4527A0"    # reports.py "Prescription Register" button
ACCENT_COLD_CHAIN = "#0277BD"     # reports.py "Cold Chain Stock" button
# Matches dashboard.py's LIGHT_THEME["body_bg"] exactly (same shade,
# independently chosen) - NOT wired to that dict on purpose (see
# dashboard.py's own comment on why its body theme system is left
# untouched); this token is only for reports.py's own table background.
SURFACE_PAGE = "#ecf0f1"

ACCENT_PDF_EXPORT = "#D32F2F"     # gst_reports.py "Download PDF" button

# ─── dashboard.py's static chrome (top bar + sidebar) ───────────────
# Explicitly NOT the same thing as dashboard.py's LIGHT_THEME/DARK_THEME
# dicts - those drive the dashboard BODY's working dark-mode toggle and
# are intentionally left alone (see that file's own comment on why a
# full app-wide dark mode was scoped out). The top bar and sidebar are
# static brand-navy in both light and dark mode already, so they're a
# plain, safe swap - same tier as PRIMARY above.
#
# ─── Concept A "Refined Brand Blue" sidebar palette (Sep 2026) ──────
# User-approved redesign direction (chose Concept A over Concept B,
# 2026-09-04). Kept as FLAT solid colors, not a real navy gradient (the
# mockup's visual top-to-bottom shade) - a gradient would need a
# Canvas-drawn sidebar background instead of the existing plain
# tk.Frame, which adds real rendering cost on every screen open. Also
# important: the sidebar is built ONCE in dashboard.py's build_ui() and
# is never destroyed/rebuilt when switching screens (only self.body's
# contents are, in clear_body()/open_module() - see that file's own
# "FLASH FIX ATTEMPT" comment on the already-investigated screen-switch
# delay). So changing these four color values is zero-risk to that
# issue - a static sidebar repaint cannot make the transition slower.
SIDEBAR_BG = "#1B4F91"            # was #2C3E50 (slate) - refined brand navy-blue
SIDEBAR_HEADER_BG = "#123A6E"     # category group headers (was raw "#1B2631" in dashboard.py)
SIDEBAR_ITEM_BG = "#2A5DA8"       # sidebar sub-item buttons, resting (was raw "#34495E")
SIDEBAR_ITEM_HOVER = "#3D72C4"    # sub-item hover / keyboard-focus state (was raw "#1B4F72")
