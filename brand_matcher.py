import sqlite3
import hashlib
import json

DB_PATH = "pharmacy.db"

def normalize(s):
    return "".join(c for c in (s or "").lower().strip() if c.isalnum())

def row_hash(name, company, pack_size):
    key = f"{normalize(name)}|{normalize(company)}|{normalize(pack_size)}"
    return hashlib.sha256(key.encode()).hexdigest()

def bigrams(s):
    return [s[i:i+2] for i in range(len(s)-1)]

def dice_score(a, b):
    A, B = bigrams(normalize(a)), bigrams(normalize(b))
    if not A or not B:
        return 0
    B_copy = list(B)
    matches = 0
    for bg in A:
        if bg in B_copy:
            matches += 1
            B_copy.remove(bg)
    return (2 * matches) / (len(A) + len(B))

def log_decision(conn, hash_, raw_text, medicine_id, decision, user, score):
    conn.execute("""
        INSERT INTO match_decision_log (raw_input_hash, raw_input_text, candidate_medicine_id, decision, matched_by, confidence_score)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(raw_input_hash, candidate_medicine_id) DO UPDATE SET decision=excluded.decision
    """, (hash_, raw_text, medicine_id, decision, user, score))
    conn.commit()

def import_row(name, company, generic="", pack_size="", user="import-bot"):
    """
    name      -> medicine_master.name  (e.g. "Omee")
    company   -> medicine_master.company (e.g. "Alkem")
    generic   -> medicine_master.generic (e.g. "Omeprazole") - optional but recommended
    pack_size -> medicine_master.pack_size
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    h = row_hash(name, company, pack_size)
    raw_text = f"{name} | {company} | {pack_size}"

    # Tier 2: முன்பே decide ஆனதா check
    memory = conn.execute(
        "SELECT candidate_medicine_id, decision FROM match_decision_log WHERE raw_input_hash=? LIMIT 1",
        (h,)
    ).fetchone()
    if memory and memory["decision"] == "CONFIRMED":
        conn.close()
        return {"status": "AUTO_LINKED", "medicine_id": memory["candidate_medicine_id"]}

    # Tier 1: exact match (name + company + pack_size)
    exact = conn.execute("""
        SELECT id FROM medicine_master
        WHERE lower(name)=lower(?) AND lower(company)=lower(?) AND lower(pack_size)=lower(?)
    """, (name, company, pack_size)).fetchone()
    if exact:
        conn.close()
        return {"status": "AUTO_LINKED", "medicine_id": exact["id"]}

    # Tier 3/4: fuzzy match — company வேற ஆனா score பாதி குறையும் (Omee/Omez guard)
    rejected_ids = {r["candidate_medicine_id"] for r in conn.execute(
        "SELECT candidate_medicine_id FROM match_decision_log WHERE raw_input_hash=? AND decision='REJECTED'", (h,)
    )}

    prefix = normalize(name)[:3]
    pool = conn.execute("""
        SELECT id, name, company, generic FROM medicine_master
        WHERE lower(name) LIKE ? OR lower(name) LIKE ?
    """, (prefix + "%", "%" + prefix + "%")).fetchall()

    candidates = []
    for r in pool:
        if r["id"] in rejected_ids:
            continue
        score = dice_score(r["name"], name)
        same_company = normalize(r["company"]) == normalize(company)
        final_score = score if same_company else score * 0.3   # <-- Omee/Omez guardrail
        if final_score > 0.15:
            candidates.append({
                "medicine_id": r["id"], "name": r["name"],
                "company": r["company"], "generic": r["generic"], "score": round(final_score, 3)
            })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    candidates = candidates[:5]

    if candidates and candidates[0]["score"] > 0.75 and normalize(candidates[0]["company"]) == normalize(company):
        best = candidates[0]
        log_decision(conn, h, raw_text, best["medicine_id"], "CONFIRMED", "SYSTEM", best["score"])
        conn.close()
        return {"status": "AUTO_LINKED", "medicine_id": best["medicine_id"]}

    # candidate இல்ல, அல்லது unsure -> review queue
    raw_row = json.dumps({"name": name, "company": company, "generic": generic, "pack_size": pack_size})
    conn.execute("INSERT INTO import_review_queue (raw_input_hash, raw_row, candidates) VALUES (?, ?, ?)",
                 (h, raw_row, json.dumps(candidates)))
    # medicine_master-ல needs_review flag வெச்சிருக்கீங்க, அதை பயன்படுத்தலாம் (optional, புது row create ஆகும்போது)
    conn.commit()
    conn.close()
    return {"status": "REVIEW_REQUIRED", "candidates": candidates}


def resolve_review(review_id, chosen_medicine_id, user="staff"):
    """
    chosen_medicine_id = ஏற்கனவே இருக்கிற medicine id -> அதுக்கே link ஆகும்
    chosen_medicine_id = None -> இது புது/வேற medicine -> புது row create பண்ணணும் (manual)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM import_review_queue WHERE id=?", (review_id,)).fetchone()
    raw_row = json.loads(row["raw_row"])
    candidates = json.loads(row["candidates"])
    h = row["raw_input_hash"]
    raw_text = f"{raw_row['name']} | {raw_row['company']} | {raw_row['pack_size']}"

    if chosen_medicine_id:
        log_decision(conn, h, raw_text, chosen_medicine_id, "CONFIRMED", user, 1.0)
        for c in candidates:
            if c["medicine_id"] != chosen_medicine_id:
                log_decision(conn, h, raw_text, c["medicine_id"], "REJECTED", user, c["score"])
    else:
        for c in candidates:
            log_decision(conn, h, raw_text, c["medicine_id"], "REJECTED", user, c["score"])
        # இங்க புது medicine_master row insert பண்ணலாம் (உங்க existing insert logic வெச்சு)

    conn.execute("UPDATE import_review_queue SET status='RESOLVED' WHERE id=?", (review_id,))
    conn.commit()
    conn.close()