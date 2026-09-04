"""
auto_po.py
Life Care Pharmacy ERP - background/automatic Purchase Order draft
generator (Aug 2026).

WHY THIS EXISTS: Purchase Order already has a manual "Load Low-Stock
Items for this Supplier" button (purchase_order.py) - a pharmacist has
to remember to open that screen and click it. This module runs the same
low-stock check automatically, once per app session, without anyone
having to open the Purchase Order screen at all - see dashboard.py's
self.root.after(...) call, wired the same way as check_license_reminders().

WHAT IT DOES NOT DO: it never sends anything to a supplier and never
marks a PO "Sent" - every PO it creates is saved with status "Draft",
the exact same status a manually-created PO starts in, so it always
needs a human to open Purchase Order, review the quantities/supplier,
and explicitly mark it "Sent" (or edit/cancel it) - nothing is
committed to a supplier without a pharmacist's review. This matches
what was told to the user before this feature was built.

DUPLICATE PROTECTION: a medicine already covered by an open (Draft or
Sent) PO is skipped - see purchase_order_repository.get_medicines_with_open_po().
Without this, simply reopening the app every morning would pile up a
fresh duplicate Draft PO for the same medicine every single day.

SUPPLIER GROUPING: reuses the exact same "last supplier used for this
medicine" lookup as the manual button (purchase_order_repository.
get_low_stock_with_last_supplier()) - a medicine with no purchase
history at all has no supplier to address a PO to, so it's skipped here
too (same limitation the manual button already has).

VELOCITY CHECK (Sep 2026): the original version of this file only ever
looked at medicine_master.stock vs. a flat/per-medicine reorder_level -
a fast-selling medicine could be well above that number today and still
run out before anyone notices, because nothing here looked at how fast
it was actually selling. Smart Alerts' "Reorder Predictions" tab
(stock_alerts_gui.py) already solved this, but only as an on-demand
screen a pharmacist has to remember to open - it never fed this
automatic generator. generate_auto_draft_pos() now also calls
purchase_order_repository.get_velocity_low_stock_with_last_supplier()
and merges its results in with the flat-threshold ones below, so a
fast mover gets an automatic Draft PO too, not just a manual one. See
that function's own docstring for why it's a separate query rather
than reusing the GUI tab's code directly.
"""

from datetime import datetime

import purchase_order_repository as repo
import audit_log

# Matches purchase_order.py's own LOW_STOCK_THRESHOLD default - only
# used as the fallback when a medicine has no reorder_level of its own set.
AUTO_PO_THRESHOLD = 10

# Matches stock_alerts_gui.py's SmartAlertsDashboard.REORDER_LEAD_DAYS/
# REORDER_WINDOW_DAYS defaults, so a medicine that would show up on the
# manual "Reorder Predictions" tab (at its default settings) is exactly
# the same medicine this automatic check would also catch.
AUTO_PO_VELOCITY_LEAD_DAYS = 15
AUTO_PO_VELOCITY_WINDOW_DAYS = 30

AUTO_PO_NOTE = "Auto-generated draft (stock hit reorder level, or fast-moving stock) - review qty/supplier before marking Sent."
AUTO_PO_CREATED_BY = "Auto-System"


def _merge_candidates(threshold_rows, velocity_rows):
    """Combines the flat-threshold rows (name, stock, effective_threshold)
    with the velocity rows (name, stock, suggested_qty) into a single
    {name: suggested_qty} dict. A medicine caught by only one check uses
    that check's own suggested quantity unchanged. A medicine caught by
    BOTH checks keeps whichever suggested quantity is larger - ordering
    less than either individual check thought was needed would defeat
    the point of running both, so ties always resolve toward the safer
    (bigger) number, never an average or a minimum."""
    suggested = {}
    for name, stock, effective_threshold in threshold_rows:
        suggested[name] = max(effective_threshold - stock, 1)
    for name, stock, suggested_qty in velocity_rows:
        if name in suggested:
            suggested[name] = max(suggested[name], suggested_qty)
        else:
            suggested[name] = suggested_qty
    return suggested


def generate_auto_draft_pos(threshold=AUTO_PO_THRESHOLD,
                             velocity_lead_days=AUTO_PO_VELOCITY_LEAD_DAYS,
                             velocity_window_days=AUTO_PO_VELOCITY_WINDOW_DAYS):
    """
    Groups currently low-stock medicines by each medicine's last-used
    supplier, then saves one Draft PO per supplier group - skipping any
    medicine that already has an open Draft/Sent PO, and any medicine
    with no purchase history (no supplier to address a PO to).

    A medicine qualifies either by the flat threshold rule
    (medicine_master.stock at or below its own reorder_level, or
    `threshold` when reorder_level is unset - the original rule this
    file always had) OR by the sales-velocity rule (predicted to run
    out within `velocity_lead_days` given its last `velocity_window_days`
    days of sales - see purchase_order_repository.
    get_velocity_low_stock_with_last_supplier()). A medicine matching
    both rules is ordered at the larger of the two suggested quantities.

    Returns a list of (po_no, supplier, items) tuples for every PO
    actually created this run (items = list of (medicine_name, qty)).
    Empty list if nothing needed doing. Never raises - a single
    supplier's save failing (e.g. a locked DB) is skipped rather than
    aborting the whole run or crashing the Dashboard startup check that
    calls this.
    """
    threshold_rows, threshold_supplier = repo.get_low_stock_with_last_supplier(threshold)
    velocity_rows, velocity_supplier = repo.get_velocity_low_stock_with_last_supplier(
        velocity_lead_days, velocity_window_days
    )

    if not threshold_rows and not velocity_rows:
        return []

    last_supplier = dict(threshold_supplier)
    last_supplier.update(velocity_supplier)  # velocity lookup covers the same names; harmless either way

    suggested_qty_by_name = _merge_candidates(threshold_rows, velocity_rows)

    already_open = repo.get_medicines_with_open_po()

    by_supplier = {}
    for name, suggested_qty in suggested_qty_by_name.items():
        if name in already_open:
            continue
        supplier = last_supplier.get(name)
        if not supplier:
            continue
        by_supplier.setdefault(supplier, []).append((name, suggested_qty))

    date = datetime.now().strftime("%Y-%m-%d")
    created = []
    for supplier, items in by_supplier.items():
        try:
            po_no = repo.save_purchase_order(date, supplier, items, AUTO_PO_NOTE, AUTO_PO_CREATED_BY)
        except Exception:
            continue  # one supplier's save failing must not block the rest
        try:
            audit_log.log_action(
                "Purchase Order", "Auto-Create",
                f"Auto-generated {po_no} for supplier '{supplier}' with {len(items)} low-stock item(s)"
            )
        except Exception:
            pass
        created.append((po_no, supplier, items))

    return created
