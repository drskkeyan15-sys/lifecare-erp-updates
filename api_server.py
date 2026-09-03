from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
from datetime import datetime
import re
from app_paths import DB_NAME
import clinic_repository as clinic_repo

app = FastAPI(title="Life Care Pharmacy - Billing API")

# React (localhost:3000 அல்லது Electron) இருந்து call பண்ண அனுமதி
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # production-ல specific origin மட்டும் வெச்சுக்கோங்க
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


# ─── Medicine Search (autocomplete) ───
def _like_search(conn, q):
    """Original substring search - always correct, just a full table
    scan (a leading '%' wildcard can never use a plain index). Used
    directly for very short queries, and as the fallback if the FTS5
    trigram table isn't available on this machine (see database.py's
    medicine_master_fts setup)."""
    return conn.execute(
        "SELECT DISTINCT name FROM medicine_master WHERE name LIKE ? ORDER BY name LIMIT 20",
        (f"%{q}%",)
    ).fetchall()


@app.get("/api/medicines/search")
def search_medicines(q: str = ""):
    conn = get_db()
    q = q.strip()
    if q:
        if len(q) < 3:
            # The trigram index is built from 3-character shingles, so
            # it structurally cannot match a 1-2 character query - LIKE
            # is the only option here, and is fast enough at this size.
            rows = _like_search(conn, q)
        else:
            try:
                # Quoting as one phrase makes FTS5 match the literal
                # substring (spaces/hyphens included) instead of
                # treating the space as separating multiple search
                # terms - "" doubles any literal quote in the query
                # itself, same idea as escaping a quote in SQL.
                phrase = '"' + q.replace('"', '""') + '"'
                rows = conn.execute("""
                    SELECT DISTINCT mm.name FROM medicine_master mm
                    JOIN medicine_master_fts f ON f.rowid = mm.id
                    WHERE medicine_master_fts MATCH ?
                    ORDER BY mm.name LIMIT 20
                """, (phrase,)).fetchall()
            except sqlite3.OperationalError:
                # medicine_master_fts doesn't exist (FTS5 unavailable on
                # this SQLite build, or pharmacy.db predates this
                # feature and hasn't been through create_database() yet)
                rows = _like_search(conn, q)
    else:
        rows = conn.execute("SELECT DISTINCT name FROM medicine_master ORDER BY name LIMIT 20").fetchall()
    conn.close()
    return [r["name"] for r in rows]


# ─── FEFO Batches for a Medicine ───
@app.get("/api/medicines/{name}/batches")
def get_fefo_batches(name: str):
    conn = get_db()
    rows = conn.execute("""
        SELECT batch, stock, sale, pack_size, expiry
        FROM medicine_master
        WHERE name=? AND stock > 0
    """, (name,)).fetchall()
    conn.close()

    today = datetime.today().replace(day=1)
    batches = []
    for r in rows:
        expiry_dt = None
        is_expired = False
        if r["expiry"]:
            try:
                expiry_dt = datetime.strptime(r["expiry"], "%m/%y").replace(day=1)
                is_expired = expiry_dt < today
            except Exception:
                pass

        import re
        pack_str = str(r["pack_size"] or "1")
        match = re.search(r"(\d+)", pack_str)
        units_per_pack = int(match.group(1)) if match else 1

        pack_price = r["sale"]
        single_price = round(pack_price / units_per_pack, 2) if units_per_pack > 1 else pack_price

        batches.append({
            "batch": r["batch"],
            "stock": r["stock"],
            "price": pack_price,
            "single_price": single_price,
            "units_per_pack": units_per_pack,
            "pack_size": r["pack_size"],
            "expiry": r["expiry"],
            "expired": is_expired,
            "expiry_sort": expiry_dt.isoformat() if expiry_dt else None
        })

    # FEFO sort — expiry இல்லாதவை கடைசியில்
    batches.sort(key=lambda b: (b["expiry_sort"] is None, b["expiry_sort"] or ""))
    return batches


# ─── Barcode Lookup ───
@app.get("/api/medicines/barcode/{code}")
def lookup_barcode(code: str):
    conn = get_db()
    row = conn.execute("SELECT DISTINCT name FROM medicine_master WHERE barcode=?", (code,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Barcode not found")
    return {"name": row["name"]}


# ─── Alternative Brand Suggestion (composition-based) ───
@app.get("/api/medicines/{name}/alternatives")
def get_alternatives(name: str):
    conn = get_db()
    med = conn.execute("SELECT generic FROM medicine_master WHERE name=? LIMIT 1", (name,)).fetchone()
    if not med or not med["generic"]:
        conn.close()
        return []
    rows = conn.execute("""
        SELECT DISTINCT name, company, sale, stock FROM medicine_master
        WHERE generic=? AND name != ? AND stock > 0
    """, (med["generic"], name)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class BillItem(BaseModel):
    medicine: str
    batch: str
    qty: int
    price: float


class BillRequest(BaseModel):
    customer: str
    items: list[BillItem]


# ─── Save Bill (stock deduct + save) ───
@app.post("/api/bills")
def save_bill(bill: BillRequest):
    conn = get_db()
    cur = conn.cursor()
    try:
        subtotal = sum(item.qty * item.price for item in bill.items)
        bill_no = f"BILL-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        bill_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cur.execute(
            "INSERT INTO sales (bill_no, bill_date, customer, subtotal, total) VALUES (?,?,?,?,?)",
            (bill_no, bill_date, bill.customer, subtotal, subtotal)
        )

        for item in bill.items:
            cur.execute(
                "UPDATE medicine_master SET stock = stock - ? WHERE name=? AND batch=?",
                (item.qty, item.medicine, item.batch)
            )

        conn.commit()
        return {"bill_no": bill_no, "subtotal": subtotal, "bill_date": bill_date}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()

# ─── Shop Details (receipt header-க்கு) ───
@app.get("/api/settings")
def get_settings():
    conn = get_db()
    row = conn.execute("SELECT * FROM settings LIMIT 1").fetchone()
    conn.close()
    if not row:
        return {"shop_name": "Life Care Pharmacy", "address": "", "phone": "", "gstin": ""}
    return dict(row)


# ============================================================
# CLINIC LEDGER - Android/mobile endpoints (Aug 2026)
#
# Every route here calls straight into clinic_repository.py - the SAME
# module the desktop clinic_*.py Tkinter screens use - so a visit saved
# from the phone and a visit saved from the shop PC run through
# identical cost/profit/stock-deduction logic. Nothing here re-derives
# purchase cost or MRP from client input; the mobile app sends only
# medicine_id/name + qty, matching CLINIC_LEDGER_ARCHITECTURE.md's rule
# that cost math must never be trusted from the client.
#
# NOTE: this file is excluded from the PyInstaller desktop build
# (pharmacy_erp.spec's excludes=['api_server']) - to actually serve a
# phone, run this separately on the shop PC, e.g.:
#     uvicorn api_server:app --host 0.0.0.0 --port 8000
# reachable from the phone over the same Wi-Fi (or a VPN/tunnel for
# true remote access - a real infra decision, not a code change).
# ============================================================

@app.get("/api/clinic/patients/search")
def clinic_search_patients(q: str = ""):
    rows = clinic_repo.search_patients(q, limit=15)
    return [
        {"id": r[0], "patient_code": r[1], "name": r[2], "age": r[3], "gender": r[4], "phone": r[5], "address": r[6]}
        for r in rows
    ]


class PatientCreate(BaseModel):
    name: str
    age: int | None = None
    gender: str | None = None
    phone: str | None = None
    address: str | None = None


@app.post("/api/clinic/patients")
def clinic_create_patient(patient: PatientCreate):
    try:
        patient_id, code = clinic_repo.create_patient(
            patient.name, patient.age, patient.gender, patient.phone, patient.address,
            created_by="mobile-app"
        )
        return {"id": patient_id, "patient_code": code}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/clinic/patients/{patient_id}/history")
def clinic_patient_history(patient_id: int):
    rows = clinic_repo.patient_history_report(patient_id)
    return [
        {"visit_id": r[0], "visit_no": r[1], "visit_date": r[2], "doctor": r[3],
         "consultation_amount": r[4], "total_collection": r[5], "actual_net_profit": r[6], "status": r[7]}
        for r in rows
    ]


class ClinicVisitItem(BaseModel):
    item_type: str          # "Medicine" | "Injection" | "Consumable"
    name: str
    qty: float
    medicine_id: int | None = None      # None => ad-hoc, not-stock-tracked
    manual_unit_cost: float | None = None
    manual_unit_mrp: float | None = None


class ClinicVisitRequest(BaseModel):
    patient_id: int
    doctor: str = ""
    reason: str = ""
    consultation_amount: float = 0
    items: list[ClinicVisitItem] = []
    # Many small clinics collect one flat/bundled amount per visit (e.g.
    # Rs.200 for 2 injections + 4 tablets) rather than billing strictly
    # at Medicine Master MRP - leave this None to fall back to the old
    # Consultation + itemized MRP total (see clinic_repository.add_visit()).
    total_collected: float | None = None
    # "All-in-One" save (Aug 2026) - when True, the same transaction also
    # auto-creates a Pharmacy Sales invoice for the stock-tracked items
    # in this visit, so the phone app never needs a second manual
    # Billing entry. See clinic_repository.add_visit()'s
    # auto_generate_bill docstring for the full reconciliation logic.
    auto_generate_bill: bool = False


@app.post("/api/clinic/visits")
def clinic_save_visit(visit: ClinicVisitRequest):
    items = [item.dict() for item in visit.items]
    # Patient's name is looked up server-side (not trusted from the
    # client) - it's only used as the auto-bill's `customer` display
    # field, but every other cost/profit number in this app is already
    # server-computed only, so this stays consistent with that rule.
    patient_row = clinic_repo.get_patient(visit.patient_id)
    patient_name = patient_row[2] if patient_row else None
    try:
        visit_id, visit_no, bill_no = clinic_repo.add_visit(
            visit.patient_id, visit.doctor, visit.reason, visit.consultation_amount,
            items, created_by="mobile-app", total_collected=visit.total_collected,
            auto_generate_bill=visit.auto_generate_bill, patient_name=patient_name,
        )
    except clinic_repo.InsufficientStockError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
    header, _ = clinic_repo.get_visit(visit_id)
    return {
        "visit_id": visit_id, "visit_no": visit_no, "bill_no": bill_no,
        "total_collection": header[11],
        "actual_net_profit": header[10],  # Total Collection - Purchase Cost (see compute_profit_breakdown())
    }


class VisitCancelRequest(BaseModel):
    reason: str = ""


@app.post("/api/clinic/visits/{visit_id}/cancel")
def clinic_cancel_visit(visit_id: int, body: VisitCancelRequest):
    try:
        clinic_repo.cancel_visit(visit_id, body.reason, cancelled_by="mobile-app")
        return {"success": True}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/clinic/dashboard/today")
def clinic_dashboard_today():
    today = datetime.now().strftime("%Y-%m-%d")
    return clinic_repo.daily_report(today)


@app.get("/api/clinic/reports/daily")
def clinic_report_daily(date: str):
    return clinic_repo.daily_report(date)


@app.get("/api/clinic/reports/monthly")
def clinic_report_monthly(year: int, month: int):
    return clinic_repo.monthly_report(year, month)