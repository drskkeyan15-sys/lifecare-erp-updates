import sqlite3
from app_paths import DB_NAME
from generic_mapping import ensure_tables, ensure_composition_master
import ddi_checker
import auth_utils

def connect():
    return sqlite3.connect(DB_NAME)

def create_database():
    conn = connect()
    cur = conn.cursor()

    # WAL MODE - future-proofing for more than one billing counter/PC
    # ever sharing this same pharmacy.db (over a network share, say).
    # SQLite's default rollback-journal mode serializes ALL writers and
    # can throw "database is locked" under concurrent access; WAL lets
    # readers and a writer proceed together instead. Harmless as a
    # single-PC setup too - this is a one-time, persistent, per-file
    # setting (survives across app restarts once written), so it's
    # cheap to just re-issue on every startup rather than needing a
    # separate migration step.
    #
    # IMPORTANT: WAL mode means the most recent writes can sit in a
    # separate pharmacy.db-wal file rather than pharmacy.db itself until
    # a checkpoint happens - backup_manager.py's backup_now()/
    # restore_backup() force a checkpoint before copying the file for
    # exactly this reason (a raw file copy without that could silently
    # miss the latest transactions). Don't remove this PRAGMA without
    # also reverting that checkpoint logic - they're a matched pair.
    cur.execute("PRAGMA journal_mode=WAL")
    # WAL's recommended companion - still durable against an app crash,
    # only trades away safety against a full OS-level power loss at the
    # exact instant of a write, which NORMAL's default (FULL) protects
    # against at a real, measurable write-speed cost this app doesn't
    # need to pay on every single billing/purchase save.
    cur.execute("PRAGMA synchronous=NORMAL")
    # A connection that hits a lock now waits up to 5s for it to clear
    # instead of failing immediately - smooths over the brief overlaps
    # WAL mode already mostly avoids, rather than surfacing a raw
    # "database is locked" error to the pharmacist.
    cur.execute("PRAGMA busy_timeout=5000")

    # டேட்டாபேஸ் டேபிள்களை உருவாக்குதல்
    ensure_tables()
    ensure_composition_master()
    # DDI Safety Checker framework (Aug 2026) - see ddi_checker.py's own
    # module docstring for the "reference only, not comprehensive"
    # caveat. Created here too (not just lazily inside
    # check_cart_interactions()) so a fresh install has the table ready
    # before Billing is ever opened, matching how the two calls above
    # already eagerly create their own tables at startup.
    ddi_checker.ensure_table()

    # 1. USERS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT
    )
    """)

    # 2. SETTINGS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        id INTEGER PRIMARY KEY,
        shop_name TEXT,
        address TEXT,
        phone TEXT,
        city TEXT,
        email TEXT,
        gstin TEXT,
        dl20 TEXT,
        dl21 TEXT
    )
    """)

    # 3. MEDICINE MASTER TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS medicine_master(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        generic TEXT,
        company TEXT,
        category TEXT,
        hsn TEXT,
        gst REAL,
        batch TEXT,
        expiry TEXT,
        purchase REAL,
        mrp REAL,
        sale REAL,
        stock INTEGER,
        pack_size TEXT DEFAULT '1',
        free_qty INTEGER DEFAULT 0,
        barcode TEXT,
        rack TEXT,
        needs_review INTEGER DEFAULT 0
    )""")

    # மைக்ரேஷன் வரிகள்
    try:
        cur.execute("ALTER TABLE medicine_master ADD COLUMN needs_review INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # மைக்ரேஷன்: Re-order Level - Smart Alerts-ன் Low Stock tab இந்த
    # per-medicine மதிப்பை (0 = "set பண்ணல") படிச்சு, stock இதுக்கு கீழ
    # போனா low-stock-ஆ flag பண்ணும். 0/NULL-ஆ இருக்கும் medicines-க்கு
    # Smart Alerts தன்னோட fixed default threshold (10) பயன்படுத்தும்.
    try:
        cur.execute("ALTER TABLE medicine_master ADD COLUMN reorder_level INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # BUG FIX: medicine_master.py's save()/update() have referenced a
    # composition_id column (linking to composition_master.composition_id,
    # via _get_or_create_composition_id()) since this session's earlier
    # Composition Master work, but the migration to actually add this
    # column was never written here - every Save/Update was failing with
    # "no such column: composition_id" until now.
    try:
        cur.execute("ALTER TABLE medicine_master ADD COLUMN composition_id INTEGER")
    except sqlite3.OperationalError:
        pass

    # மைக்ரேஷன்: Dosage Form (Tablet/Capsule/Syrup/Injection/Ointment/...)
    # - Purchase/Medicine Master-ல் Tablet & Capsule-ஐ Ointment/Injection/
    # Syrup-லேர்ந்து தனியா பிரிச்சு பாக்கணும்-ன்னு கேட்கப்பட்ட feature.
    # NULL-ஆ இருக்கும் பழைய rows-க்கு UI-ல் "Not Set" ஆ காட்டப்படும்.
    try:
        cur.execute("ALTER TABLE medicine_master ADD COLUMN dosage_form TEXT")
    except sqlite3.OperationalError:
        pass

    # மைக்ரேஷன்: Cold-Chain / Refrigerator flag - insulin, vaccines, some
    # biologics need refrigerated storage. This is a per-medicine (brand +
    # batch) flag, not a composition-level one like Schedule X below,
    # because it's about physical storage location the pharmacist controls,
    # not a fixed drug-class property. Defaults to 0 (not refrigerated) -
    # unlike habit_forming/schedule_x this has no ambiguous "not checked
    # yet" state worth preserving; a medicine simply is or isn't in the
    # fridge right now.
    try:
        cur.execute("ALTER TABLE medicine_master ADD COLUMN needs_refrigeration INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # PERFORMANCE: medicine_master had zero indexes anywhere in this
    # file - every exact lookup (barcode scan in api_server.py's
    # lookup_barcode(), FEFO batch fetch by name in get_fefo_batches(),
    # the name+batch check bulk_import.py/import_invoice.py do before
    # deciding INSERT vs UPDATE) was a full table scan. Fine at a few
    # hundred rows, noticeably slower once a catalog grows into the
    # thousands - exactly the "faster search on large databases" item
    # already tracked in BUG_LOG.md. CREATE INDEX IF NOT EXISTS is
    # idempotent on its own (unlike ALTER TABLE ADD COLUMN above), no
    # try/except needed.
    #
    # NOTE: this does NOT speed up medicine_matcher.py's fuzzy matching
    # (find_medicine_matches() reads every row on purpose to score
    # similarity - no B-tree index can help a fuzzy scan) or
    # api_server.py's search_medicines() autocomplete (its `LIKE
    # '%...%'` has a leading wildcard, which SQLite also can't use an
    # index for). Both would need SQLite FTS5 to actually speed up -
    # a separate, bigger change than this one.
    cur.execute("CREATE INDEX IF NOT EXISTS idx_medicine_master_barcode ON medicine_master(barcode)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_medicine_master_name ON medicine_master(name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_medicine_master_name_batch ON medicine_master(name, batch)")

    # PERFORMANCE (part 2): FTS5 trigram index for api_server.py's
    # search_medicines() autocomplete, which does `name LIKE '%q%'` - a
    # LEADING wildcard, which none of the three plain indexes above can
    # help with (SQLite can only use a B-tree index for a LIKE pattern
    # that doesn't start with a wildcard). The trigram tokenizer indexes
    # every overlapping 3-character shingle of the name, which lets a
    # MATCH query find the same "substring anywhere, case-insensitive"
    # results as the old LIKE '%q%' - verified: "ome" matches "OMEE" and
    # "OMEZ-D", "cet" matches "PARACETAMOL", hyphens/spaces work fine
    # when the query is wrapped as one quoted phrase - but via the
    # trigram index instead of scanning every row.
    #
    # This is an "external content" FTS5 table - it stores no data of
    # its own, just an index pointing back at medicine_master's own
    # name column (content_rowid='id' ties each FTS row to
    # medicine_master.id, which is a true INTEGER PRIMARY KEY rowid
    # alias). That means it does NOT auto-update itself on
    # INSERT/UPDATE/DELETE - the three triggers below are the standard
    # SQLite-documented way to keep it in sync, and are the only place
    # that needs to change if a future migration adds another searchable
    # column here (e.g. generic name).
    #
    # Wrapped in try/except: FTS5 is bundled in the official Windows
    # python.org installers this app is built with, but isn't
    # guaranteed on every SQLite build. api_server.py's
    # search_medicines() has its own fallback to the original LIKE
    # query if this table doesn't exist, so a machine without FTS5
    # simply doesn't get the speed-up rather than failing to start.
    try:
        fts_already_existed = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='medicine_master_fts'"
        ).fetchone() is not None

        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS medicine_master_fts USING fts5(
                name, content='medicine_master', content_rowid='id', tokenize='trigram'
            )
        """)

        if not fts_already_existed:
            # First time this table exists on this pharmacy.db - backfill
            # from whatever's already in medicine_master. The triggers
            # below only cover rows added/changed AFTER this point, so
            # this one-time backfill is what makes existing medicines
            # searchable too, not just newly-added ones.
            cur.execute("INSERT INTO medicine_master_fts(rowid, name) SELECT id, name FROM medicine_master")

        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS medicine_master_fts_ai AFTER INSERT ON medicine_master BEGIN
                INSERT INTO medicine_master_fts(rowid, name) VALUES (new.id, new.name);
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS medicine_master_fts_ad AFTER DELETE ON medicine_master BEGIN
                INSERT INTO medicine_master_fts(medicine_master_fts, rowid, name) VALUES('delete', old.id, old.name);
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS medicine_master_fts_au AFTER UPDATE ON medicine_master BEGIN
                INSERT INTO medicine_master_fts(medicine_master_fts, rowid, name) VALUES('delete', old.id, old.name);
                INSERT INTO medicine_master_fts(rowid, name) VALUES (new.id, new.name);
            END
        """)
    except sqlite3.OperationalError:
        pass

    # BRAND_MASTER TABLE - Brand Name -> Generic Composition/Manufacturer/
    # Category/Dosage Form reference catalog (separate from medicine_master,
    # which is your actual STOCKED inventory). Purchase Entry looks this up
    # when you type a brand name that isn't in medicine_master yet, and
    # auto-fills Generic/Company/Category/Dosage Form instead of leaving
    # them blank. Seeded from real brand data you provided (see
    # brand_seed_data.py) - never auto-generated, since wrong brand/generic
    # mappings in a pharmacy system are a real patient-safety risk.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS brand_master(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brand_name TEXT UNIQUE,
        generic_text TEXT,
        dosage_form TEXT,
        manufacturer TEXT,
        category TEXT
    )""")

    # 4. PURCHASE TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_no TEXT,
        bill_date TEXT,
        supplier TEXT,
        medicine TEXT,
        batch TEXT,
        expiry TEXT,
        purchase REAL,
        mrp REAL,
        sale REAL,
        gst REAL,
        pack_size TEXT DEFAULT '1',
        qty INTEGER,
        free_qty INTEGER DEFAULT 0,
        total REAL
    )""")

    # மைக்ரேஷன்: Distributor Ledger-ல் ஒவ்வொரு invoice-உம் Due/Overdue-ஆ
    # காட்ட, ஒவ்வொரு purchase-ன் due date சேமிக்க வேண்டும் (bill_date +
    # supplier-ன் credit_period_days). purchase.py-ன் save_purchase()
    # இதை பில் சேமிக்கும்போதே populate பண்ணும் - பழைய rows-ல் இது NULL-ஆகவே
    # இருக்கும் (status "Unknown" ஆக காட்டப்படும், due date கண்டுபிடிக்க முடியாது).
    try:
        cur.execute("ALTER TABLE purchase ADD COLUMN due_date TEXT")
    except sqlite3.OperationalError:
        pass

    # மைக்ரேஷன்: HSN + Supplier's Invoice No/Date - Purchase Entry's
    # BharatERP-style CSV/PDF export (Aug 2026) needs these per-invoice.
    # HSN is snapshotted from medicine_master.hsn AT THE TIME OF PURCHASE
    # (not looked up fresh on every reprint) - so if Medicine Master's
    # HSN is corrected later, old invoices still show what was actually
    # printed/shared with the supplier at the time, not a silently
    # rewritten history. gst already existed as a column but was never
    # populated by save_purchase() until now (see purchase.py) - it's
    # not re-added here. Old rows before this migration stay NULL/blank
    # on export - expected, same as due_date above.
    for col_def in ("hsn TEXT", "supplier_invoice_no TEXT", "supplier_invoice_date TEXT"):
        try:
            cur.execute(f"ALTER TABLE purchase ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass

    # மைக்ரேஷன்: Purchase Entry's CSV/PDF Export Settings (Aug 2026) -
    # which columns to include/hide and their custom header labels,
    # stored as a JSON string so the pharmacist's choice (e.g. "hide
    # Composition", "rename GST% to Tax%") persists across app restarts.
    # Same UPDATE-only pattern as dashboard.py's dark_mode_enabled - see
    # purchase.py's get_export_column_config()/save_export_column_config().
    try:
        cur.execute("ALTER TABLE settings ADD COLUMN purchase_export_columns TEXT")
    except sqlite3.OperationalError:
        pass

    # 5. SALES TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_no TEXT,
        bill_date TEXT,
        customer TEXT,
        doctor TEXT,
        subtotal REAL,
        total REAL
    )""")

    # மைக்ரேஷன்: Billing counter-ல் Payment Mode + Cash Received/Balance
    # (Change) calculator சேர்க்க - Sri Vari Super Market-ன் billing
    # software reference-ல் இருந்து கேட்ட feature. payment_mode Cash-ஆ
    # இருந்தா received_amt/balance_amt meaningful (change calculator);
    # Card/UPI/Wallet-க்கு billing.py received_amt-ஐ grand total-க்கு
    # சமமா auto-set பண்ணும் (change ஏதும் இல்ல).
    for col_def in (
        "payment_mode TEXT DEFAULT 'Cash'",
        "received_amt REAL",
        "balance_amt REAL",
        # discount was previously only added by reports.py's own
        # migrate_schema(), which only runs once someone opens the
        # Reports screen - on a fresh install where Billing is opened
        # FIRST (the normal case: a new pharmacy's very first sale),
        # save_bill()'s INSERT references sales.discount and would crash
        # with "no such column: discount" before Reports was ever
        # touched. Centralized here so it exists from the very first
        # app startup, like every other core sales column. reports.py's
        # own copy is left in place as a harmless no-op safety net.
        "discount REAL DEFAULT 0",
        # Patient address for the Schedule H1 Register - the user's own
        # suggestion (drug-inspector list should record Doctor, Patient,
        # AND Address). Doctor/Patient(customer) were already captured;
        # this fills the gap. Optional field, same as Doctor - a walk-in
        # customer's address may not always be given.
        "address TEXT",
        # Clinic Ledger auto-bill (Aug 2026): clinic_repository.add_visit()
        # can now auto-generate a Sales invoice for the medicines/injections
        # dispensed in a visit (see that function's docstring), so this
        # bill's stock deduction and GST reporting happen exactly once
        # instead of needing a second manual Billing entry. `source` tags
        # WHERE a sale came from so a future combined-revenue report can
        # tell a Clinic-auto-generated sale apart from a normal counter
        # sale and never double-count the same rupee - a normal Billing
        # screen sale always leaves this at its default 'Counter'.
        "source TEXT DEFAULT 'Counter'",
    ):
        try:
            cur.execute(f"ALTER TABLE sales ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass

    # 6. SALES ITEMS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_no TEXT,
        medicine TEXT,
        batch TEXT,
        qty INTEGER,
        purchase REAL,
        sale REAL,
        total REAL
    )""")

    # மைக்ரேஷன் (Aug 2026): purchase.medicine / sales_items.medicine
    # இதுவரை ஒரு plain TEXT column - medicine_master-ல அந்த பெயர் இருக்கானு
    # எந்த protection-ம் இல்லாம, தப்பா typed name-ஓ, delete ஆன medicine-ஓ
    # silently insert ஆகிடும். PRAGMA foreign_keys=ON (app_paths.py)
    # இருந்தாலும் இந்த இரு table-லும் REFERENCES clause இல்லாததால அது
    # இதுவரை எதையும் protect பண்றதில்ல.
    #
    # ஒரு real "FOREIGN KEY (medicine) REFERENCES medicine_master(name)"
    # இங்க வேண்டுமென்றே போடல - medicine_master.name UNIQUE இல்ல (ஒரே
    # மருந்து batch வாரியா வேற வேற rows-ஆ இருக்கும் - உங்க FIFO batch
    # tracking-க்கு இது தேவை, billing.py/purchase.py எல்லாம் "WHERE
    # name=? AND batch=?" தான் query பண்றது). SQLite ஒரு non-unique
    # column-க்கு FK-ஐ allow பண்ணாது - அதை force பண்ண medicine_master.
    # name-ஐ UNIQUE ஆக்கணும், அது இந்த batch-per-row design-ஐயே
    # உடைச்சிடும். அதனால schema-ஐ touch பண்றதுக்கு பதிலா, TRIGGER வச்சு
    # அதே protection-ஐ கொடுக்கிறோம் - INSERT/UPDATE பண்ணும்போதே medicine
    # name medicine_master-ல இருக்கானு check பண்ணி, இல்லாட்டி reject
    # பண்ணும்.
    #
    # ஏற்கனவே இருக்குற (இந்த migration-க்கு முன்னாடி insert ஆன) rows-ஐ
    # இந்த trigger தொடாது - SQLite triggers புதுசா insert/update ஆகும்
    # rows-க்கு மட்டும்தான் fire ஆகும், பழைய data எதுவும் retroactively
    # சோதிக்கப்படாது/நீக்கப்படாது.
    #
    # purchase.py-ன் save_purchase() (medicine ஏற்கனவே offer_create_
    # medicine()-ல் create ஆயிடும் add_item()-க்கு முன்னாடியே) மற்றும்
    # billing.py-ன் save_bill() (billTable-ல் இருக்குற medicine எல்லாம்
    # ஏற்கனவே medicine_master-ல் இருந்தே தேர்ந்தெடுக்கப்பட்டது) - இரண்டு
    # real insert path-ம் இப்போவே எப்போதும் ஒரு existing medicine name-
    # ஐத்தான் insert பண்ணுது, அதனால இந்த trigger அவற்றை break பண்றதில்ல்
    # (headless-ஆ verify பண்ணியாச்சு - ஒரு bogus name insert பண்ண
    # முயற்சிச்சா trigger block பண்ணுது, real save flow தொடர்ந்து
    # வேலை செய்யுது).
    for trigger_sql in (
        """
        CREATE TRIGGER IF NOT EXISTS trg_purchase_medicine_exists_ins
        BEFORE INSERT ON purchase
        FOR EACH ROW
        WHEN NOT EXISTS (SELECT 1 FROM medicine_master WHERE name = NEW.medicine)
        BEGIN
            SELECT RAISE(ABORT, 'purchase.medicine: no matching medicine_master.name');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_purchase_medicine_exists_upd
        BEFORE UPDATE OF medicine ON purchase
        FOR EACH ROW
        WHEN NOT EXISTS (SELECT 1 FROM medicine_master WHERE name = NEW.medicine)
        BEGIN
            SELECT RAISE(ABORT, 'purchase.medicine: no matching medicine_master.name');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_sales_items_medicine_exists_ins
        BEFORE INSERT ON sales_items
        FOR EACH ROW
        WHEN NOT EXISTS (SELECT 1 FROM medicine_master WHERE name = NEW.medicine)
        BEGIN
            SELECT RAISE(ABORT, 'sales_items.medicine: no matching medicine_master.name');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_sales_items_medicine_exists_upd
        BEFORE UPDATE OF medicine ON sales_items
        FOR EACH ROW
        WHEN NOT EXISTS (SELECT 1 FROM medicine_master WHERE name = NEW.medicine)
        BEGIN
            SELECT RAISE(ABORT, 'sales_items.medicine: no matching medicine_master.name');
        END
        """,
    ):
        cur.execute(trigger_sql)

    # 7. SALES RETURN TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales_return(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_date TEXT,
        bill_no TEXT,
        medicine TEXT,
        batch TEXT,
        qty INTEGER,
        price REAL,
        total REAL,
        customer TEXT
    )""")

    # 8. PURCHASE RETURN TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase_return(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        return_date TEXT,
        bill_no TEXT,
        supplier TEXT,
        medicine TEXT,
        batch TEXT,
        qty INTEGER,
        purchase REAL,
        total REAL
    )""")

    # 9. SUPPLIER TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS supplier(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        contact TEXT,
        mobile TEXT,
        gstin TEXT,
        dlno TEXT,
        address TEXT,
        city TEXT,
        email TEXT
    )""")

    # மைக்ரேஷன்: Supplier-க்கு Credit Period (நாட்கள்) - Purchase Entry
    # இதை படித்து ஒவ்வொரு invoice-க்கும் due_date கணக்கிடும். Default 0 =
    # Cash/immediate (பழைய suppliers-க்கு இதுவே பொருந்தும் - explicit-ஆ
    # credit period set பண்ணாத வரை "இன்றே due").
    try:
        cur.execute("ALTER TABLE supplier ADD COLUMN credit_period_days INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # SEED DEFAULT SUPPLIERS - the pharmacy's regular weekly suppliers
    # (Tue: Srinivasa Agency, Thu: UP2Date Drugs & Surgicals, Sat:
    # Dhanalakshmi Medical Agencies + Ramesh Distributors - the last two
    # print the exact same invoice layout under different names, per the
    # pharmacist's own description) pre-installed here so Supplier Master
    # already has them instead of typing the same 4 names in by hand
    # every time the DB is reset or reinstalled elsewhere.
    #
    # GSTIN filled in only where confirmed during Bulk Import's supplier-
    # template calibration (UP2Date, Dhanalakshmi) - Srinivasa and Ramesh
    # are seeded name-only; the pharmacist fills in GSTIN/mobile/address
    # later via Supplier Master's own Update button, same "fill details
    # in later" pattern Medicine Master already uses for auto-created
    # medicines (see medicine_master.py's "Details Pending" banner).
    #
    # Matched by NAME, not a blanket "table is empty" check, so this is
    # idempotent and safe to run on EVERY startup (like the password-hash
    # migration below): it only inserts whichever of the four are still
    # missing, never duplicates one the pharmacist already added by hand,
    # and doesn't care whether other, unrelated suppliers exist already.
    _default_suppliers = [
        # GSTIN confirmed against a clean (non-blurry) Srinivasa Agency
        # invoice scan - matches the GSTIN that kept OCR-misreading as
        # "33ADEPLAGSILIZ6" (digits read as letters) during the Bulk
        # Import supplier-template calibration work.
        ("Srinivasa Agency", "33ADEPL4991L1Z6"),
        ("UP2Date Drugs & Surgicals", "33AYHPM7335D1ZK"),
        ("Dhanalakshmi Medical Agencies", "33AECPS6254C1ZQ"),
        ("Ramesh Distributors", ""),
    ]
    for _sup_name, _sup_gstin in _default_suppliers:
        cur.execute("SELECT 1 FROM supplier WHERE lower(name)=lower(?)", (_sup_name,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO supplier (name, gstin, credit_period_days) VALUES (?, ?, 0)",
                (_sup_name, _sup_gstin)
            )

    # 10. CUSTOMERS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT,
        phone TEXT,
        address TEXT,
        doctor TEXT,
        gstin TEXT
    )""")

    # CUSTOMER_PAYMENTS TABLE - same fix, applied proactively this time:
    # this used to be created only lazily inside customer_ledger.py's own
    # load_customer_ledger()/record_payment() (only runs once that screen
    # is opened). billing.py's new Credit Limit check (see save_bill())
    # queries it directly regardless of whether Customer Ledger has been
    # opened this session - exactly the same class of bug the
    # supplier_payments fix below addresses, so it's centralized here
    # from the start instead of waiting to hit it in testing again.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS customer_payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer TEXT,
        amount REAL,
        pay_date TEXT
    )""")

    # SUPPLIER_PAYMENTS TABLE - BUG FIX: this table used to be created only
    # lazily, inside supplier_ledger.py's own load_ledger() (only runs once
    # that screen is actually opened). daybook.py queries it directly on
    # startup regardless of whether Supplier Ledger has ever been opened
    # this session, so it failed with "no such table: supplier_payments"
    # for anyone who opened Daybook first. Creating it here guarantees it
    # always exists, same as every other table.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS supplier_payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier TEXT,
        amount REAL,
        pay_date TEXT,
        payment_mode TEXT DEFAULT 'Cash'
    )""")
    try:
        cur.execute("ALTER TABLE supplier_payments ADD COLUMN payment_mode TEXT DEFAULT 'Cash'")
    except sqlite3.OperationalError:
        pass

    # 11. EXPENSES TABLE - Daybook screen's cash-out entries (rent,
    # electricity, staff, misc.) that aren't purchases/supplier payments.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        expense_date TEXT,
        category TEXT,
        description TEXT,
        amount REAL,
        payment_mode TEXT DEFAULT 'Cash'
    )""")

    # 12. DAYBOOK TABLE - one row per day, storing the Opening Balance the
    # pharmacist entered and the Closing Balance daybook.py computed/saved.
    # Tomorrow's Opening Balance auto-suggests from today's Closing Balance
    # (daybook.py's load_day() does that lookup) - stored here rather than
    # recomputed each time so a day's closing figure stays fixed once
    # saved, even if later corrections change that day's sales/expenses.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS daybook(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT UNIQUE,
        opening_balance REAL DEFAULT 0,
        closing_balance REAL DEFAULT 0,
        notes TEXT
    )""")

    # 13. STOCK ADJUSTMENTS TABLE - damage/theft/breakage/physical-count
    # corrections that aren't a Purchase Return, Sales Return, or Expiry
    # Return (those already have their own tables/workflows) - a plain
    # "stock went up or down and here's why" log, signed so a single
    # qty_change column covers both additions and removals.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS stock_adjustments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        adj_date TEXT,
        medicine TEXT,
        batch TEXT,
        qty_change INTEGER,
        reason TEXT,
        note TEXT,
        adjusted_by TEXT
    )""")

    # 14. AUDIT LOG TABLE - who changed/deleted which record and when,
    # across screens that matter for accountability (Medicine Master,
    # Purchase, Billing, Stock Adjustment, ...). Deliberately generic
    # (screen/action/record description as free text) rather than one
    # column per possible field, since the set of editable screens/
    # fields will keep growing - a rigid schema would need a migration
    # every time a new screen adds logging, a free-text `details` column
    # does not.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        log_time TEXT,
        username TEXT,
        screen TEXT,
        action TEXT,
        details TEXT
    )""")

    # 15. PURCHASE ORDERS TABLE - one row per line item (same flat,
    # grouped-by-a-shared-number pattern as the `purchase` table itself,
    # grouped by po_no instead of bill_no). Deliberately separate from
    # `purchase` - a PO is a request sent TO a supplier, not yet a real
    # received invoice; reconciling a PO against the eventual Purchase
    # Entry that fulfils it is a manual status change (Draft -> Sent ->
    # Received) here, not an automatic link, since suppliers routinely
    # partial-ship or substitute items.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase_orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        po_no TEXT,
        po_date TEXT,
        supplier TEXT,
        medicine TEXT,
        qty INTEGER,
        note TEXT,
        status TEXT DEFAULT 'Draft',
        created_by TEXT
    )""")

    # CUSTOMER_PRESCRIPTIONS TABLE - Patient Prescription Archive (see
    # prescription_archive.py). Free-text reference note per customer
    # visit, not a structured per-medicine record - a real prescription's
    # medicine list varies too much in how it's written to force into
    # rigid columns for no real benefit here. customer_name is plain text
    # like every other customer-linked table in this app (sales.customer,
    # customer_payments.customer) - no FK to customers.id.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS customer_prescriptions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT,
        rx_date TEXT,
        doctor TEXT,
        medicines TEXT,
        notes TEXT,
        created_at TEXT
    )""")

    # OCR_SUPPLIER_TEMPLATES TABLE - Bulk Purchase Import's "Scan Invoice
    # (OCR)" tab has to guess which printed column is which field from
    # shape alone (a date-shaped cell is probably Expiry, etc.), which
    # breaks whenever a supplier's invoice layout doesn't match that
    # generic guess. In practice a pharmacy re-orders from the same
    # handful of suppliers repeatedly, so a one-time, per-supplier
    # calibration - "this supplier always prints columns in THIS exact
    # order" - removes the guessing entirely for known suppliers, while
    # unknown suppliers keep using the generic engine unchanged. GSTIN is
    # the lookup key since it's a fixed 15-character code (small OCR
    # misreads are tolerated via near-match, not exact-match, at lookup
    # time - see ocr_supplier_templates.find_matching_template()).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ocr_supplier_templates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier_name TEXT NOT NULL,
        gstin TEXT NOT NULL UNIQUE,
        column_order TEXT NOT NULL,
        created_at TEXT
    )""")

    # ─── CLINIC LEDGER MODULE (Aug 2026) ──────────────────────────────
    # Internal clinic accounting: patient visits, consultation income,
    # medicine/injection/consumable usage during treatment, and the
    # resulting purchase-cost/MRP-value/gross-profit per visit. This is
    # DELIBERATELY separate from `sales`/`sales_items` (an actual
    # pharmacy counter sale) - a Clinic Visit records what a doctor USED
    # on a patient during treatment, not a bill the patient bought over
    # the counter. Both still draw from the SAME `medicine_master` stock
    # (see clinic_repository.py's allocate_clinic_stock()) - there is no
    # second, independent inventory here, only a second kind of
    # transaction against the one real stock table.

    # CLINIC_PATIENTS - deliberately its own small table, not a reuse of
    # `customers` (that table is billing/GST/credit-limit shaped and has
    # no age/gender - forcing those fields onto every existing customer
    # row would pollute a table shops that never use Clinic Ledger still
    # rely on). linked_customer_id is an OPTIONAL cross-reference, not a
    # copy, for the case where the same person is also a walk-in buyer.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS clinic_patients(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_code TEXT UNIQUE,
        name TEXT NOT NULL,
        age INTEGER,
        gender TEXT,
        phone TEXT,
        address TEXT,
        linked_customer_id INTEGER REFERENCES customers(id),
        created_by TEXT,
        created_at TEXT
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_clinic_patients_phone ON clinic_patients(phone)")

    # CLINIC_VISITS - one row per patient visit (header). Money totals
    # are stored (not just derivable from clinic_visit_items) so daily/
    # monthly/yearly reports never have to re-walk every line item of
    # every visit in a date range - same "store the computed total"
    # choice `sales.total`/`sales.subtotal` already make.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS clinic_visits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        visit_no TEXT UNIQUE,
        patient_id INTEGER NOT NULL REFERENCES clinic_patients(id),
        visit_date TEXT,
        doctor TEXT,
        reason TEXT,
        consultation_amount REAL DEFAULT 0,
        total_purchase_cost REAL DEFAULT 0,
        total_mrp_value REAL DEFAULT 0,
        total_gross_profit REAL DEFAULT 0,
        total_collection REAL DEFAULT 0,
        status TEXT DEFAULT 'Active',
        cancel_reason TEXT,
        cancelled_by TEXT,
        cancelled_at TEXT,
        created_by TEXT,
        created_at TEXT,
        updated_by TEXT,
        updated_at TEXT
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_clinic_visits_date ON clinic_visits(visit_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_clinic_visits_patient ON clinic_visits(patient_id)")

    # Links a visit to the Pharmacy Sales invoice clinic_repository.
    # add_visit()'s auto_generate_bill flow created for it (Aug 2026) -
    # NULL for every visit that didn't use the All-in-One auto-bill.
    # Additive/non-destructive, same try/except pattern as every other
    # migration in this function.
    try:
        cur.execute("ALTER TABLE clinic_visits ADD COLUMN bill_no TEXT")
    except sqlite3.OperationalError:
        pass

    # CLINIC_VISIT_ITEMS - one row per medicine/injection/consumable
    # USED in a visit, one row per batch (a single logical "Paracetamol
    # x10" line can still split into 2 rows if FEFO allocation spans two
    # batches) - the exact same one-row-per-batch shape `sales_items`
    # already uses, on purpose, so reporting code that already knows how
    # to walk that shape needs no new mental model. medicine_id is NULL
    # only for a genuinely ad-hoc, not-stock-tracked consumable (see
    # clinic_repository.add_visit()'s docstring).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS clinic_visit_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        visit_id INTEGER NOT NULL REFERENCES clinic_visits(id),
        item_type TEXT NOT NULL,
        medicine_id INTEGER REFERENCES medicine_master(id),
        item_name TEXT NOT NULL,
        batch TEXT,
        pack_size TEXT,
        qty REAL NOT NULL,
        unit_purchase_cost REAL DEFAULT 0,
        unit_mrp REAL DEFAULT 0,
        purchase_cost_total REAL DEFAULT 0,
        mrp_value_total REAL DEFAULT 0,
        gross_profit REAL DEFAULT 0,
        created_at TEXT
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_clinic_visit_items_visit ON clinic_visit_items(visit_id)")

    # EXPENSES - additive column only. `expenses` already existed with
    # no screen writing to it yet, so this is zero-risk: existing rows
    # (there are none in practice, but any future ones) default to
    # 'Pharmacy', clinic_repository.add_expense() always writes 'Clinic'.
    try:
        cur.execute("ALTER TABLE expenses ADD COLUMN module TEXT DEFAULT 'Pharmacy'")
    except sqlite3.OperationalError:
        pass

    # DEFAULT ADMIN INSERT
    cur.execute("""
    INSERT OR IGNORE INTO users(username, password, role)
    VALUES('admin', 'admin123', 'Admin')
    """)

    # PASSWORD HASHING MIGRATION - one-time, self-healing. Every login
    # used to compare plaintext passwords directly (see auth_utils.py's
    # module docstring for why that's a real problem, not a style nit).
    # Runs on every startup but is a cheap no-op once every row is
    # already hashed (auth_utils.is_hashed() skips anything starting
    # with "pbkdf2$"), so this is safe to leave here permanently rather
    # than needing a separate one-off migration script someone has to
    # remember to run. Each user's existing password keeps working
    # exactly as before - only what's stored on disk changes.
    cur.execute("SELECT id, password FROM users")
    for user_id, stored_password in cur.fetchall():
        if not auth_utils.is_hashed(stored_password):
            cur.execute(
                "UPDATE users SET password=? WHERE id=?",
                (auth_utils.hash_password(stored_password or ""), user_id)
            )

    # ─── அனைத்துப் பணிகளும் முடிந்த பிறகு ஒரே ஒரு முறை மட்டும் கமிட் செய்து க்ளோஸ் செய்யவும் ───
    conn.commit()
    conn.close()