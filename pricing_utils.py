"""
pricing_utils.py
Shared pack-size parsing for LifeCare Pharmacy ERP.

This used to live only inside stock.py, and billing.py had grown its own
simpler (and wrong) copy of the same idea. The two drifted apart:
stock.py knew that "60ML" means "one bottle" (multiplier 1), but
billing.py's copy just grabbed the first number in the string and divided
by it - so a 60ML bottle got priced as if it were 60 separate units
(₹36 bottle -> ₹0.60/unit), and combo packs like "5X10'S" (5 strips of
10 = 50 units) only picked up the first number (5), undercharging by 10x.

Every module that needs to know "how many sellable units are in this
pack" should import get_pack_multiplier from here instead of writing
its own version.
"""

import re
from money import to_money


def get_pack_multiplier(pack_raw):
    """
    Returns how many individual sellable units are inside one pack record's
    `sale` price, so unit_price = sale / get_pack_multiplier(pack_size).

    Examples:
      "60ML"      -> 1   (one bottle is the sellable unit - do NOT divide by 60)
      "100GM"     -> 1   (one tube/jar is the sellable unit)
      "10'S"      -> 10  (strip of 10 tablets, sold per tablet)
      "100'S"     -> 100 (box of 100 masks, sold per mask)
      "5X10'S"    -> 50  (5 strips of 10 = 50 tablets total)
      "1X12"      -> 12  (1 box containing 12 units = 12 units)
      "5X10ML"    -> 5   (5 vials of 10ML each - sellable unit is the vial, not the ml)
      ""  / None  -> 1   (unknown/blank - treat as a single unit, never divide by 0)
    """
    pack_str = str(pack_raw or "1").upper().replace(" ", "").replace("'", "")

    # Combo packs like "5*10" or "1X12"
    if '*' in pack_str or 'X' in pack_str:
        nums = re.findall(r'\d+', pack_str)
        if len(nums) >= 2:
            n1, n2 = int(nums[0]), int(nums[1])
            if n1 == 1:
                return n2
            if "ML" in pack_str or "GM" in pack_str or "MG" in pack_str:
                # e.g. "5X10ML" -> 5 vials of 10ml, sellable unit is the vial
                return n1
            return n1 * n2

    # Pure volume/weight packs ("60ML", "100GM") - the bottle/tube itself
    # is the sellable unit, never divide the price by the ml/gram count.
    if re.search(r'\d+(ML|GM|MG|G|M|KG|L)$', pack_str):
        return 1

    # Plain piece-count packs: "10'S", "100'S", "50"
    nums = re.findall(r'\d+', pack_str)
    if nums:
        val = int(nums[0])
        return val if val > 0 else 1

    return 1


def get_unit_price(sale_price, pack_raw):
    """Convenience wrapper: full pack sale price -> price per sellable unit."""
    try:
        sale_val = float(sale_price or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if sale_val <= 0:
        return 0.0
    multiplier = get_pack_multiplier(pack_raw)
    return to_money(sale_val / multiplier)


def guess_display_unit(pack_raw):
    """
    Best-effort "NOS/LTR/GM/..." unit label for display only (Price
    List / Stock Summary screens, 2026-08-22) - cosmetic, matching the
    look of BharatERP's screenshots (whose Item Master has a real,
    separate Unit-of-Measure field). This app's own schema has no such
    column - `pack_size` is a free-text description like "10'S" or
    "60ML" (see get_pack_multiplier's own docstring) - so this GUESSES
    a unit from that same text instead of a stored, authoritative
    value. Never used for any stock-quantity math, only for the label
    printed next to a number.

    Examples: "60ML" -> "ML", "100GM" -> "GM", "2KG" -> "KG",
    "10'S"/"100'S"/"5X10'S"/"" -> "NOS" (falls back to piece-count).
    """
    pack_str = str(pack_raw or "").upper()
    for unit in ("ML", "GM", "MG", "KG", "LTR", "L"):
        if unit in pack_str:
            return unit
    return "NOS"


def allocate_fifo(usable_batches, qty_needed):
    """
    Given batch dicts (each with "batch", "stock", "price", already
    sorted earliest-expiry-first - see billing.py's get_fifo_batches())
    and the quantity a sale needs, returns the allocations needed to
    fill qty_needed: the earliest-expiring batch is consumed first, and
    a sale automatically spills into the next batch once one runs out.
    This is the actual FIFO rule the app is built around (see
    changes.md - the bug this replaced was a plain
    `SELECT ... WHERE name=` `.fetchone()` that ignored expiry and batch
    splitting entirely, silently selling whichever batch SQLite happened
    to return first).

    Pulled out of billing.py's add_item() as a plain function (no
    Tkinter/DB dependency) specifically so this financially critical
    allocation math can be unit tested directly - the Treeview-merging
    and UI-message parts stay in add_item() since they need real widgets.

    Returns a list of {"batch", "qty", "price", "total"} dicts - "total"
    is price * qty rounded via money.to_money(). Does NOT check whether
    qty_needed exceeds total available stock; callers already validate
    that before calling this, since the error message needs UI context
    (the medicine's display name) this function doesn't have.
    """
    remaining = qty_needed
    allocations = []
    for b in usable_batches:
        if remaining <= 0:
            break
        take = min(b["stock"], remaining)
        if take <= 0:
            continue
        allocations.append({
            "batch": b["batch"],
            "qty": take,
            "price": b["price"],
            "total": to_money(b["price"] * take),
        })
        remaining -= take
    return allocations
