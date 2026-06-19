import json
from datetime import datetime
from pathlib import Path

from ap_database import master_repository
from ap_database.engines import get_master_engine
from ingestion.clients.sap_client import SAPClient
from ingestion.clients.kefron_client import KefronClient
from ingestion.ap_agent_trigger import trigger_ap_agent_process_new

# -----------------------------------
# PATHS
# -----------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

STATE_FILE = PROJECT_ROOT / "state" / "last_run.json"

STATE_FILE.parent.mkdir(
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
    """Legacy compatibility wrapper for callers managing a transaction.

    New code should call master_repository functions directly. Existing callers
    still use ``with get_conn() as conn`` followed by ``conn.commit()``; a
    SQLAlchemy Connection preserves that contract for SQLite and PostgreSQL.
    """
    return get_master_engine().connect()

# -----------------------------------
# INIT DATABASE
# -----------------------------------

def init_db():
    return master_repository.init_master_schema_if_needed()
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

        return last_run

    except Exception:

        return None


def update_last_run_time(new_time):

    with open(
        STATE_FILE,
        "w"
    ) as f:

        json.dump(

            {
                "last_run_time":
                    new_time
            },

            f,

            indent=4
        )

    print(
        f"Updated watermark -> "
        f"{new_time}"
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

                row_dt = datetime.fromisoformat(ts)

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
    return master_repository.upsert_invoice(row, connection=conn)

def upsert_posted_invoice(
    conn,
    row,
    sap_document_number=None,
    posting_status="POSTED",
    posting_message=None,
    source_system="AP_AGENT",
):
    payload = dict(row)
    payload.update(
        sap_document_number=sap_document_number,
        posting_status=posting_status,
        posting_message=posting_message,
        source_system=source_system,
    )
    return master_repository.upsert_posted_invoice(
        payload,
        connection=conn,
    )
def upsert_po(conn, row):
    return master_repository.upsert_po(row, connection=conn)


def upsert_gr(conn, row):
    return master_repository.upsert_grn(row, connection=conn)

# -----------------------------------
# MAIN INGESTION FLOW
# -----------------------------------

def run_ingestion():

    init_db()

    last_run_time = get_last_run_time()

    print(
        f"\nLast Watermark: {last_run_time}"
    )

    ap_agent_trigger_result = None

    try:

        # -----------------------------------
        # IMPORTANT:
        # We no longer ingest invoices from API.
        # invoice_master should contain uploaded invoices only.
        # Structured API sync now pulls only PO and GRN data.
        # -----------------------------------

        po_data = fetch_po_data(
            last_run_time
        )

        gr_data = fetch_gr_data(
            last_run_time
        )

        po_rows = po_data.get(
            "data",
            []
        )

        gr_rows = gr_data.get(
            "data",
            []
        )

        with get_conn() as conn:

            for row in po_rows:
                upsert_po(
                    conn,
                    row
                )

            for row in gr_rows:
                upsert_gr(
                    conn,
                    row
                )

            conn.commit()

        print(
            "API invoice ingestion skipped. "
            "invoice_master is reserved for uploaded invoices only."
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
            po_data,
            gr_data
        )

        if latest_time:

            update_last_run_time(
                latest_time
            )

        else:

            print(
                "No new PO/GRN records. "
                "Watermark unchanged."
            )

        # -----------------------------------
        # Do not trigger AP Agent from API sync.
        # AP Agent will be triggered by uploaded invoices.
        # -----------------------------------

        print(
            "AP Agent trigger skipped during API sync. "
            "AP Agent is triggered by invoice upload flow."
        )

        print(
            "\nINGESTION COMPLETED SUCCESSFULLY"
        )

        return {

            "invoice_count": 0,

            "po_count": len(po_rows),

            "grn_count": len(gr_rows),

            "ap_agent_trigger": ap_agent_trigger_result,

            "status": "success",

            "message": (
                "Structured sync completed. "
                "Only PO and GRN data were synced from API. "
                "Invoices are upload-only."
            )
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

# -----------------------------------
# DEMO / MAINTENANCE HELPERS
# -----------------------------------

def delete_invoice(invoice_number):
    return master_repository.delete_invoice(invoice_number)


def delete_posted_invoice(invoice_number):
    return master_repository.delete_posted_invoice(invoice_number)


def delete_po(po_number):
    return master_repository.delete_po(po_number)


def delete_grn(grn_number):
    return master_repository.delete_grn(grn_number)


def clear_invoice_table():
    return master_repository.clear_invoice_table()


def clear_po_table():
    return master_repository.clear_po_table()


def clear_grn_table():
    return master_repository.clear_grn_table()

def clear_posted_invoice_table():
    return master_repository.clear_posted_invoice_table()
def keep_latest_rows(
    table_name,
    keep_count=10
):
    return master_repository.keep_latest_rows(table_name, keep_count)


def reset_demo_environment():
    init_db()
    # reset watermark
    with open(
        STATE_FILE,
        "w"
    ) as f:

        json.dump(
            {
                "last_run_time": None
            },
            f,
            indent=4
        )

    return master_repository.reset_demo_environment()
