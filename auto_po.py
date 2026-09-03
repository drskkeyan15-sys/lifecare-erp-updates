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
"""

from datetime import datetime

import purchase_order_repository as repo
import audit_log

# Matches purchase_order.py's own LOW_STOCK_THRESHOLD default - only
# used as the fallback when a medicine has no reorder_level of its own set.
AUTO_PO_THRESHOLD = 10

AUTO_PO_NOTE = "Auto-generated draft (stock hit reorder level) - review qty/supplier before marking Sent."
AUTO_PO_CREATED_BY = "Auto-System"


def generate_auto_draft_pos(threshold=AUTO_PO_THRESHOLD):
    """
    Groups currently low-stock medicines (medicine_master.stock at or
    below its own reorder_level, or `threshold` when reorder_level is
    unset - identical rule to the manual button) by each medicine's
    last-used supplier, then saves one Draft PO per supplier group -
    skipping any medicine that already has an open Draft/Sent PO, and
    any medicine with no purchase history (no supplier to address a PO to).

    Returns a list of (po_no, supplier, items) tuples for every PO
    actually created this run (items = list of (medicine_name, qty)).
    Empty list if nothing needed doing. Never raises - a single
    supplier's save failing (e.g. a locked DB) is skipped rather than
    aborting the whole run or crashing the Dashboard startup check that
    calls this.
    """
    rows, last_supplier = repo.get_low_stock_with_last_supplier(threshold)
    if not rows:
        return []

    already_open = repo.get_medicines_with_open_po()

    by_supplier = {}
    for name, stock, effective_threshold in rows:
        if name in already_open:
            continue
        supplier = last_supplier.get(name)
        if not supplier:
            continue
        suggested_qty = max(effective_threshold - stock, 1)
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
