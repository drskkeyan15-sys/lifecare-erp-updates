"""
money.py
Decimal-safe currency rounding for LifeCare Pharmacy ERP.

Every price in this app is stored and calculated as a Python float
(SQLite's REAL columns, Tkinter's DoubleVar, reportlab's PDF drawing -
all of them). That is fine for a single price shown on screen, but
binary floating point cannot represent most 2-decimal rupee amounts
exactly (0.10, 1.12, ... are all repeating fractions in binary), and
billing.py's calculate_total() adds up every line of a bill, applies a
discount %, then rounds - three float operations chained together where
tiny per-step errors can compound. In practice this usually still prints
correctly with :.2f, but it is the kind of drift that occasionally shows
up as a total that is one paisa off from a manual recount, and it gets
worse anywhere a percentage or a GST-inclusive amount is divided back
out (see gst_reports.py).

This module does NOT change any database column type (still REAL/float
end to end) and does NOT change any function signature elsewhere - it is
a drop-in replacement for the raw round(x, 2) calls already scattered
through billing.py and gst_reports.py, using Decimal internally so the
rounding step itself is exact, then handing back a plain float.

Rounding rule: ROUND_HALF_UP ("round 0.5 up"), matching what a
pharmacist doing this by hand or on a calculator expects - not Python's
own round()/Decimal default of ROUND_HALF_EVEN ("banker's rounding"),
which most people find surprising for money (round(0.125, 2) with plain
Decimal defaults gives 0.12, not 0.13).
"""

from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict

TWO_PLACES = Decimal("0.01")


def to_money(value) -> float:
    """Round a single numeric value to 2 decimal places via Decimal.
    None/blank/unparseable input is treated as 0.0, never raises."""
    if value is None or value == "":
        return 0.0
    try:
        d = Decimal(str(value))
    except Exception:
        return 0.0
    return float(d.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def money_sum(values) -> float:
    """Decimal-accurate sum of an iterable of amounts, rounded once at
    the end - avoids accumulating float error across many additions
    (e.g. every line of a large bill)."""
    total = Decimal("0")
    for v in values:
        if v is None or v == "":
            continue
        try:
            total += Decimal(str(v))
        except Exception:
            continue
    return float(total.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def split_gst_inclusive(gross, gst_rate_percent):
    """
    Given a GST-inclusive amount (MRP-based line/bill total, since Indian
    MRP is always tax-inclusive by law) and the GST rate that actually
    applies to it (5, 12, 18, ...), returns (taxable_value, gst_amount),
    both rounded to the paisa. CGST/SGST are gst_amount / 2 each
    (intra-state sale, the only case this app currently handles).

    Replaces the previous blanket `subtotal / 1.12` used in
    gst_reports.py, which silently assumed every medicine in every bill
    was taxed at a flat 12% - wrong for the equally common 5% and 18%
    slabs pharmacies actually deal in.
    """
    try:
        gross_d = Decimal(str(gross or 0))
        rate_d = Decimal(str(gst_rate_percent if gst_rate_percent is not None else 12))
    except Exception:
        return 0.0, 0.0
    if gross_d <= 0:
        return 0.0, 0.0
    if rate_d < 0:
        rate_d = Decimal("0")
    divisor = Decimal("1") + (rate_d / Decimal("100"))
    taxable = (gross_d / divisor).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    gst_amount = (gross_d - taxable).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    return float(taxable), float(gst_amount)


def aggregate_gst_by_bill(item_rows, fallback_rate=12):
    """
    item_rows: iterable of (bill_no, line_total, gst_rate) - one row per
    sales_items line, gst_rate may be None (medicine not matched / no
    rate recorded).

    Returns {bill_no: (taxable_total, gst_total)}, each line split at
    ITS OWN medicine's GST rate and summed per bill - so a single bill
    containing, say, a 5%-rated medicine and an 18%-rated medicine is no
    longer forced through one flat assumed rate for the whole bill.
    """
    per_bill = defaultdict(lambda: [Decimal("0"), Decimal("0")])
    for bill_no, line_total, gst_rate in item_rows:
        rate = gst_rate if gst_rate is not None else fallback_rate
        taxable, gst_amt = split_gst_inclusive(line_total, rate)
        per_bill[bill_no][0] += Decimal(str(taxable))
        per_bill[bill_no][1] += Decimal(str(gst_amt))
    return {
        bill_no: (float(t.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)),
                   float(g.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)))
        for bill_no, (t, g) in per_bill.items()
    }
