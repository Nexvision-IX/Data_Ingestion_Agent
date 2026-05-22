import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ingestion.clients.sap_client import SAPClient
from ingestion.clients.kefron_client import KefronClient

# -----------------------------------
# PATHS
# -----------------------------------

STATE_FILE = Path("state/last_run.json")

MASTER_DB_PATH = Path(
    "data/master/ap_master.db"
)

STATE_FILE.parent.mkdir(
    parents=True,
    exist_ok=True
)

MASTER_DB_PATH.parent.mkdir(
    parents=True,
    exist_ok=True
)

# -----------------------------------
# CREATE CLIENTS
# -----------------------------------

sap_client = SAPClient()

kefron_client = KefronClient()

# -----------------------------------
# DB CONNECTION
# -----------------------------------

def get_conn():

    conn = sqlite3.connect(
        MASTER_DB_PATH
    )

    conn.execute(
        "PRAGMA foreign_keys = ON;"
    )

    return conn

# -----------------------------------
# INIT DATABASE
# -----------------------------------

def init_db():

    with get_conn() as conn:

        conn.execute("""

            CREATE TABLE IF NOT EXISTS invoice_master (

                invoice_number TEXT PRIMARY KEY,

                po_number TEXT,

                vendor_name TEXT,

                invoice_date TEXT,

                currency TEXT,

                document_subtotal REAL,

                tax_amount REAL,

                vat_percent REAL,

                document_total REAL,

                payment_status TEXT,

                items_json TEXT,

                raw_json TEXT,

                last_modified TEXT,

                updated_at TEXT
            )

        """)

        conn.execute("""

            CREATE TABLE IF NOT EXISTS sap_po_master (

                po_number TEXT PRIMARY KEY,

                vendor_name TEXT,

                po_date TEXT,

                currency TEXT,

                document_subtotal REAL,

                tax_amount REAL,

                vat_percent REAL,

                document_total REAL,

                po_status TEXT,

                items_json TEXT,

                raw_json TEXT,

                last_modified TEXT,

                updated_at TEXT
            )

        """)

        conn.execute("""

            CREATE TABLE IF NOT EXISTS sap_grn_master (

                gr_number TEXT PRIMARY KEY,

                po_number TEXT,

                vendor_name TEXT,

                gr_date TEXT,

                currency TEXT,

                document_subtotal REAL,

                document_total REAL,

                gr_status TEXT,

                items_json TEXT,

                raw_json TEXT,

                last_modified TEXT,

                updated_at TEXT
            )

        """)

# -----------------------------------
# WATERMARK / CHECKPOINT
# -----------------------------------

def get_last_run_time():

    if not STATE_FILE.exists():

        return None

    with open(
        STATE_FILE,
        "r"
    ) as f:

        state = json.load(f)

    last_run = state.get(
        "last_run_time"
    )

    if not last_run:

        return None

    try:

        return (

            datetime
            .fromisoformat(last_run)
            .date()
            .isoformat()

        )

    except Exception:

        return None

def update_last_run_time(new_time):

    date_only = (

        datetime
        .fromisoformat(new_time)
        .date()
        .isoformat()

    )

    with open(
        STATE_FILE,
        "w"
    ) as f:

        json.dump(

            {
                "last_run_time":
                    date_only
            },

            f,

            indent=4
        )

    print(
        f"Updated watermark -> "
        f"{date_only}"
    )

def get_latest_modified_time(*datasets):

    latest_dt = None

    for dataset in datasets:

        for row in dataset.get("data", []):

            ts = row.get(
                "last_modified"
            )

            if not ts:

                continue

            try:

                row_dt = (

                    datetime
                    .fromisoformat(ts)
                    .date()

                )

            except ValueError:

                continue

            if (

                latest_dt is None
                or row_dt > latest_dt

            ):

                latest_dt = row_dt

    return (

        latest_dt.isoformat()
        if latest_dt
        else None

    )

# -----------------------------------
# HELPERS
# -----------------------------------

def build_params(since_date):

    if since_date:

        return {

            "since_date":
                since_date
        }

    return None

# -----------------------------------
# FETCH FUNCTIONS
# -----------------------------------

def fetch_invoice_data(since_date):

    print(
        "\nFetching Invoice Data "
        "from Kefron..."
    )

    data = kefron_client.get(

        "/kefron/invoices",

        params=build_params(
            since_date
        )
    )

    print(
        f"Invoice Records Found: "
        f"{data['count']}"
    )

    return data

def fetch_po_data(since_date):

    print(
        "\nFetching PO Data "
        "from SAP..."
    )

    data = sap_client.get(

        "/sap/po",

        params=build_params(
            since_date
        )
    )

    print(
        f"PO Records Found: "
        f"{data['count']}"
    )

    return data

def fetch_gr_data(since_date):

    print(
        "\nFetching GR Data "
        "from SAP..."
    )

    data = sap_client.get(

        "/sap/gr",

        params=build_params(
            since_date
        )
    )

    print(
        f"GR Records Found: "
        f"{data['count']}"
    )

    return data

# -----------------------------------
# UPSERT FUNCTIONS
# -----------------------------------

def upsert_invoice(conn, row):

    now = datetime.now().isoformat(
        timespec="seconds"
    )

    conn.execute("""

        INSERT INTO invoice_master (

            invoice_number,
            po_number,
            vendor_name,
            invoice_date,
            currency,

            document_subtotal,
            tax_amount,
            vat_percent,
            document_total,

            payment_status,

            items_json,
            raw_json,

            last_modified,
            updated_at

        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(invoice_number) DO UPDATE SET

            po_number=excluded.po_number,
            vendor_name=excluded.vendor_name,
            invoice_date=excluded.invoice_date,
            currency=excluded.currency,

            document_subtotal=excluded.document_subtotal,
            tax_amount=excluded.tax_amount,
            vat_percent=excluded.vat_percent,
            document_total=excluded.document_total,

            payment_status=excluded.payment_status,

            items_json=excluded.items_json,
            raw_json=excluded.raw_json,

            last_modified=excluded.last_modified,
            updated_at=excluded.updated_at

    """, (

        row.get("invoice_number"),
        row.get("po_number"),
        row.get("vendor_name"),
        row.get("invoice_date"),
        row.get("currency"),

        row.get("document_subtotal"),
        row.get("tax_amount"),
        row.get("vat_percent"),
        row.get("document_total"),

        row.get("payment_status"),

        json.dumps(
            row.get("line_items", [])
        ),

        json.dumps(row),

        row.get("last_modified"),

        now
    ))

def upsert_po(conn, row):

    now = datetime.now().isoformat(timespec="seconds")

    conn.execute("""

        INSERT INTO sap_po_master (

            po_number,
            vendor_name,
            po_date,
            currency,

            document_subtotal,
            tax_amount,
            vat_percent,
            document_total,

            po_status,

            items_json,
            raw_json,

            last_modified,
            updated_at

        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(po_number) DO UPDATE SET

            vendor_name=excluded.vendor_name,
            po_date=excluded.po_date,
            currency=excluded.currency,

            document_subtotal=excluded.document_subtotal,
            tax_amount=excluded.tax_amount,
            vat_percent=excluded.vat_percent,
            document_total=excluded.document_total,

            po_status=excluded.po_status,

            items_json=excluded.items_json,
            raw_json=excluded.raw_json,

            last_modified=excluded.last_modified,
            updated_at=excluded.updated_at

    """, (

        row.get("po_number"),
        row.get("vendor_name"),
        row.get("po_date"),
        row.get("currency"),

        row.get("document_subtotal"),
        row.get("tax_amount"),
        row.get("vat_percent"),
        row.get("document_total"),

        row.get("po_status"),

        json.dumps(row.get("line_items", [])),

        json.dumps(row),

        row.get("last_modified"),

        now
    ))

def upsert_gr(conn, row):

    now = datetime.now().isoformat(timespec="seconds")

    conn.execute("""

        INSERT INTO sap_grn_master (

            gr_number,
            po_number,
            vendor_name,
            gr_date,
            currency,

            document_subtotal,
            document_total,

            gr_status,

            items_json,
            raw_json,

            last_modified,
            updated_at

        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(gr_number) DO UPDATE SET

            po_number=excluded.po_number,
            vendor_name=excluded.vendor_name,
            gr_date=excluded.gr_date,
            currency=excluded.currency,

            document_subtotal=excluded.document_subtotal,
            document_total=excluded.document_total,

            gr_status=excluded.gr_status,

            items_json=excluded.items_json,
            raw_json=excluded.raw_json,

            last_modified=excluded.last_modified,
            updated_at=excluded.updated_at

    """, (

        row.get("gr_number"),
        row.get("po_number"),
        row.get("vendor_name"),
        row.get("gr_date"),
        row.get("currency"),

        row.get("document_subtotal"),
        row.get("document_total"),

        row.get("gr_status"),

        json.dumps(row.get("line_items", [])),

        json.dumps(row),

        row.get("last_modified"),

        now
    ))

# -----------------------------------
# MAIN INGESTION FLOW
# -----------------------------------

def run_ingestion():

    init_db()

    last_run_time = get_last_run_time()

    print(f"\nLast Watermark: {last_run_time}")

    try:

        invoice_data = fetch_invoice_data(last_run_time)

        po_data = fetch_po_data(last_run_time)

        gr_data = fetch_gr_data(last_run_time)

        invoice_rows = invoice_data.get("data", [])

        po_rows = po_data.get("data", [])

        gr_rows = gr_data.get("data", [])

        with get_conn() as conn:

            for row in invoice_rows:
                upsert_invoice(conn, row)

            for row in po_rows:
                upsert_po(conn, row)

            for row in gr_rows:
                upsert_gr(conn, row)

            conn.commit()

        print(
            f"Upserted invoice rows: "
            f"{len(invoice_rows)}"
        )

        print(
            f"Upserted PO rows: "
            f"{len(po_rows)}"
        )

        print(
            f"Upserted GR rows: "
            f"{len(gr_rows)}"
        )

        latest_time = get_latest_modified_time(
            invoice_data,
            po_data,
            gr_data
        )

        if latest_time:

            update_last_run_time(
                latest_time
            )

        else:

            print(
                "No new records in any source. "
                "Watermark unchanged."
            )

        print(
            "\nINGESTION COMPLETED SUCCESSFULLY"
        )

        return {

            "invoice_count": len(invoice_rows),

            "po_count": len(po_rows),

            "grn_count": len(gr_rows),

            "status": "success"
        }

    except Exception as e:

        print(
            f"\nINGESTION FAILED: {e}"
        )

        return {

            "status": "failed",

            "error": str(e)
        }

# -----------------------------------
# ENTRY POINT
# -----------------------------------

if __name__ == "__main__":
    run_ingestion()