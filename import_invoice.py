"""
import_invoice.py
Srinivasa Agency Invoice Bulk Purchase Import Script (Safe Stock Update)
"""

import sqlite3
from app_paths import DB_NAME
from pricing_utils import get_unit_price

# 35 Parsed Items from Srinivasa Agency Invoice
invoice_items = [
    {"sno": 1, "name": "OMEE CAP 20'S", "pack": "20 S", "mfr": "ALK", "hsn": "30049034", "batch": "26860178", "exp": "12/27", "mrp": 61.32, "qty": 20, "rate": 28.98, "gst": 5.0},
    {"sno": 2, "name": "LUPIN ORS LIQUID (ORNG)", "pack": "200M", "mfr": "LUP", "hsn": "30049086", "batch": "LPO60425", "exp": "09/27", "mrp": 31.50, "qty": 60, "rate": 15.97, "gst": 5.0},
    {"sno": 3, "name": "REXCOF DX 60ML", "pack": "60ML", "mfr": "CIP", "hsn": "30049093", "batch": "CPL60075", "exp": "01/28", "mrp": 87.34, "qty": 20, "rate": 27.60, "gst": 5.0},
    {"sno": 4, "name": "ALKOF JUNIOR SYP", "pack": "60ML", "mfr": "ALK", "hsn": "30049093", "batch": "25840049", "exp": "10/27", "mrp": 75.00, "qty": 29, "rate": 19.75, "gst": 5.0},
    {"sno": 5, "name": "ALKOF JUNIOR SYP", "pack": "60ML", "mfr": "ALK", "hsn": "30049093", "batch": "25840136", "exp": "11/27", "mrp": 75.00, "qty": 1, "rate": 19.75, "gst": 5.0},
    {"sno": 6, "name": "PREDILAB 10MG TAB", "pack": "10'S", "mfr": "LAB", "hsn": "30043912", "batch": "PPTUT-010", "exp": "06/27", "mrp": 13.66, "qty": 30, "rate": 9.84, "gst": 5.0},
    {"sno": 7, "name": "MONTOVENT-LC", "pack": "10'S", "mfr": "MIC", "hsn": "300490", "batch": "ML26051", "exp": "02/28", "mrp": 158.60, "qty": 20, "rate": 19.42, "gst": 5.0},
    {"sno": 8, "name": "MEDI MASK", "pack": "1S", "mfr": "SYR", "hsn": "62171020", "batch": "MMA/1/2026", "exp": "03/29", "mrp": 10.00, "qty": 100, "rate": 3.22, "gst": 5.0},
    {"sno": 9, "name": "MEDI GRIP CAPSICUM PLASTE", "pack": "10 S", "mfr": "PCP", "hsn": "30051090", "batch": "CP061", "exp": "05/29", "mrp": 26.00, "qty": 30, "rate": 5.52, "gst": 5.0},
    {"sno": 10, "name": "OKACET L TAB", "pack": "10S", "mfr": "CIP", "hsn": "30049099", "batch": "AMQ02AWB", "exp": "12/27", "mrp": 78.22, "qty": 10, "rate": 5.81, "gst": 5.0},
    {"sno": 11, "name": "GRISORID 250 TAB", "pack": "10 S", "mfr": "RID", "hsn": "30041010", "batch": "NBT-251252", "exp": "11/27", "mrp": 90.00, "qty": 6, "rate": 38.53, "gst": 5.0},
    {"sno": 12, "name": "ELDER MOUTH ULCER GEL", "pack": "10GM", "mfr": "ELD", "hsn": "30049099", "batch": "E5L009", "exp": "11/27", "mrp": 56.72, "qty": 5, "rate": 12.86, "gst": 5.0},
    {"sno": 13, "name": "CLOBETA GM 10GM", "pack": "10GM", "mfr": "LAB", "hsn": "30049029", "batch": "PC114", "exp": "08/27", "mrp": 89.10, "qty": 10, "rate": 12.46, "gst": 5.0},
    {"sno": 14, "name": "KTSDERM", "pack": "15G", "mfr": "LAB", "hsn": "30049029", "batch": "KGV-001", "exp": "12/27", "mrp": 118.00, "qty": 5, "rate": 21.60, "gst": 5.0},
    {"sno": 15, "name": "CIPLADINE OINTMENT 10GM", "pack": "10GM", "mfr": "CIP", "hsn": "30049099", "batch": "N0260108", "exp": "02/28", "mrp": 36.22, "qty": 24, "rate": 25.37, "gst": 5.0},
    {"sno": 16, "name": "OMNIGEL CREAM 10GM", "pack": "10GM", "mfr": "CIP", "hsn": "30049066", "batch": "B0921", "exp": "10/27", "mrp": 67.34, "qty": 10, "rate": 34.19, "gst": 5.0},
    {"sno": 17, "name": "PANTOSEC DSR CAPS 15S", "pack": "15S", "mfr": "CIP", "hsn": "30049087", "batch": "G6445031", "exp": "05/27", "mrp": 227.59, "qty": 5, "rate": 70.29, "gst": 5.0},
    {"sno": 18, "name": "ALDIGESIC P(BOX PACK)", "pack": "15'S", "mfr": "ALK", "hsn": "30049069", "batch": "261E0024", "exp": "12/27", "mrp": 119.06, "qty": 30, "rate": 16.10, "gst": 5.0},
    {"sno": 19, "name": "NICIP", "pack": "10", "mfr": "CIP", "hsn": "30049067", "batch": "CP60305", "exp": "04/29", "mrp": 44.80, "qty": 50, "rate": 6.79, "gst": 5.0},
    {"sno": 20, "name": "NEW KETOKEM SHAMPOO", "pack": "110M", "mfr": "ALK", "hsn": "30049029", "batch": "N26027", "exp": "02/28", "mrp": 242.30, "qty": 3, "rate": 97.50, "gst": 5.0},
    {"sno": 21, "name": "CALAMINE LOTION IP", "pack": "100M", "mfr": "PRS", "hsn": "30049099", "batch": "CME361", "exp": "11/28", "mrp": 60.00, "qty": 12, "rate": 17.92, "gst": 5.0},
    {"sno": 22, "name": "METRONIDAZOLE 400MG TAB", "pack": "10", "mfr": "VIK", "hsn": "30049022", "batch": "25539", "exp": "03/28", "mrp": 15.00, "qty": 10, "rate": 7.76, "gst": 5.0},
    {"sno": 23, "name": "NEW ALKOF COFGELS CAP", "pack": "15S", "mfr": "ALK", "hsn": "30049099", "batch": "ALFF001D", "exp": "12/27", "mrp": 89.05, "qty": 20, "rate": 28.82, "gst": 5.0},
    {"sno": 24, "name": "DEFENAC AMP 3ML", "pack": "3ML", "mfr": "LUP", "hsn": "30049029", "batch": "U045052", "exp": "11/27", "mrp": 5.30, "qty": 100, "rate": 3.45, "gst": 5.0},
    {"sno": 25, "name": "RECTOL 170 SUPP", "pack": "5*5", "mfr": "BLI", "hsn": "30049099", "batch": "T1ACH006", "exp": "11/28", "mrp": 41.60, "qty": 5, "rate": 31.95, "gst": 5.0},
    {"sno": 26, "name": "KETOROLAC INJ", "pack": "1 ML", "mfr": "ALK", "hsn": "30049067", "batch": "SGKET26006", "exp": "03/28", "mrp": 40.20, "qty": 50, "rate": 6.45, "gst": 5.0},
    {"sno": 27, "name": "POLYWIN INJ", "pack": "2ML", "mfr": "M &", "hsn": "30049010", "batch": "MA26A10", "exp": "06/27", "mrp": 28.13, "qty": 20, "rate": 2.88, "gst": 5.0},
    {"sno": 28, "name": "PCM INJ", "pack": "2ML", "mfr": "INT", "hsn": "30049099", "batch": "P5J0938", "exp": "04/27", "mrp": 7.36, "qty": 30, "rate": 4.92, "gst": 5.0},
    {"sno": 29, "name": "KENPORE PLUS 1\"", "pack": "1*12", "mfr": "ROM", "hsn": "30050960", "batch": "G26B060040", "exp": "01/30", "mrp": 90.25, "qty": 5, "rate": 29.60, "gst": 5.0},
    {"sno": 30, "name": "DEXALAB INJ", "pack": "30ML", "mfr": "LAB", "hsn": "30043913", "batch": "QDLIL055", "exp": "03/28", "mrp": 29.00, "qty": 10, "rate": 24.50, "gst": 5.0},
    {"sno": 31, "name": "PANTOSEC IV", "pack": "40MG", "mfr": "CIP", "hsn": "30049039", "batch": "CN3056003", "exp": "04/28", "mrp": 53.88, "qty": 25, "rate": 18.92, "gst": 5.0},
    {"sno": 32, "name": "NS 100ML IV", "pack": "100M", "mfr": "ALK", "hsn": "30049099", "batch": "SCI26043AM", "exp": "02/29", "mrp": 18.70, "qty": 30, "rate": 13.80, "gst": 5.0},
    {"sno": 33, "name": "PYRICOOL 100ML IV", "pack": "100M", "mfr": "ALK", "hsn": "30049069", "batch": "26441948", "exp": "10/27", "mrp": 284.05, "qty": 20, "rate": 30.31, "gst": 5.0},
    {"sno": 34, "name": "BD EMERALD 3ML SY", "pack": "3ML", "mfr": "B-D", "hsn": "90183100", "batch": "6052672", "exp": "01/31", "mrp": 10.00, "qty": 300, "rate": 4.15, "gst": 5.0},
    {"sno": 35, "name": "ANALYTICA-PINK CARD", "pack": "1 S", "mfr": "RAN", "hsn": "30049099", "batch": "XFC0033", "exp": "11/27", "mrp": 87.19, "qty": 10, "rate": 18.31, "gst": 5.0}
]

def bulk_import_purchases(items=invoice_items):
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()
    
    imported = 0
    updated = 0
    
    for item in items:
        unit_sale_price = get_unit_price(item["mrp"], item["pack"])
        
        cur.execute("SELECT id, stock FROM medicine_master WHERE name=? AND batch=?", (item["name"], item["batch"]))
        existing = cur.fetchone()
        
        if existing:
            # பழைய ஸ்டாக் மதிப்பை பாதுகாப்பாக எடுத்து புதிய Qty உடன் கூட்டுவது
            current_stock = int(existing[1] if existing[1] is not None else 0)
            new_stock = current_stock + item["qty"]
            
            cur.execute("""
                UPDATE medicine_master 
                SET stock=?, purchase=?, sale=?, expiry=?, pack_size=?, gst=?, company=?, mrp=?
                WHERE id=?
            """, (new_stock, item["rate"], unit_sale_price, item["exp"], item["pack"], item["gst"], item["mfr"], item["mrp"], existing[0]))
            updated += 1
        else:
            cur.execute("""
                INSERT INTO medicine_master (name, company, batch, expiry, purchase, sale, mrp, stock, pack_size, gst, hsn)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (item["name"], item["mfr"], item["batch"], item["exp"], item["rate"], unit_sale_price, item["mrp"], item["qty"], item["pack"], item["gst"], item["hsn"]))
            imported += 1

    con.commit()
    con.close()
    print(f"Import Complete: {imported} new entries added, {updated} existing batches updated.")

if __name__ == "__main__":
    bulk_import_purchases()