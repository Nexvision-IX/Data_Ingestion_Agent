import json
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st
from sqlalchemy import inspect, text as sql_text
from sqlalchemy.engine import make_url
from ap_storage import (
    InvoiceArtifactBundle,
    get_storage_service,
)
from ap_database.agent_monitor_repository import (
    agent_db_available as ap_agent_db_exists,
    load_ap_agent_communications,
    load_ap_agent_events,
    load_ap_agent_invoices,
    load_ap_agent_summary,
    load_ap_agent_validation_results,
)
from ap_database.engines import get_agent_engine, get_master_engine
from ap_database.agent_artifact_models import ArtifactBase
from ap_database.master_repository import (
    get_table_count,
    load_table_data,
    reset_demo_environment as reset_master_tables,
)
from ap_database.settings import is_postgres_url, settings as database_settings
from ap_database.extraction_confidence import (
    canonicalize_extraction_confidence,
)
from ap_storage.settings import load_storage_settings
from ingestion.master_ingestion import (

    delete_invoice,
    delete_posted_invoice,
    delete_po,
    delete_grn,

    clear_invoice_table,
    clear_posted_invoice_table,
    clear_po_table,
    clear_grn_table,

    keep_latest_rows,
    reset_invoice_flow_data,
    STATE_FILE,

    init_db,
    get_conn,
    upsert_invoice,
)

from ingestion.ap_agent_trigger import trigger_ap_agent_process_new
from pipeline_runner import process_invoice_pipeline, sync_structured_sources
from agent_app.app.services.demo_reset_service import (
    reset_agent_invoice_flow,
)
from agent_app.app.services.payment_terms_control import calculate_due_date
from reset_client import (
    MockSAPResetClientError,
    call_mock_api_admin_reset,
)
from reset_contract import ResetMode, mock_sap_base_url
from reset_workflow import run_staged_reset

# -----------------------------------
# DATABASE PATH
# -----------------------------------

from dotenv import load_dotenv

load_dotenv()

SAP_API_PORT = int(os.getenv("SAP_API_PORT", "8001"))
API_BASE_URL = mock_sap_base_url()
SAP_USERNAME = os.getenv(
    "SAP_USERNAME",
    "sap_user"
)

SAP_PASSWORD = os.getenv(
    "SAP_PASSWORD",
    "sap_pass"
)

AGENT_API_PORT = int(os.getenv("AGENT_API_PORT", "8000"))
AP_AGENT_BASE_URL = os.getenv(
    "AGENT_API_BASE_URL",
    os.getenv("AP_AGENT_BASE_URL", f"http://127.0.0.1:{AGENT_API_PORT}"),
).rstrip("/")
EXCEPTION_RESPONSE_ENDPOINT_TEMPLATE = "/api/v1/exceptions/{exception_id}/responses"
CONTROLLED_RECHECK_ENDPOINT_TEMPLATE = "/api/v1/invoices/{invoice_id}/recheck"

# Common ISO 4217 currencies supported by the manual document-entry forms.
CURRENCY_OPTIONS = [
    "INR",
    "USD",
    "EUR",
    "GBP",
    "AED",
    "AUD",
    "CAD",
    "CHF",
    "CNY",
    "HKD",
    "JPY",
    "KRW",
    "MYR",
    "NZD",
    "SAR",
    "SEK",
    "SGD",
    "THB",
    "ZAR",
]
# -----------------------------------
# LOCAL SQLITE FOLDER SAFETY
# -----------------------------------

def ensure_local_sqlite_parent_dirs():
    """Create parent folders for local SQLite database files.

    This prevents the common local error:
    sqlite3.OperationalError: unable to open database file

    Example affected URL: sqlite:///./data/master/ap_master.db
    """
    for database_url in {
        database_settings.database_url,
        database_settings.master_database_url,
    }:
        if not database_url or not database_url.strip().lower().startswith("sqlite"):
            continue

        try:
            parsed_url = make_url(database_url)
            database_path = parsed_url.database
        except Exception:
            continue

        if not database_path or database_path == ":memory":
            continue

        db_file = Path(database_path)
        if not db_file.is_absolute():
            db_file = Path.cwd() / db_file

        db_file.parent.mkdir(parents=True, exist_ok=True)


ensure_local_sqlite_parent_dirs()


def ensure_agent_artifact_schema():
    """Create the invoice_artifacts table if it is missing.

    Streamlit saves upload/OCR artifact metadata before the AP Agent
    finishes processing the invoice. In a fresh local SQLite database, this
    table may not exist yet, so we create it non-destructively at startup.
    This also works against RDS later because create_all only creates missing
    tables and does not delete data.
    """
    try:
        ArtifactBase.metadata.create_all(bind=get_agent_engine())
    except Exception as exc:
        st.warning(
            "Invoice artifact table could not be initialized automatically. "
            "Run: python scripts/init_rds_schema.py. "
            f"Details: {type(exc).__name__}: {exc}"
        )


ensure_agent_artifact_schema()

# -----------------------------------
# INPUT DIRECTORY
# -----------------------------------

INPUT_DIR = Path(
    os.getenv(
        "UNSTRUCTURED_INPUT_DIR",
        "unstructured_ingestion/unstructured_inputs"
    )
)

INPUT_DIR.mkdir(parents=True, exist_ok=True)


# -----------------------------------
# HELPERS
# -----------------------------------

def save_uploaded_file(uploaded_file):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_id = uuid.uuid4().hex
    extension = Path(uploaded_file.name).suffix.lower()
    internal_filename = f"{timestamp}_{upload_id[:8]}{extension}"
    save_path = INPUT_DIR / internal_filename
    uploaded_bytes = uploaded_file.getvalue()

    artifact_bundle = InvoiceArtifactBundle(
        storage=get_storage_service(),
        upload_id=upload_id,
        original_filename=uploaded_file.name,
    )
    artifact_bundle.save_original(
        uploaded_bytes,
        content_type=(
            uploaded_file.type
            or "application/octet-stream"
        ),
    )

    with open(save_path, "wb") as f:
        f.write(uploaded_bytes)

    return save_path, artifact_bundle


def show_master_reset_blocked_message():
    st.error(
        "Destructive master-data operations are disabled for this "
        "environment or database. Set "
        "ALLOW_DESTRUCTIVE_MASTER_RESET=true only when an intentional "
        "maintenance action is required."
    )


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def is_local_demo_runtime():
    return (
        database_settings.app_env.strip().lower()
        not in {"production", "prod", "staging", "stage", "demo", "aws"}
        and not is_postgres_url(database_settings.database_url)
    )


def agent_reset_allowed():
    return env_bool(
        "ALLOW_DESTRUCTIVE_AGENT_RESET",
        default=is_local_demo_runtime(),
    )


def show_agent_reset_blocked_message():
    st.error(
        "AP Agent reset is disabled. Set "
        "ALLOW_DESTRUCTIVE_AGENT_RESET=true only for an intentional "
        "local/demo reset."
    )


def reset_ap_agent_tables():
    """Clear AP Agent monitor tables without dropping the schema."""
    if not agent_reset_allowed():
        raise RuntimeError(
            "ALLOW_DESTRUCTIVE_AGENT_RESET is not enabled."
        )

    engine = get_agent_engine()
    deleted_rows = reset_agent_invoice_flow(engine)

    return {
        "status": "success",
        "deleted_rows": deleted_rows,
    }


def clear_demo_runtime_files():
    """Remove generated demo files while keeping checked-in sample inputs."""
    removed = []
    generated_upload_pattern = re.compile(
        r"^\d{8}_\d{6}_[0-9a-fA-F]{8}"
    )

    def remove_path(path: Path):
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(str(path))

    # Uploaded files: remove only generated timestamp/uuid files, not sample PDFs.
    if INPUT_DIR.exists():
        for child in INPUT_DIR.iterdir():
            if generated_upload_pattern.match(child.name):
                remove_path(child)

    # Generated OCR / AI extraction outputs can be cleared safely.
    generated_dirs = [
        Path("unstructured_ingestion/extracted_text"),
        Path("unstructured_ingestion/extracted_json"),
        Path("unstructured_ingestion/structured_debug"),
    ]
    for directory in generated_dirs:
        if directory.exists():
            for child in directory.iterdir():
                remove_path(child)

    processed_file = Path("unstructured_ingestion/processed_files.json")
    if processed_file.exists():
        processed_file.write_text("{}", encoding="utf-8")

    # Local artifact storage is runtime output. Do not delete S3 contents from UI.
    try:
        storage_settings = load_storage_settings()
        if storage_settings.backend == "local" and storage_settings.local_root.exists():
            for child in storage_settings.local_root.iterdir():
                remove_path(child)
    except Exception:
        # File cleanup should not block database reset.
        pass

    return {
        "status": "success",
        "files_removed": len(removed),
        "paths_removed": removed[:20],
    }


def reset_structured_sync_watermark():
    """Reset structured sync watermark so PO/GRN sync can reload reference data."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"last_run_time": None}, indent=4),
        encoding="utf-8",
    )
    return {"status": "success", "state_file": str(STATE_FILE)}


def preflight_admin_reset(
    reset_mode,
    *,
    clear_files=True,
    correlation_id=None,
):
    """Validate every reset dependency before any destructive stage."""
    mode = ResetMode(reset_mode)
    environment = database_settings.app_env.strip().lower()
    if environment in {"production", "prod", "staging", "stage", "aws"}:
        raise RuntimeError(
            f"Demo reset is prohibited in environment '{environment}'."
        )
    if not database_settings.allow_destructive_master_reset:
        raise RuntimeError(
            "ALLOW_DESTRUCTIVE_MASTER_RESET is not enabled."
        )
    if not agent_reset_allowed():
        raise RuntimeError(
            "ALLOW_DESTRUCTIVE_AGENT_RESET is not enabled."
        )

    agent_engine = get_agent_engine()
    master_engine = get_master_engine()
    with agent_engine.connect() as connection:
        connection.execute(sql_text("SELECT 1"))
    with master_engine.connect() as connection:
        connection.execute(sql_text("SELECT 1"))

    agent_tables = set(inspect(agent_engine).get_table_names())
    master_tables = set(inspect(master_engine).get_table_names())
    required_agent = {"invoices", "po_grn_consumption_ledger"}
    required_master = {
        "invoice_master",
        "sap_posted_invoice_master",
        "sap_po_master",
        "sap_grn_master",
    }
    missing_agent = sorted(required_agent - agent_tables)
    missing_master = sorted(required_master - master_tables)
    if missing_agent or missing_master:
        raise RuntimeError(
            "Reset database preflight failed. Missing tables: "
            + ", ".join(missing_agent + missing_master)
        )

    state_parent = Path(STATE_FILE).parent
    if not state_parent.exists() or not os.access(state_parent, os.W_OK):
        raise RuntimeError(
            f"Reset state directory is not writable: {state_parent}"
        )
    if clear_files:
        for directory in (
            INPUT_DIR,
            Path("unstructured_ingestion/extracted_json"),
            Path("unstructured_ingestion/extracted_text"),
            Path("unstructured_ingestion/structured_debug"),
        ):
            if directory.exists() and not os.access(directory, os.W_OK):
                raise RuntimeError(
                    f"Reset file directory is not writable: {directory}"
                )

    mock_result = call_mock_api_admin_reset(
        mode,
        dry_run=True,
        correlation_id=correlation_id,
        auth=(SAP_USERNAME, SAP_PASSWORD),
    )
    return {
        "environment": environment,
        "agent_reset_available": True,
        "master_database_accessible": True,
        "agent_database_accessible": True,
        "state_files_accessible": True,
        "mock_sap_endpoint": mock_result,
    }


def _file_reset_stage(clear_files, *, reset_watermark=True):
    return {
        "watermark": (
            reset_structured_sync_watermark()
            if reset_watermark
            else {"status": "kept"}
        ),
        "runtime_files": (
            clear_demo_runtime_files()
            if clear_files
            else {"status": "skipped", "files_removed": 0}
        ),
    }


def _run_admin_reset(
    reset_mode,
    *,
    clear_files=True,
    resume_result=None,
):
    mode = ResetMode(reset_mode)
    correlation_id = (
        (resume_result or {}).get("correlation_id")
        or uuid.uuid4().hex
    )
    if mode == ResetMode.INVOICE_FLOW:
        master_operation = reset_invoice_flow_data
    else:
        master_operation = reset_master_tables

    return run_staged_reset(
        reset_mode=mode.value,
        correlation_id=correlation_id,
        resume_result=resume_result,
        preflight=lambda: preflight_admin_reset(
            mode,
            clear_files=clear_files,
            correlation_id=correlation_id,
        ),
        operations=[
            ("MASTER_DATABASE_RESET", master_operation),
            (
                "AP_AGENT_RESET",
                lambda: {
                    "status": "success",
                    "deleted_rows": reset_agent_invoice_flow(
                        get_agent_engine()
                    ),
                },
            ),
            (
                "MOCK_SAP_RESET",
                lambda: call_mock_api_admin_reset(
                    mode,
                    correlation_id=correlation_id,
                    auth=(SAP_USERNAME, SAP_PASSWORD),
                ),
            ),
            (
                "FILE_RESET",
                lambda: _file_reset_stage(clear_files),
            ),
        ],
    )


def reset_invoice_flow_environment(
    clear_files=True,
    resume_result=None,
):
    """
    Client-demo reset that keeps PO and GRN reference data intact.

    Clears only invoice-flow data: source/uploaded invoices, posted invoices,
    AP Agent records, local runtime artifacts, and invoice-related mock API JSON.
    """
    return _run_admin_reset(
        ResetMode.INVOICE_FLOW,
        clear_files=clear_files,
        resume_result=resume_result,
    )


def reset_master_demo_environment(
    clear_files=True,
    resume_result=None,
):
    """Master reset that clears all demo data, including PO and GRN reference data."""
    return _run_admin_reset(
        ResetMode.MASTER,
        clear_files=clear_files,
        resume_result=resume_result,
    )


def _reset_summary_counts(result):
    deleted = {}
    retained = {}
    for stage in result.get("stages", {}).values():
        details = stage.get("details") or {}
        for key, value in (details.get("deleted") or {}).items():
            deleted[key] = deleted.get(key, 0) + int(value or 0)
        for key, value in (details.get("deleted_rows") or {}).items():
            deleted[key] = deleted.get(key, 0) + int(value or 0)
        for key, value in (details.get("retained") or {}).items():
            retained[key] = int(value or 0)
        runtime_files = details.get("runtime_files") or {}
        if runtime_files.get("files_removed") is not None:
            deleted["runtime_files"] = int(
                runtime_files.get("files_removed") or 0
            )
    return deleted, retained


def _render_reset_failure(result):
    deleted, retained = _reset_summary_counts(result)
    st.error(
        f"{result.get('reset_mode', 'Reset')} reset did not complete. "
        f"Failed stage: {result.get('failed_stage') or 'unknown'}."
    )
    st.write(
        {
            "reset_mode": result.get("reset_mode"),
            "completed_stages": result.get("completed_stages", []),
            "failed_stage": result.get("failed_stage"),
            "deleted": deleted,
            "retained": retained,
            "retry_guidance": result.get("retry_guidance"),
        }
    )
    if database_settings.app_env.strip().lower() not in {
        "production",
        "prod",
        "staging",
        "stage",
        "aws",
    }:
        with st.expander("Technical details"):
            st.json(result.get("technical_details") or {})


def _handle_reset_result(result, *, clear_files):
    if result.get("success"):
        deleted, retained = _reset_summary_counts(result)
        ledger_rows = deleted.get("po_grn_consumption_ledger", 0)
        st.session_state.pop("demo_reset_pending", None)
        st.session_state["demo_reset_success"] = (
            f"{result['reset_mode']} reset completed. Removed "
            f"{ledger_rows} PO/GRN consumption ledger row(s)."
        )
        st.session_state["demo_reset_result"] = {
            **result,
            "deleted": deleted,
            "retained": retained,
        }
        st.cache_data.clear()
        st.rerun()
        return

    st.session_state["demo_reset_pending"] = {
        "reset_mode": result.get("reset_mode"),
        "clear_files": clear_files,
        "result": result,
    }
    _render_reset_failure(result)


def _render_reset_retry(reset_mode, *, location):
    pending = st.session_state.get("demo_reset_pending")
    if not pending or pending.get("reset_mode") != reset_mode:
        return
    st.warning(
        "A previous reset stopped after a partial failure. A retry will "
        "execute only unfinished stages."
    )
    _render_reset_failure(pending.get("result") or {})
    if st.button(
        "Retry unfinished reset stages",
        key=f"{location}_{reset_mode}_retry_reset",
        use_container_width=True,
    ):
        clear_files = bool(pending.get("clear_files", True))
        previous = pending.get("result")
        result = (
            reset_master_demo_environment(
                clear_files=clear_files,
                resume_result=previous,
            )
            if reset_mode == ResetMode.MASTER.value
            else reset_invoice_flow_environment(
                clear_files=clear_files,
                resume_result=previous,
            )
        )
        _handle_reset_result(result, clear_files=clear_files)


def render_invoice_flow_reset_panel(location="sidebar"):
    """Reset panel that keeps PO/GRN intact."""
    st.caption(
        "Use this before each client walkthrough. It clears invoice-flow data "
        "but keeps PO and GRN reference data intact."
    )
    _render_reset_retry(
        ResetMode.INVOICE_FLOW.value,
        location=location,
    )
    clear_files = st.checkbox(
        "Also clear uploaded/OCR demo files",
        value=True,
        key=f"{location}_invoice_flow_clear_demo_files",
    )
    confirm_text = st.text_input(
        "Type RESET to enable the button",
        key=f"{location}_invoice_flow_reset_confirm_text",
        placeholder="RESET",
    )
    reset_disabled = confirm_text.strip().upper() != "RESET"

    if st.button(
        "Reset invoice demo flow",
        key=f"{location}_reset_invoice_flow_btn",
        disabled=reset_disabled,
        type="primary",
        use_container_width=True,
    ):
        if not database_settings.allow_destructive_master_reset:
            show_master_reset_blocked_message()
            return
        if not agent_reset_allowed():
            show_agent_reset_blocked_message()
            return

        with st.spinner("Resetting invoice demo flow and mock API invoice records..."):
            result = reset_invoice_flow_environment(
                clear_files=clear_files
            )
            _handle_reset_result(result, clear_files=clear_files)


def render_master_reset_panel(location="admin"):
    """Reset panel that deletes everything, including PO and GRN data."""
    st.caption(
        "Use this only when you want to completely wipe the demo database and "
        "mock API JSON files, including PO and GRN records."
    )
    _render_reset_retry(
        ResetMode.MASTER.value,
        location=location,
    )
    clear_files = st.checkbox(
        "Also clear uploaded/OCR demo files",
        value=True,
        key=f"{location}_master_clear_demo_files",
    )
    confirm_text = st.text_input(
        "Type MASTER RESET to enable the button",
        key=f"{location}_master_reset_confirm_text",
        placeholder="MASTER RESET",
    )
    reset_disabled = confirm_text.strip().upper() != "MASTER RESET"

    if st.button(
        "Master reset everything",
        key=f"{location}_master_reset_btn",
        disabled=reset_disabled,
        type="secondary",
        use_container_width=True,
    ):
        if not database_settings.allow_destructive_master_reset:
            show_master_reset_blocked_message()
            return
        if not agent_reset_allowed():
            show_agent_reset_blocked_message()
            return

        with st.spinner("Running master reset across database, AP Agent, files and mock API..."):
            result = reset_master_demo_environment(
                clear_files=clear_files
            )
            _handle_reset_result(result, clear_files=clear_files)


def get_safe_table_count(table_name):
    try:
        return get_table_count(table_name)
    except Exception:
        return 0


def parse_items_cell(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def load_invoice_options(limit=200):
    try:
        invoice_df = load_table_data("invoice_master", limit=limit)
    except Exception:
        return []

    if invoice_df.empty or "invoice_number" not in invoice_df.columns:
        return []

    return [
        invoice
        for invoice in invoice_df["invoice_number"].dropna().astype(str).tolist()
        if invoice
    ]


def get_invoice_master_row(invoice_number):
    if not invoice_number:
        return None
    try:
        invoice_df = load_table_data("invoice_master", limit=500)
    except Exception:
        return None
    if invoice_df.empty or "invoice_number" not in invoice_df.columns:
        return None
    rows = invoice_df[invoice_df["invoice_number"].astype(str) == str(invoice_number)]
    if rows.empty:
        return None
    return rows.iloc[0].to_dict()


def get_posted_invoice_master_row(invoice_number):
    if not invoice_number:
        return None
    try:
        posted_df = load_table_data("sap_posted_invoice_master", limit=500)
    except Exception:
        return None
    if posted_df.empty or "invoice_number" not in posted_df.columns:
        return None
    rows = posted_df[posted_df["invoice_number"].astype(str) == str(invoice_number)]
    if rows.empty:
        return None
    return rows.iloc[0].to_dict()


def create_demo_reference_data_and_sync():
    """Create one deterministic PO/GRN pair in the mock API, then sync to master."""
    now = datetime.now().isoformat()
    line_items = [
        {
            "line_no": 1,
            "description": "Demo service subscription",
            "qty": 1,
            "unit_price": 1000.0,
            "line_amount": 1000.0,
        }
    ]

    po_payload = {
        "document_type": "po",
        "po_number": "PO-DEMO-1001",
        "vendor_name": "Demo Supplier Pvt Ltd",
        "po_date": datetime.now().date().isoformat(),
        "currency": "INR",
        "document_subtotal": 1000.0,
        "tax_amount": 180.0,
        "vat_percent": 18.0,
        "document_total": 1180.0,
        "amount": 1180.0,
        "po_status": "Open",
        "line_items": line_items,
        "last_modified": now,
    }

    grn_payload = {
        "document_type": "grn",
        "gr_number": "GRN-DEMO-1001",
        "po_number": "PO-DEMO-1001",
        "vendor_name": "Demo Supplier Pvt Ltd",
        "gr_date": datetime.now().date().isoformat(),
        "currency": "INR",
        "document_subtotal": 1000.0,
        "document_total": 1000.0,
        "amount": 1000.0,
        "gr_status": "Received",
        "line_items": line_items,
        "last_modified": now,
    }

    po_response = requests.post(
        f"{API_BASE_URL}/sap/po",
        json=po_payload,
        auth=(SAP_USERNAME, SAP_PASSWORD),
        timeout=60,
    )
    grn_response = requests.post(
        f"{API_BASE_URL}/sap/gr",
        json=grn_payload,
        auth=(SAP_USERNAME, SAP_PASSWORD),
        timeout=60,
    )

    if po_response.status_code >= 400:
        raise RuntimeError(f"Demo PO creation failed: {po_response.text}")
    if grn_response.status_code >= 400:
        raise RuntimeError(f"Demo GRN creation failed: {grn_response.text}")

    reset_structured_sync_watermark()
    sync_result = sync_structured_sources()

    return {
        "po_api": po_response.json(),
        "grn_api": grn_response.json(),
        "sync": sync_result,
    }


def build_demo_invoice_payload(invoice_number):
    """Build a clean demo invoice using the demo PO/GRN reference data."""
    po_row = None
    try:
        po_df = load_table_data("sap_po_master", limit=500)
        if not po_df.empty and "po_number" in po_df.columns:
            demo_rows = po_df[po_df["po_number"].astype(str) == "PO-DEMO-1001"]
            if not demo_rows.empty:
                po_row = demo_rows.iloc[0].to_dict()
            else:
                po_row = po_df.iloc[0].to_dict()
    except Exception:
        po_row = None

    if po_row:
        po_number = po_row.get("po_number") or "PO-DEMO-1001"
        vendor_name = po_row.get("vendor_name") or "Demo Supplier Pvt Ltd"
        currency = po_row.get("currency") or "INR"
        subtotal = float(po_row.get("document_subtotal") or 1000.0)
        tax_amount = float(po_row.get("tax_amount") or 180.0)
        vat_percent = float(po_row.get("vat_percent") or 18.0)
        document_total = float(po_row.get("document_total") or subtotal + tax_amount)
        payment_terms = po_row.get("payment_terms") or "NET 30"
        line_items = parse_items_cell(po_row.get("items_json")) or [
            {
                "line_no": 1,
                "description": "Demo service subscription",
                "qty": 1,
                "unit_price": subtotal,
                "line_amount": subtotal,
            }
        ]
    else:
        po_number = "PO-DEMO-1001"
        vendor_name = "Demo Supplier Pvt Ltd"
        currency = "INR"
        subtotal = 1000.0
        tax_amount = 180.0
        vat_percent = 18.0
        document_total = 1180.0
        payment_terms = "NET 30"
        line_items = [
            {
                "line_no": 1,
                "description": "Demo service subscription",
                "qty": 1,
                "unit_price": 1000.0,
                "line_amount": 1000.0,
            }
        ]

    invoice_date = datetime.now().date()
    due_date = calculate_due_date(invoice_date, payment_terms)
    return {
        "document_type": "invoice",
        "invoice_number": invoice_number,
        "po_number": po_number,
        "vendor_name": vendor_name,
        "invoice_date": invoice_date.isoformat(),
        "due_date": due_date.isoformat() if due_date is not None else None,
        "currency": currency,
        "document_subtotal": subtotal,
        "tax_amount": tax_amount,
        "vat_percent": vat_percent,
        "document_total": document_total,
        "amount": document_total,
        "payment_terms": payment_terms,
        "payment_status": "Pending",
        "line_items": line_items,
        "last_modified": datetime.now().isoformat(),
    }



# =========================================================
# CP-19 DEMO WALKTHROUGH HELPERS
# =========================================================

def api_health(url):
    try:
        response = requests.get(f"{url.rstrip('/')}/health", timeout=3)
        if response.status_code < 400:
            return "Healthy"
        return "Unavailable"
    except Exception:
        return "Unavailable"


def post_agent_api(path, payload=None, params=None):
    url = f"{AP_AGENT_BASE_URL}{path}"
    try:
        response = requests.post(url, json=payload or {}, params=params, timeout=60)
        try:
            data = response.json()
        except ValueError:
            data = {"raw_response": response.text}
        return {
            "ok": response.status_code < 400,
            "status_code": response.status_code,
            "url": url,
            "data": data,
            "error": None if response.status_code < 400 else data,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status_code": None,
            "url": url,
            "data": None,
            "error": str(exc),
        }


def current_row_value(row, candidate_columns, default="—"):
    for column in candidate_columns:
        try:
            value = row.get(column, None)
        except AttributeError:
            value = None
        if value is not None and str(value) not in {"", "nan", "NaT", "None"}:
            return value
    return default


def to_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "pass", "passed"}


def one_based_dataframe(data):
    try:
        import pandas as pd
        if isinstance(data, pd.DataFrame):
            display_df = data.copy()
        else:
            display_df = pd.DataFrame(data)
        display_df.index = range(1, len(display_df) + 1)
        display_df.index.name = "R.no"
        return display_df
    except Exception:
        return data


def display_dataframe(df, columns=None, empty_message="No records found."):
    if df is None or getattr(df, "empty", False):
        st.info(empty_message)
        return
    display_df = df.copy()
    if columns:
        available = [column for column in columns if column in display_df.columns]
        if available:
            display_df = display_df[available]
    st.dataframe(one_based_dataframe(display_df), use_container_width=True)


def render_metric_row(items, columns=4):
    if not items:
        return
    for start in range(0, len(items), columns):
        row_items = items[start:start + columns]
        cols = st.columns(len(row_items))
        for col, item in zip(cols, row_items):
            with col:
                label = item[0]
                value = item[1]
                delta = item[2] if len(item) > 2 else None
                st.metric(label, value, delta)


def parse_json_like(value, default=None):
    if default is None:
        default = {}
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return default
        try:
            return json.loads(value)
        except Exception:
            return default
    return default


def invoice_extraction_payload(invoice_row):
    if not invoice_row:
        return {}
    payload = {}
    for key in ["raw_json", "extracted_json", "parsed_json", "metadata_json"]:
        parsed = parse_json_like(invoice_row.get(key), default={})
        if isinstance(parsed, dict) and parsed:
            payload = dict(parsed)
            break
    if not payload and isinstance(invoice_row, dict):
        payload = dict(invoice_row)

    for key in ("invoice_date", "due_date", "payment_terms"):
        canonical_value = invoice_row.get(key)
        if canonical_value not in (None, ""):
            payload[key] = canonical_value

    if payload.get("due_date") in (None, ""):
        calculated = calculate_due_date(
            payload.get("invoice_date"),
            payload.get("payment_terms"),
        )
        if calculated is not None:
            payload["due_date"] = calculated.isoformat()
    return payload


def populate_display_due_dates(dataframe):
    """Fill display-only due dates without altering raw extraction evidence."""
    if dataframe is None or getattr(dataframe, "empty", False):
        return dataframe
    required = {"invoice_date", "due_date", "payment_terms"}
    if not required.issubset(dataframe.columns):
        return dataframe

    display_df = dataframe.copy()
    for index, row in display_df.iterrows():
        current_due_date = row.get("due_date")
        if current_due_date not in (None, "") and str(current_due_date) not in {
            "nan",
            "NaT",
        }:
            continue
        calculated = calculate_due_date(
            row.get("invoice_date"),
            row.get("payment_terms"),
        )
        if calculated is not None:
            display_df.at[index, "due_date"] = calculated.isoformat()
    return display_df


def extract_warning_list(payload):
    warnings = payload.get("warnings", []) if isinstance(payload, dict) else []
    if isinstance(warnings, list):
        return [str(item) for item in warnings if str(item).strip()]
    if isinstance(warnings, str):
        parsed = parse_json_like(warnings, default=None)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
        return [warnings] if warnings.strip() else []
    return []


def _load_agent_optional_dataframe(statement, params):
    try:
        import pandas as pd
        from sqlalchemy import text
        from ap_database.engines import get_agent_session_factory

        if not ap_agent_db_exists():
            return pd.DataFrame()
        session_factory = get_agent_session_factory()
        with session_factory() as session:
            return pd.read_sql_query(
                text(statement),
                session.connection(),
                params=params,
            )
    except Exception:
        import pandas as pd
        return pd.DataFrame()


def load_exception_cases_for_invoice(selected_invoice):
    return _load_agent_optional_dataframe(
        """
        SELECT
            ec.id AS exception_id,
            ec.category,
            ec.classifier_rationale AS description,
            ec.status,
            ec.owner_team AS owner,
            ec.priority,
            ec.resolution_strategy,
            ec.created_at,
            ec.updated_at
        FROM exception_cases ec
        JOIN invoices i ON i.id = ec.invoice_id
        WHERE i.invoice_number = :invoice_number
        ORDER BY ec.created_at DESC, ec.id DESC
        """,
        {"invoice_number": selected_invoice},
    )


def load_agent_invoice_identity(selected_invoice):
    return _load_agent_optional_dataframe(
        """
        SELECT
            i.id AS invoice_id,
            i.invoice_number
        FROM invoices i
        WHERE i.invoice_number = :invoice_number
        ORDER BY i.created_at DESC, i.id DESC
        LIMIT 1
        """,
        {"invoice_number": selected_invoice},
    )


def load_posting_attempts_for_invoice(selected_invoice):
    return _load_agent_optional_dataframe(
        """
        SELECT
            pa.created_at,
            pa.status,
            pa.sap_document_number,
            pa.message,
            pa.attempt_number
        FROM posting_attempts pa
        JOIN invoices i ON i.id = pa.invoice_id
        WHERE i.invoice_number = :invoice_number
        ORDER BY pa.created_at DESC, pa.id DESC
        """,
        {"invoice_number": selected_invoice},
    )


def load_consumption_ledger_for_invoice(selected_invoice):
    return _load_agent_optional_dataframe(
        """
        SELECT
            cl.po_number,
            cl.grn_number,
            cl.invoice_number,
            cl.po_item AS line_no,
            cl.quantity AS consumed_qty,
            cl.amount AS consumed_amount,
            cl.ledger_status,
            cl.created_at
        FROM po_grn_consumption_ledger cl
        WHERE cl.invoice_number = :invoice_number
        ORDER BY cl.created_at DESC, cl.id DESC
        """,
        {"invoice_number": selected_invoice},
    )


def validation_summary(validation_df):
    summary = {"total": 0, "passed": 0, "failed_blocking": 0, "warnings": 0}
    if validation_df is None or getattr(validation_df, "empty", False):
        return summary
    summary["total"] = len(validation_df)
    for _, row in validation_df.iterrows():
        severity = str(row.get("severity", "")).upper()
        passed = to_bool(row.get("passed"))
        if severity in {"WARNING", "WARN"}:
            summary["warnings"] += 1
        elif passed:
            summary["passed"] += 1
        else:
            summary["failed_blocking"] += 1
    return summary


def failed_blocking_controls(validation_df):
    if validation_df is None or getattr(validation_df, "empty", False):
        return validation_df
    def is_blocking(row):
        severity = str(row.get("severity", "")).upper()
        if severity in {"WARNING", "WARN"}:
            return False
        return not to_bool(row.get("passed"))
    return validation_df[validation_df.apply(is_blocking, axis=1)]


def event_contains(events_df, tokens):
    if events_df is None or getattr(events_df, "empty", False):
        return False
    token_list = [str(token).upper() for token in tokens]
    for _, row in events_df.iterrows():
        haystack = " ".join(
            str(row.get(column, ""))
            for column in ["event_type", "agent_name", "message", "metadata_json"]
            if column in events_df.columns
        ).upper()
        if any(token in haystack for token in token_list):
            return True
    return False


def render_extracted_invoice_tiles(parsed_json):
    payload = parsed_json or {}
    if not isinstance(payload, dict):
        return
    render_metric_row(
        [
            ("Invoice", payload.get("invoice_number", "—")),
            ("Vendor", payload.get("vendor_name", "—")),
            ("Vendor No.", payload.get("vendor_number", "—")),
            ("PO", payload.get("po_number", "—")),
            ("Invoice Date", payload.get("invoice_date", "—")),
            ("Due Date", payload.get("due_date") or "—"),
            ("Payment Terms", payload.get("payment_terms") or "—"),
            ("Total", f"{payload.get('document_total', '—')} {payload.get('currency', '')}"),
        ],
        columns=4,
    )
    line_items = payload.get("line_items")
    if isinstance(line_items, list) and line_items:
        with st.expander("Show extracted line items", expanded=False):
            st.dataframe(one_based_dataframe(line_items), use_container_width=True)


def render_extraction_quality_tiles(invoice_row, validation_df=None, parsed_json=None):
    payload = parsed_json if isinstance(parsed_json, dict) and parsed_json else invoice_extraction_payload(invoice_row)
    canonical_confidence = canonicalize_extraction_confidence(payload)
    warnings = canonical_confidence.warnings
    review_required = payload.get("review_required") if isinstance(payload, dict) else None
    quality_score = canonical_confidence.extraction_confidence
    confidence_map = payload.get("field_confidence") if isinstance(payload, dict) else {}
    if isinstance(confidence_map, str):
        confidence_map = parse_json_like(confidence_map, default={})

    ocr_rules = None
    if validation_df is not None and not getattr(validation_df, "empty", False) and "rule_code" in validation_df.columns:
        mask = validation_df["rule_code"].astype(str).str.upper().str.startswith("OCR-")
        if "rule_name" in validation_df.columns:
            mask = mask | validation_df["rule_name"].astype(str).str.upper().str.contains("OCR|EXTRACTION", regex=True)
        ocr_rules = validation_df[mask]

    warning_count = len(warnings)
    failed_count = 0
    if ocr_rules is not None and not ocr_rules.empty:
        warning_count = 0
        failed_count = 0
        for _, result in ocr_rules.iterrows():
            severity = str(result.get("severity", "")).upper()
            passed = to_bool(result.get("passed"))
            if severity in {"WARNING", "WARN"} and not passed:
                warning_count += 1
            elif not passed:
                failed_count += 1

    if review_required is None:
        review_required = failed_count > 0 or warning_count > 0 and str(quality_score or "").strip() not in {"", "100"}

    status = "Review Required" if to_bool(review_required) or failed_count else "Passed"
    confidence = "High"
    if isinstance(confidence_map, dict) and confidence_map:
        low_fields = [k for k, v in confidence_map.items() if str(v).lower() == "low"]
        confidence = "Low" if low_fields else "High"
    elif quality_score not in [None, "", "—"]:
        try:
            confidence = "High" if float(quality_score) >= 0.9 else "Medium" if float(quality_score) >= 0.7 else "Low"
        except Exception:
            confidence = "Available"

    render_metric_row(
        [
            ("Extraction Quality", status),
            (
                "Overall Confidence",
                (
                    f"{float(quality_score):.2%}"
                    if quality_score not in [None, ""]
                    else "Unknown"
                ),
            ),
            ("Confidence Source", canonical_confidence.confidence_source),
            ("Field Confidence", confidence),
            ("OCR Warnings", warning_count),
            ("OCR Blocking Failures", failed_count),
            ("Review Required", "Yes" if to_bool(review_required) else "No"),
            ("Attempt", canonical_confidence.attempt_number),
            ("Retry Count", canonical_confidence.retry_count),
        ],
        columns=3,
    )
    if warnings:
        with st.expander("Extraction warnings", expanded=False):
            for warning in warnings:
                st.warning(warning)
    if ocr_rules is not None and not ocr_rules.empty:
        display_dataframe(
            ocr_rules,
            ["rule_code", "rule_name", "passed", "severity", "message", "created_at"],
            "No OCR/extraction quality controls found.",
        )
    else:
        st.success("No separate OCR blocking controls were generated. Extraction quality is treated as passed for this invoice.")


def render_validation_summary_and_groups(validation_df, extraction_context_available=False):
    summary = validation_summary(validation_df)
    render_metric_row(
        [
            ("Total Controls", summary["total"]),
            ("Passed Controls", summary["passed"]),
            ("Failed Blocking Controls", summary["failed_blocking"]),
            ("Warning / Advisory Controls", summary["warnings"]),
        ],
        columns=4,
    )
    if validation_df is None or getattr(validation_df, "empty", False):
        st.info("No validation results found.")
        return
    groups = [
        ("PO", ["PO", "AP-001", "PO-"]),
        ("GRN", ["GRN", "AP-006", "AP-007"]),
        ("Vendor", ["VENDOR", "VND", "AP-002", "AP-003", "AP-004"]),
        ("Duplicate", ["DUP", "AP-009"]),
        ("Financial / Amount", ["FIN", "AMOUNT", "PRICE", "TOTAL", "AP-008"]),
        ("Tax / VAT", ["TAX", "VAT", "GST"]),
        ("Payment Terms", ["PAY", "PAYMENT", "TERMS", "AP-010"]),
        ("Date", ["DATE"]),
        ("PO/GRN Consumption", ["CONS", "CONSUMPTION", "LEDGER", "CUMULATIVE"]),
        ("Extraction Quality", ["OCR", "EXTRACTION"]),
        ("Other", []),
    ]
    tabs = st.tabs([name for name, _ in groups])
    assigned = set()
    for tab, (name, tokens) in zip(tabs, groups):
        with tab:
            if tokens:
                indexes = []
                upper_tokens = [token.upper() for token in tokens]
                for idx, row in validation_df.iterrows():
                    haystack = " ".join(
                        str(row.get(column, ""))
                        for column in ["rule_code", "rule_name", "message"]
                        if column in validation_df.columns
                    ).upper()
                    if any(token in haystack for token in upper_tokens):
                        indexes.append(idx)
                        assigned.add(idx)
                group_df = validation_df.loc[indexes]
            else:
                group_df = validation_df.drop(index=list(assigned), errors="ignore")
            if name == "Extraction Quality" and (group_df is None or group_df.empty):
                if extraction_context_available:
                    st.success("No separate extraction-quality validation rows were generated. The extraction quality tiles above show the result for this clean invoice.")
                else:
                    st.info("No extraction quality controls found.")
            else:
                display_dataframe(
                    group_df,
                    ["rule_code", "rule_name", "passed", "severity", "message", "created_at"],
                    f"No {name.lower()} controls found.",
                )


def render_exception_case_section(exception_df):
    if exception_df is None or getattr(exception_df, "empty", False):
        st.info("No exception case found for this invoice.")
        return
    open_count = 0
    if "status" in exception_df.columns:
        open_count = int(exception_df["status"].astype(str).str.upper().isin({"OPEN", "ACTIVE"}).sum())
    latest = exception_df.iloc[0]
    render_metric_row(
        [
            ("Exception Cases", len(exception_df)),
            ("Open Exceptions", open_count),
            ("Latest Category", current_row_value(latest, ["category"])),
            ("Owner", current_row_value(latest, ["owner", "owner_team"])),
            ("Priority", current_row_value(latest, ["priority"])),
            ("Resolution Strategy", current_row_value(latest, ["resolution_strategy"])),
        ],
        columns=3,
    )
    display_dataframe(
        exception_df,
        ["exception_id", "category", "description", "status", "owner", "priority", "resolution_strategy", "created_at", "updated_at"],
        "No exception cases found.",
    )


def render_communication_section(communication_df):
    if communication_df is None or getattr(communication_df, "empty", False):
        st.info("No email / communication records found. Clean posted invoices normally do not generate exception emails.")
        return
    latest = communication_df.iloc[0]
    render_metric_row(
        [
            ("Messages", len(communication_df)),
            ("Latest Status", current_row_value(latest, ["status"])),
            ("Latest Recipient", current_row_value(latest, ["recipient"])),
            ("Latest Direction", current_row_value(latest, ["direction"])),
        ],
        columns=4,
    )
    display_dataframe(
        communication_df,
        ["created_at", "direction", "recipient", "subject", "status", "smtp_message_id"],
        "No email / communication records found.",
    )
    if "body" in communication_df.columns:
        for _, communication in communication_df.iterrows():
            with st.expander(f"Message body — {communication.get('subject', 'Message')}", expanded=False):
                st.code(str(communication.get("body", "")), language="text")


def render_events_section(events_df):
    if events_df is None or getattr(events_df, "empty", False):
        st.info("No workflow events found.")
        return
    latest = events_df.iloc[0]
    render_metric_row(
        [
            ("Audit Events", len(events_df)),
            ("Latest Event", current_row_value(latest, ["event_type"])),
            ("Latest Agent", current_row_value(latest, ["agent_name"])),
            ("Response Captured", "Yes" if event_contains(events_df, ["RESPONSE", "EVIDENCE", "FIELD_UPDATED"]) else "No"),
            ("Recheck / Reprocess", "Yes" if event_contains(events_df, ["RECHECK", "REPROCESS"]) else "No"),
        ],
        columns=3,
    )
    display_dataframe(
        events_df,
        ["created_at", "event_type", "agent_name", "message"],
        "No workflow events found.",
    )


def render_posting_and_ledger_section(posting_df, ledger_df, selected_row, posted_row=None):
    render_metric_row(
        [
            ("Posting Status", current_row_value(selected_row, ["posting_status"])),
            ("SAP Document", current_row_value(selected_row, ["sap_document_number"])),
            ("Payment Status", current_row_value(selected_row, ["payment_status"])),
            ("Due Date", current_row_value(selected_row, ["due_date"])),
            ("Posted Reference", "Created" if posted_row else "Not Created"),
            ("Posting Attempts", 0 if posting_df is None or getattr(posting_df, "empty", False) else len(posting_df)),
            ("Consumption Ledger Rows", 0 if ledger_df is None or getattr(ledger_df, "empty", False) else len(ledger_df)),
        ],
        columns=3,
    )
    st.info("Posting and payment are separate. A posted invoice should not automatically be treated as paid.")
    st.markdown("**Posting attempts**")
    display_dataframe(
        posting_df,
        ["created_at", "status", "sap_document_number", "message", "attempt_number"],
        "No posting attempts found.",
    )
    st.markdown("**PO/GRN consumption ledger**")
    display_dataframe(
        ledger_df,
        ["po_number", "grn_number", "invoice_number", "line_no", "consumed_qty", "consumed_amount", "ledger_status", "created_at"],
        "No PO/GRN consumption ledger rows found.",
    )
    if posted_row:
        with st.expander("Show posted invoice reference", expanded=False):
            st.json({key: str(value) for key, value in posted_row.items()})


def active_exception_id(exception_df):
    if exception_df is None or getattr(exception_df, "empty", False):
        return None
    if "status" in exception_df.columns:
        open_cases = exception_df[exception_df["status"].astype(str).str.upper().isin({"OPEN", "ACTIVE"})]
    else:
        open_cases = exception_df.iloc[0:0]
    active_case = open_cases.iloc[0] if not open_cases.empty else exception_df.iloc[0]
    return current_row_value(active_case, ["exception_id"], default=None)


def render_response_recheck_controls(invoice_number, selected_row, validation_df, exception_df, identity_df):
    exception_id = active_exception_id(exception_df)
    invoice_id = None
    if identity_df is not None and not getattr(identity_df, "empty", False):
        invoice_id = current_row_value(identity_df.iloc[0], ["invoice_id"], default=None)

    if not exception_id:
        st.info("No open exception is available for response intake. Clean invoices do not need this step.")
        return

    template_options = [
        "Supplier confirms GRN completed",
        "Supplier provides corrected PO number",
        "Procurement confirms payment terms",
        "Procurement confirms PO reopened",
        "General clarification",
        "Custom response",
    ]
    template = st.selectbox("Response Template", template_options, key=f"walkthrough_response_template_{invoice_number}")
    current_po = current_row_value(selected_row, ["po_number"], default="")
    if template == "Supplier confirms GRN completed":
        default_response = f"GRN has now been completed for invoice {invoice_number}. Please recheck against PO {current_po}."
    elif template == "Supplier provides corrected PO number":
        default_response = f"The correct PO number for invoice {invoice_number} is {current_po}. Please update and run controlled recheck."
    elif template == "Procurement confirms payment terms":
        default_response = f"Procurement confirms the approved payment terms for PO {current_po} and invoice {invoice_number} are NET 30. Please update the PO master payment terms and rerun validation."
    elif template == "Procurement confirms PO reopened":
        default_response = f"Procurement confirms PO {current_po} has been reopened and is valid for invoice {invoice_number}."
    elif template == "General clarification":
        default_response = f"Clarification received for invoice {invoice_number}. Please attach this response and run controlled recheck."
    else:
        default_response = ""

    response_text = st.text_area(
        "Supplier / Procurement Response",
        value=default_response,
        height=130,
        key=f"walkthrough_response_text_{invoice_number}",
    )
    col_a, col_b = st.columns(2)
    with col_a:
        corrected_po = st.text_input("Corrected PO Number (optional)", value="", key=f"walkthrough_corrected_po_{invoice_number}")
        responder = st.text_input("Responder", value="Demo Procurement User", key=f"walkthrough_responder_{invoice_number}")
    with col_b:
        approved_terms = st.text_input("Approved Payment Terms (optional)", value="", key=f"walkthrough_approved_terms_{invoice_number}")
        source = st.selectbox("Source", ["PROCUREMENT", "VENDOR", "AP", "MANUAL_TEST"], key=f"walkthrough_response_source_{invoice_number}")

    col_submit, col_recheck, col_refresh = st.columns(3)
    with col_submit:
        if st.button("Submit Response", key=f"walkthrough_submit_response_{invoice_number}", use_container_width=True):
            if not response_text.strip():
                st.warning("Enter response text first.")
            else:
                values = {"invoice_number": invoice_number, "response_source": "Demo Walkthrough"}
                if corrected_po.strip():
                    values["po_number"] = corrected_po.strip()
                if approved_terms.strip():
                    values["payment_terms"] = approved_terms.strip()
                payload = {
                    "exception_id": exception_id,
                    "source": source,
                    "response_text": response_text.strip(),
                    "provided_by": responder or "Demo UI",
                    "values": values,
                    "resume_recheck": False,
                }
                result = post_agent_api(
                    EXCEPTION_RESPONSE_ENDPOINT_TEMPLATE.format(exception_id=exception_id),
                    payload=payload,
                )
                st.session_state[f"last_response_text_{invoice_number}"] = response_text.strip()
                st.session_state[f"last_response_result_{invoice_number}"] = result
                if result.get("ok"):
                    st.success("Response recorded successfully.")
                else:
                    st.error("Response intake failed.")
                with st.expander("Response API result", expanded=False):
                    st.json(result)
    with col_recheck:
        if st.button("Run Controlled Recheck", key=f"walkthrough_run_recheck_{invoice_number}", use_container_width=True):
            if not invoice_id:
                st.warning("Invoice ID was not found for this AP Agent invoice.")
            else:
                payload = {
                    "latest_message": st.session_state.get(f"last_response_text_{invoice_number}", response_text),
                    "simulate_resolution": False,
                }
                result = post_agent_api(
                    CONTROLLED_RECHECK_ENDPOINT_TEMPLATE.format(invoice_id=invoice_id),
                    payload=payload,
                )
                st.session_state[f"last_recheck_result_{invoice_number}"] = result
                if result.get("ok"):
                    st.success("Controlled recheck completed.")
                else:
                    st.error("Controlled recheck failed.")
                with st.expander("Recheck API result", expanded=False):
                    st.json(result)
    with col_refresh:
        if st.button("Refresh Journey", key=f"walkthrough_refresh_{invoice_number}", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    with st.expander("Last technical response/recheck result", expanded=False):
        st.json(
            {
                "last_response_result": st.session_state.get(f"last_response_result_{invoice_number}"),
                "last_recheck_result": st.session_state.get(f"last_recheck_result_{invoice_number}"),
            }
        )

def render_single_invoice_walkthrough(invoice_number):
    """Show the complete CP-19 lifecycle for one invoice on the Demo Walkthrough page."""
    invoice_row = get_invoice_master_row(invoice_number)

    if not invoice_row:
        st.info("No saved invoice record found for this invoice yet.")
        return

    extraction_payload = invoice_extraction_payload(invoice_row)

    st.markdown("### 1. Invoice saved and extracted")
    render_metric_row(
        [
            ("Invoice", invoice_row.get("invoice_number", "—")),
            ("PO", invoice_row.get("po_number", "—")),
            ("Vendor", invoice_row.get("vendor_name", "—")),
            ("Vendor No.", invoice_row.get("vendor_number", extraction_payload.get("vendor_number", "—") if isinstance(extraction_payload, dict) else "—")),
            ("Payment Terms", invoice_row.get("payment_terms", extraction_payload.get("payment_terms", "—") if isinstance(extraction_payload, dict) else "—")),
            ("Due Date", extraction_payload.get("due_date") or invoice_row.get("due_date") or "—"),
            ("Total", f"{invoice_row.get('document_total', '—')} {invoice_row.get('currency', '')}"),
        ],
        columns=3,
    )
    render_extracted_invoice_tiles(extraction_payload)
    with st.expander("Show saved invoice record", expanded=False):
        st.json({key: str(value) for key, value in invoice_row.items()})

    st.markdown("### 2. Extraction quality")
    validation_df = load_ap_agent_validation_results(invoice_number) if ap_agent_db_exists() else None
    render_extraction_quality_tiles(invoice_row, validation_df=validation_df, parsed_json=extraction_payload)

    st.markdown("### 3. AP Agent review status")
    if not ap_agent_db_exists():
        st.warning("AP Agent database is not available yet. Start the AP Agent API and process the invoice.")
        return

    agent_df = load_ap_agent_invoices(limit=500)
    if agent_df.empty or "invoice_number" not in agent_df.columns:
        st.info("Invoice is saved, but AP Agent has not imported it yet.")
        return

    selected_agent_rows = agent_df[agent_df["invoice_number"].astype(str) == str(invoice_number)]
    if selected_agent_rows.empty:
        st.info(
            "Invoice is saved, but AP Agent has not imported it yet. "
            "Make sure AP Agent API is running and trigger invoice processing."
        )
        return

    selected_agent_row_series = selected_agent_rows.iloc[0]
    selected_agent_row = selected_agent_row_series.to_dict()
    workflow_status = current_row_value(selected_agent_row_series, ["status", "agent_status", "workflow_status"])
    validation_df = load_ap_agent_validation_results(invoice_number)
    communication_df = load_ap_agent_communications(invoice_number)
    events_df = load_ap_agent_events(invoice_number)
    exception_df = load_exception_cases_for_invoice(invoice_number)
    identity_df = load_agent_invoice_identity(invoice_number)
    posting_df = load_posting_attempts_for_invoice(invoice_number)
    ledger_df = load_consumption_ledger_for_invoice(invoice_number)
    posted_row = get_posted_invoice_master_row(invoice_number)
    failed_df = failed_blocking_controls(validation_df)
    failed_count = 0 if failed_df is None or getattr(failed_df, "empty", False) else len(failed_df)

    render_metric_row(
        [
            ("Workflow Status", workflow_status),
            ("Failed Rules", failed_count),
            ("Posting Status", current_row_value(selected_agent_row_series, ["posting_status"])),
            ("SAP Document", current_row_value(selected_agent_row_series, ["sap_document_number"])),
            ("Payment Status", current_row_value(selected_agent_row_series, ["payment_status"])),
            ("Exception Category", current_row_value(selected_agent_row_series, ["exception_category"])),
        ],
        columns=3,
    )
    st.markdown("#### Canonical extraction and resolution")
    render_metric_row(
        [
            ("Invoice Supplier", selected_agent_row.get("vendor_name", "—")),
            (
                "Extracted Vendor No.",
                selected_agent_row.get("extracted_vendor_number") or "—",
            ),
            (
                "Resolved Vendor No.",
                selected_agent_row.get("resolved_vendor_number") or "—",
            ),
            (
                "Vendor Match",
                " / ".join(
                    str(value)
                    for value in (
                        selected_agent_row.get("vendor_match_status"),
                        selected_agent_row.get("vendor_match_method"),
                    )
                    if value not in (None, "")
                ) or "—",
            ),
            (
                "Invoice Date (raw → normalized)",
                f"{selected_agent_row.get('raw_invoice_date') or '—'} → "
                f"{selected_agent_row.get('invoice_date') or '—'}",
            ),
            (
                "Due Date (raw → normalized)",
                f"{selected_agent_row.get('raw_due_date') or '—'} → "
                f"{selected_agent_row.get('due_date') or '—'}",
            ),
            (
                "Currency (extracted → resolved)",
                f"{selected_agent_row.get('extracted_currency') or '—'} → "
                f"{selected_agent_row.get('resolved_currency') or '—'}",
            ),
            (
                "Currency Method",
                selected_agent_row.get("currency_resolution_method") or "—",
            ),
            (
                "Extraction Confidence",
                selected_agent_row.get("extraction_confidence") or "—",
            ),
            (
                "Confidence Source",
                selected_agent_row.get("extraction_confidence_source") or "—",
            ),
            (
                "Extraction Provider / Model",
                " / ".join(
                    str(value)
                    for value in (
                        selected_agent_row.get("extraction_provider"),
                        selected_agent_row.get("extraction_model"),
                    )
                    if value not in (None, "")
                ) or "—",
            ),
            (
                "Extraction Attempt",
                selected_agent_row.get("extraction_attempt_number") or "—",
            ),
            (
                "Retry Count",
                selected_agent_row.get("extraction_retry_count") or 0,
            ),
            (
                "Extraction Review",
                selected_agent_row.get("extraction_review_status") or "—",
            ),
        ],
        columns=4,
    )
    if selected_agent_row.get("date_parse_warning"):
        st.warning(str(selected_agent_row["date_parse_warning"]))

    st.markdown("### 4. Journey status")
    response_captured = event_contains(events_df, ["RESPONSE", "EVIDENCE", "FIELD_UPDATED"])
    recheck_happened = event_contains(events_df, ["RECHECK", "REPROCESS"])
    render_metric_row(
        [
            ("Invoice Received", "Done"),
            ("OCR + LLM Extraction", "Done"),
            ("Extraction Quality", "Review" if str(workflow_status) in {"EXTRACTION_REVIEW_REQUIRED", "EXTRACTION_FAILED"} else "Passed"),
            ("AP Controls", "Failed" if failed_count else "Passed"),
            ("Exception", "Created" if not exception_df.empty else "Not Required"),
            ("Communication", "Created" if not communication_df.empty else "Not Created"),
            ("Response", "Captured" if response_captured else "Not Captured"),
            ("Controlled Recheck", "Done" if recheck_happened else "Not Done"),
            ("Posting", "Posted" if str(workflow_status) == "POSTED" or posted_row else "Not Posted"),
        ],
        columns=3,
    )

    st.markdown("### 5. Validation controls")
    render_validation_summary_and_groups(
        validation_df,
        extraction_context_available=bool(extraction_payload),
    )

    st.markdown("### 6. Exception and response / recheck")
    tab_exception, tab_response, tab_after = st.tabs(
        ["Exception Case", "Capture Response & Recheck", "After Recheck Evidence"]
    )
    with tab_exception:
        render_exception_case_section(exception_df)
        st.markdown("**Failed blocking controls**")
        display_dataframe(
            failed_df,
            ["rule_code", "rule_name", "passed", "severity", "message", "created_at"],
            "No failed blocking controls found for this invoice.",
        )
    with tab_response:
        render_response_recheck_controls(
            invoice_number,
            selected_agent_row_series,
            validation_df,
            exception_df,
            identity_df,
        )
    with tab_after:
        refreshed_agent_df = load_ap_agent_invoices(limit=500)
        refreshed_row = selected_agent_row_series
        if refreshed_agent_df is not None and not refreshed_agent_df.empty and "invoice_number" in refreshed_agent_df.columns:
            refreshed_match = refreshed_agent_df[refreshed_agent_df["invoice_number"].astype(str) == str(invoice_number)]
            if not refreshed_match.empty:
                refreshed_row = refreshed_match.iloc[0]
        refreshed_validations = load_ap_agent_validation_results(invoice_number)
        refreshed_events = load_ap_agent_events(invoice_number)
        refreshed_communications = load_ap_agent_communications(invoice_number)
        refreshed_failed = failed_blocking_controls(refreshed_validations)
        refreshed_failed_count = 0 if refreshed_failed is None or getattr(refreshed_failed, "empty", False) else len(refreshed_failed)
        render_metric_row(
            [
                ("Workflow", f"{workflow_status} → {current_row_value(refreshed_row, ['status', 'agent_status', 'workflow_status'])}"),
                ("Posting", f"{current_row_value(selected_agent_row_series, ['posting_status'])} → {current_row_value(refreshed_row, ['posting_status'])}"),
                ("Payment", f"{current_row_value(selected_agent_row_series, ['payment_status'])} → {current_row_value(refreshed_row, ['payment_status'])}"),
                ("Due Date", current_row_value(refreshed_row, ["due_date"])),
                ("Failed Controls", f"{failed_count} → {refreshed_failed_count}"),
                ("Communications", f"{0 if communication_df.empty else len(communication_df)} → {0 if refreshed_communications.empty else len(refreshed_communications)}"),
                ("Audit Events", f"{0 if events_df.empty else len(events_df)} → {0 if refreshed_events.empty else len(refreshed_events)}"),
                ("Response Captured", "Yes" if event_contains(refreshed_events, ["RESPONSE", "EVIDENCE", "FIELD_UPDATED"]) else "No"),
                ("Recheck Happened", "Yes" if event_contains(refreshed_events, ["RECHECK", "REPROCESS"]) else "No"),
            ],
            columns=4,
        )
        display_dataframe(
            refreshed_events.head(15) if refreshed_events is not None and not refreshed_events.empty else refreshed_events,
            ["created_at", "event_type", "agent_name", "message"],
            "No workflow events found after recheck.",
        )

    st.markdown("### 7. Communication / email")
    render_communication_section(communication_df)

    st.markdown("### 8. Posting, payment, and PO/GRN consumption")
    render_posting_and_ledger_section(posting_df, ledger_df, selected_agent_row_series, posted_row=posted_row)

    st.markdown("### 9. Workflow audit trail")
    render_events_section(events_df)

    st.markdown("### 10. Technical details for deep-dive")
    st.caption("Use AP Agent Monitor for broader history. This expander keeps the walkthrough page client-friendly by default.")
    with st.expander("AP Agent invoice row", expanded=False):
        st.dataframe(selected_agent_rows, use_container_width=True)
    with st.expander("Validation raw data", expanded=False):
        st.json(validation_df.to_dict(orient="records") if validation_df is not None else [])
    with st.expander("Exception raw data", expanded=False):
        st.json(exception_df.to_dict(orient="records") if exception_df is not None else [])
    with st.expander("Communication raw data", expanded=False):
        st.json(communication_df.to_dict(orient="records") if communication_df is not None else [])
    with st.expander("Workflow event raw data", expanded=False):
        st.json(events_df.to_dict(orient="records") if events_df is not None else [])
    with st.expander("Posting and ledger raw data", expanded=False):
        st.json(
            {
                "posting_attempts": posting_df.to_dict(orient="records") if posting_df is not None else [],
                "consumption_ledger": ledger_df.to_dict(orient="records") if ledger_df is not None else [],
            }
        )


def init_line_items(state_key, default_item):
    if state_key not in st.session_state:
        st.session_state[state_key] = [default_item]


def build_line_item_payload(items, qty_key="qty", amount_key="line_amount"):
    payload_items = []
    subtotal = 0.0

    for i, item in enumerate(items, start=1):
        qty = float(item.get(qty_key, 0))
        unit_price = float(item.get("unit_price", 0.0))
        line_amount = float(item.get(amount_key, qty * unit_price))

        normalized = dict(item)
        normalized["line_no"] = i
        normalized["qty"] = int(qty)
        normalized["unit_price"] = unit_price
        normalized["line_amount"] = line_amount

        payload_items.append(normalized)
        subtotal += line_amount

    return payload_items, subtotal



def save_manual_invoice_to_master(payload):

    init_db()

    with get_conn() as conn:

        upsert_invoice(
            conn,
            payload
        )

        conn.commit()

    try:

        ap_agent_result = trigger_ap_agent_process_new(
            limit=50
        )

        return {
            "status": "success",
            "invoice_number": payload.get("invoice_number"),
            "ap_agent_trigger": ap_agent_result,
            "ap_agent_trigger_error": None,
        }

    except Exception as trigger_error:

        return {
            "status": "success",
            "invoice_number": payload.get("invoice_number"),
            "ap_agent_trigger": None,
            "ap_agent_trigger_error": str(trigger_error),
        }
    




def render_line_items_editor(
    state_key,
    prefix,
    title,
    qty_label="Qty",
    qty_min=1,
):
    st.subheader(title)
    init_line_items(
        state_key,
        {
            "line_no": 1,
            "description": "",
            "qty": 1,
            "unit_price": 0.0,
            "line_amount": 0.0,
        },
    )

    subtotal = 0.0

    for i, item in enumerate(st.session_state[state_key]):
        st.markdown(f"### Item {i + 1}")
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            item["description"] = st.text_input(
                "Description",
                value=item.get("description", ""),
                key=f"{prefix}_description_{i}",
            )

        with col2:
            item["qty"] = st.number_input(
                qty_label,
                min_value=qty_min,
                value=int(item.get("qty", 1)),
                key=f"{prefix}_qty_{i}",
            )

        with col3:
            item["unit_price"] = st.number_input(
                "Unit Price",
                min_value=0.0,
                value=float(item.get("unit_price", 0.0)),
                key=f"{prefix}_unit_price_{i}",
            )

        item["line_no"] = i + 1
        item["line_amount"] = float(item["qty"]) * float(item["unit_price"])

        with col4:
            st.number_input(
                "Line Amount",
                value=float(item["line_amount"]),
                disabled=True,
                key=f"{prefix}_line_amount_{i}",
            )

        subtotal += float(item["line_amount"])
        st.divider()

    col_add, col_remove = st.columns(2)

    with col_add:
        if st.button(f"Add {title[:-1]} Item", key=f"{prefix}_add_item"):
            st.session_state[state_key].append(
                {
                    "line_no": len(st.session_state[state_key]) + 1,
                    "description": "",
                    "qty": 1,
                    "unit_price": 0.0,
                    "line_amount": 0.0,
                }
            )
            st.rerun()

    with col_remove:
        if st.button(f"Remove Last {title[:-1]} Item", key=f"{prefix}_remove_item"):
            if len(st.session_state[state_key]) > 1:
                st.session_state[state_key].pop()
                st.rerun()

    return subtotal, st.session_state[state_key]


# -----------------------------------
# PAGE CONFIG
# -----------------------------------

st.set_page_config(
    page_title="AP Agent Demo",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .main .block-container {padding-top: 1.5rem;}
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #e6e9ef;
        padding: 14px 16px;
        border-radius: 14px;
        box-shadow: 0 1px 3px rgba(16, 24, 40, 0.06);
        min-height: 112px;
        overflow: visible;
    }
    div[data-testid="stMetricLabel"] p,
    div[data-testid="stMetricValue"] > div,
    div[data-testid="stMetricValue"] p,
    div[data-testid="stMetricDelta"] > div {
        max-width: 100%;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
        overflow-wrap: anywhere;
        word-break: break-word;
    }
    div[data-testid="stMetricLabel"] p {
        line-height: 1.25;
    }
    div[data-testid="stMetricValue"] {
        width: 100%;
        overflow: visible;
    }
    div[data-testid="stMetricValue"] > div,
    div[data-testid="stMetricValue"] p {
        font-size: clamp(1rem, 1.7vw, 1.75rem);
        line-height: 1.2;
    }
    .demo-card {
        background: linear-gradient(135deg, #f8fbff 0%, #eef5ff 100%);
        border: 1px solid #dbe7ff;
        border-radius: 18px;
        padding: 18px 20px;
        margin-bottom: 18px;
    }
    .small-muted {color: #667085; font-size: 0.92rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="demo-card">
      <h1 style="margin-bottom: 0.25rem;">AP Automation Demo</h1>
      <div class="small-muted">Invoice ingestion → PO/GRN validation → exception handling → SAP posting simulation.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

reset_success_message = st.session_state.pop("demo_reset_success", None)
if reset_success_message:
    st.success(reset_success_message)
    reset_result = st.session_state.pop("demo_reset_result", None)
    if reset_result:
        with st.expander("Reset details"):
            st.json(reset_result)

st.sidebar.header("Demo Navigation")
st.sidebar.caption("Recommended flow: Dashboard → Demo Walkthrough → AP Agent Monitor")

with st.sidebar.expander("Reset invoice demo flow", expanded=False):
    render_invoice_flow_reset_panel(location="sidebar")

st.sidebar.divider()
st.sidebar.caption("Service Status")
st.sidebar.write(f"AP Agent: {api_health(AP_AGENT_BASE_URL)}")
st.sidebar.caption(AP_AGENT_BASE_URL)
st.sidebar.write(f"Mock SAP API: {api_health(API_BASE_URL)}")
st.sidebar.caption(API_BASE_URL)

selected_module = st.sidebar.radio(
    "Choose screen",
    [
        "Dashboard",
        "Demo Walkthrough",
        "AP Agent Monitor",
        "Reference Data Sync (PO/GRN API)",
        "Admin Data Manager",
    ],
)

# ===================================
# DASHBOARD
# ===================================

if selected_module == "Dashboard":
    st.header("Demo Overview")

    st.success(
        "Demo flow: upload one invoice, run OCR + LLM extraction, save it to invoice_master, then show AP Agent validation, posting or exception handling."
    )

    step1, step2, step3, step4 = st.columns(4)
    with step1:
        st.info("1. Upload Invoice")
    with step2:
        st.info("2. OCR + LLM Extract")
    with step3:
        st.info("3. AP Agent Checks")
    with step4:
        st.info("4. Post or Draft Email")

    col1, col2, col3,col4 = st.columns(4)

    with col1:
        try:
            st.metric("Invoices Processed", get_table_count("invoice_master"))
        except Exception:
            st.metric("Invoices Processed", 0)

    with col2:
        try:
            st.metric("PO Records", get_table_count("sap_po_master"))
        except Exception:
            st.metric("PO Records", 0)

    with col3:
        try:
            st.metric("GRN Records", get_table_count("sap_grn_master"))
        except Exception:
            st.metric("GRN Records", 0)
    with col4:
        try:
            st.metric(
                "Posted Invoices",
                get_table_count("sap_posted_invoice_master")
            )
        except Exception:
            st.metric("Posted Invoices", 0)
    st.info("System ready for processing.")

    st.subheader("All Invoices")
    try:
        invoice_df = populate_display_due_dates(
            load_table_data(
                "invoice_master",
                limit=get_table_count("invoice_master"),
            )
        )
        invoice_df.index=(invoice_df.index+1)
        invoice_df.index.name="R.no"
        st.dataframe(
            invoice_df,
            use_container_width=True,
            height=400,
        )
    except Exception as e:
        st.error(f"Invoice table error: {e}")

    st.subheader("All Purchase Orders")
    try:
        po_df=load_table_data(
            "sap_po_master",
            limit=get_table_count("sap_po_master"),
        )
        po_df.index=(po_df.index+1)
        po_df.index.name="R.no"
        st.dataframe(
            po_df,
            use_container_width=True,
            height=400,
        )
    except Exception as e:
        st.error(f"PO table error: {e}")

    st.subheader("All GRNs")
    try:
        grn_df=load_table_data(
            "sap_grn_master",
            limit=get_table_count("sap_grn_master"),
        )
        grn_df.index=(grn_df.index+1)
        grn_df.index.name="R.no"
        st.dataframe(
            grn_df,
            use_container_width=True,
            height=400,
        )
    except Exception as e:
        st.error(f"GRN table error: {e}")

    st.subheader("All Posted Invoices")
    try:
        posted_invoice_df = populate_display_due_dates(
            load_table_data(
                "sap_posted_invoice_master",
                limit=get_table_count("sap_posted_invoice_master"),
            )
        )
        posted_invoice_df.index = posted_invoice_df.index + 1
        posted_invoice_df.index.name = "R.no"
        st.dataframe(
            posted_invoice_df,
            use_container_width=True,
            height=400,
        )
    except Exception as e:
        st.error(f"Posted invoice table error: {e}")
# ===================================
# DEMO WALKTHROUGH
# ===================================

elif selected_module == "Demo Walkthrough":
    st.header("Invoice Upload Walkthrough - End to End")

    st.info(
        "Use this page for the client demo. Start with one invoice upload, then show "
        "OCR + LLM extraction, invoice save, AP Agent validation, posting or exception, "
        "and any drafted email communication on the same page."
    )

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Invoices", get_safe_table_count("invoice_master"))
    with m2:
        st.metric("PO Reference", get_safe_table_count("sap_po_master"))
    with m3:
        st.metric("GRN Reference", get_safe_table_count("sap_grn_master"))
    with m4:
        st.metric("Posted", get_safe_table_count("sap_posted_invoice_master"))

    st.divider()

    st.markdown("### Step 1 - Upload invoice")
    st.caption(
        "Before this step, PO and GRN reference data should already exist in master. "
        "Use Reference Data Sync only to refresh PO/GRN data. This page does not create demo PO, GRN, or invoice content."
    )

    uploaded_file = st.file_uploader(
        "Upload invoice PDF or image",
        type=["pdf", "png", "jpg", "jpeg"],
        key="walkthrough_invoice_upload",
    )

    if uploaded_file:
        st.success(f"Selected invoice file: {uploaded_file.name}")

        if st.button(
            "Process uploaded invoice end-to-end",
            key="walkthrough_process_uploaded_invoice",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner(
                "Running OCR, LLM extraction, saving invoice, and triggering AP Agent..."
            ):
                try:
                    saved_file_path, artifact_bundle = save_uploaded_file(uploaded_file)
                    result = process_invoice_pipeline(
                        saved_file_path,
                        artifact_bundle=artifact_bundle,
                    )

                    st.session_state["last_upload_pipeline_result"] = result
                    st.session_state["last_upload_file_name"] = uploaded_file.name

                    parsed_json = result.get("parsed_json") or {}
                    invoice_number = parsed_json.get("invoice_number")

                    if invoice_number:
                        st.session_state["current_uploaded_invoice_number"] = str(invoice_number)

                    st.cache_data.clear()
                    st.rerun()

                except Exception as e:
                    st.exception(e)

    pipeline_result = st.session_state.get("last_upload_pipeline_result")
    if pipeline_result:
        st.markdown("### Step 2 - OCR + LLM extraction result")

        status = pipeline_result.get("status", "failed")
        if status == "success":
            st.success("Invoice OCR and LLM extraction completed.")

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("OCR Time", f"{pipeline_result.get('ocr_time_sec', 'N/A')} sec")
            with col2:
                st.metric("LLM Time", f"{pipeline_result.get('groq_time_sec', 'N/A')} sec")
            with col3:
                st.metric("Total Time", f"{pipeline_result.get('total_time_sec', 'N/A')} sec")

            parsed_json = pipeline_result.get("parsed_json", {})
            invoice_number = parsed_json.get("invoice_number")

            st.subheader("Extracted invoice dashboard")
            render_extracted_invoice_tiles(parsed_json)
            st.subheader("Extraction quality dashboard")
            render_extraction_quality_tiles(parsed_json, validation_df=None, parsed_json=parsed_json)

            if invoice_number:
                st.success(
                    f"Invoice `{invoice_number}` was extracted and should now be saved in invoice_master."
                )
            else:
                st.warning(
                    "OCR/LLM completed, but no invoice number was extracted. "
                    "The invoice cannot be shown in the AP Agent flow until invoice_number is available."
                )

            with st.expander("Show extracted invoice JSON", expanded=False):
                st.json(parsed_json)

            if parsed_json.get("ap_agent_trigger_error"):
                st.warning(
                    "Invoice was extracted, but AP Agent trigger failed. "
                    "Make sure AP Agent API is running on port 8000."
                )
                st.code(parsed_json.get("ap_agent_trigger_error"), language="text")

        else:
            error_text = str(
                pipeline_result.get("error")
                or pipeline_result.get("message")
                or pipeline_result
            )
            st.error("Invoice processing failed.")
            st.code(error_text, language="text")

            lower_error = error_text.lower()
            if (
                "fitz" in lower_error
                or "pymupdf" in lower_error
                or "paddle" in lower_error
                or "no module named" in lower_error
            ):
                st.warning(
                    "OCR dependencies are missing in this environment. "
                    "Install the optional OCR packages only if you want PDF/Image upload processing."
                )
                st.code(
                    "python -m pip install PyMuPDF==1.27.2.3 paddleocr==2.7.3 paddlepaddle==2.6.2 protobuf==3.20.2",
                    language="powershell",
                )
            elif "groq" in lower_error or "api_key" in lower_error or "api key" in lower_error:
                st.warning(
                    "LLM extraction needs GROQ_API_KEY in your .env file."
                )

    st.divider()

    st.markdown("### Step 3 - AP Agent review and final result")
    invoice_options = load_invoice_options(limit=500)
    current_uploaded_invoice = st.session_state.get("current_uploaded_invoice_number")

    if current_uploaded_invoice and current_uploaded_invoice not in invoice_options:
        invoice_options = [current_uploaded_invoice] + invoice_options

    if not invoice_options:
        st.warning(
            "No saved invoice is available yet. Upload and process an invoice first. "
            "If the invoice was processed but not saved, check that invoice_number was extracted and the database path is valid."
        )
    else:
        default_index = 0
        if current_uploaded_invoice in invoice_options:
            default_index = invoice_options.index(current_uploaded_invoice)

        selected_invoice_for_demo = st.selectbox(
            "Invoice to show end-to-end",
            invoice_options,
            index=default_index,
            key="selected_uploaded_invoice_walkthrough",
        )

        st.caption(
            "For deeper history or older invoices, open AP Agent Monitor. "
            "This page is for the single-invoice client walkthrough."
        )
        render_single_invoice_walkthrough(selected_invoice_for_demo)

# ===================================
# STRUCTURED INGESTION
# ===================================

elif selected_module == "Reference Data Sync (PO/GRN API)":
    st.header("Reference Data Sync")

    st.info(
        "Invoices are no longer synced from the API. "
        "Invoices enter through PDF/Image upload or Manual Invoice Entry. "
        "This page syncs only PO and GRN reference data."
    )

    st.write(
        """
        Sync structured reference records from:
        - SAP Purchase Orders
        - SAP GRNs
        """
    )

    if st.button("Start Structured Sync"):
        with st.spinner("Running structured ingestion..."):
            try:
                result = sync_structured_sources()
                st.write(result)

                status = result.get("status", "failed")
                if status == "success":
                    st.success("Structured ingestion completed.")
                    details = result.get("details", {})

                    col1, col2, col3 = st.columns(3)

                    with col1:
                        st.metric(
                            "API Invoices Synced",
                            details.get("invoice_count", 0)
                        )
                        st.caption(
                            "Expected value is 0. "
                            "Invoices are upload/manual-entry only."
                        )

                    with col2:
                        st.metric(
                            "PO Records Synced",
                            details.get("po_count", 0)
                        )

                    with col3:
                        st.metric(
                            "GRN Records Synced",
                            details.get("grn_count", 0)
                        )

                    st.info(
                        f"Total Sync Time: {result.get('total_time_sec', 'N/A')} sec"
                    )
                else:
                    st.error(
                        f"Sync Failed: {result.get('error', result.get('message', 'Unknown error'))}"
                    )
            except Exception as e:
                st.exception(e)


# ===================================
# INVOICE PROCESSING
# ===================================

elif selected_module == "Invoice Processing (PDF/Image)":
    st.header("Invoice OCR & AI Extraction")

    uploaded_file = st.file_uploader("Upload Invoice", type=["pdf", "png", "jpg", "jpeg"])

    if uploaded_file:
        st.success(f"Uploaded: {uploaded_file.name}")

        if st.button("Process Invoice"):
            with st.spinner("Running OCR + AI extraction..."):
                try:
                    saved_file_path, artifact_bundle = save_uploaded_file(
                        uploaded_file
                    )
                    result = process_invoice_pipeline(
                        saved_file_path,
                        artifact_bundle=artifact_bundle,
                    )
                    st.write(result)

                    status = result.get("status", "failed")
                    if status == "success":
                        st.success("Invoice processed successfully.")

                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("OCR Time", f"{result.get('ocr_time_sec', 'N/A')} sec")
                        with col2:
                            st.metric("Groq Time", f"{result.get('groq_time_sec', 'N/A')} sec")
                        with col3:
                            st.metric("Total Time", f"{result.get('total_time_sec', 'N/A')} sec")

                        st.subheader("Extracted Invoice Data")
                        st.json(result.get("parsed_json", {}))
                    else:
                        st.error(
                            f"Processing Failed: {result.get('error', result.get('message', 'Unknown error'))}"
                        )
                except Exception as e:
                    st.exception(e)

# ===================================
# AP AGENT MONITOR
# ===================================

elif selected_module == "AP Agent Monitor":

    st.header("AP Agent Processing Monitor")

    st.write(
        """
        This view shows what happened after invoices entered the AP Agent workflow:
        - Posted invoices
        - Exception invoices
        - Failed validation rules
        - Posting attempts
        - Agent workflow events
        """
    )

    if not ap_agent_db_exists():

        st.warning(
            "The AP Agent database is unavailable or its schema has not been initialized. "
            "For AWS/RDS, run scripts/test_rds_connection.py, "
            "scripts/init_rds_schema.py and scripts/check_rds_schema.py."
        )

    else:

        summary_df = load_ap_agent_summary()

        if summary_df.empty:

            st.info(
                "No AP Agent records found yet."
            )

        else:

            st.subheader("AP Agent Status Summary")

            status_counts = {
                row["status"]: row["total"]
                for _, row in summary_df.iterrows()
            }

            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.metric(
                    "Posted",
                    status_counts.get("POSTED", 0)
                )

            with col2:
                st.metric(
                    "Exceptions",
                    status_counts.get("EXCEPTION_IDENTIFIED", 0)
                )

            with col3:
                st.metric(
                    "Extracted",
                    status_counts.get("EXTRACTED", 0)
                )

            with col4:
                st.metric(
                    "Total Agent Records",
                    int(summary_df["total"].sum())
                )

            st.dataframe(
                summary_df,
                use_container_width=True
            )

        st.divider()

        st.subheader("AP Agent Invoice Status")

        limit = st.number_input(
            "Rows to show",
            min_value=10,
            max_value=500,
            value=50,
            step=10
        )

        agent_df = load_ap_agent_invoices(
            limit=limit
        )

        if agent_df.empty:

            st.info(
                "No AP Agent invoice records available."
            )

        else:

            agent_df.index = agent_df.index + 1
            agent_df.index.name = "R.no"

            st.dataframe(
                agent_df,
                use_container_width=True
            )

            st.divider()

            st.subheader("Invoice Drilldown")

            invoice_options = agent_df["invoice_number"].dropna().unique().tolist()

            selected_invoice = st.selectbox(
                "Select Invoice",
                invoice_options
            )

            if selected_invoice:

                st.markdown(
                    f"### Selected Invoice: `{selected_invoice}`"
                )

                selected_row = agent_df[
                    agent_df["invoice_number"] == selected_invoice
                ]

                st.write(
                    selected_row
                )

                st.subheader("Validation Results")

                validation_df = load_ap_agent_validation_results(
                    selected_invoice
                )

                if validation_df.empty:
                    st.info("No validation results found.")
                else:
                    st.dataframe(
                        validation_df,
                        use_container_width=True
                    )
                st.subheader("Email / Communication")

                communication_df = load_ap_agent_communications(
                    selected_invoice
                )

                if communication_df.empty:

                    st.info(
                        "No email communication created for this invoice. "
                        "Clean posted invoices normally do not generate emails."
                    )

                else:

                    latest_email = communication_df.iloc[0]

                    e1, e2, e3 = st.columns(3)

                    with e1:
                        st.metric(
                            "Email Status",
                            latest_email.get("status", "—")
                        )

                    with e2:
                        st.metric(
                            "Recipient",
                            latest_email.get("recipient", "—")
                        )

                    with e3:
                        st.metric(
                            "Direction",
                            latest_email.get("direction", "—")
                        )

                    st.dataframe(
                        communication_df[
                            [
                                "created_at",
                                "direction",
                                "recipient",
                                "subject",
                                "status",
                                "smtp_message_id",
                            ]
                        ],
                        use_container_width=True,
                    )

                    st.markdown("### Email Message")

                    for _, communication in communication_df.iterrows():

                        st.markdown(
                            f"**{communication.get('subject', '')}**"
                        )

                        st.caption(
                            f"To: {communication.get('recipient', 'Not configured')} "
                            f"· Status: {communication.get('status', '')} "
                            f"· Created: {communication.get('created_at', '')}"
                        )

                        st.code(
                            communication.get("body", ""),
                            language="text",
                        )

                        st.divider()

                
                st.subheader("Workflow Events")

                events_df = load_ap_agent_events(
                    selected_invoice
                )

                if events_df.empty:
                    st.info("No workflow events found.")
                else:
                    st.dataframe(
                        events_df,
                        use_container_width=True
                    )
# ===================================
# MANUAL DATA ENTRY
# ===================================

elif selected_module == "Test Data Setup (Manual Invoice + PO/GRN API)":
    st.header("Test Data Setup")

    st.info(
        "Manual invoice entry now writes directly to invoice_master "
        "and triggers AP Agent. It does not create a source invoice API record. "
        "PO and GRN entries still use the mock SAP API for reference data setup."
    )

    tab1, tab2, tab3 = st.tabs(
        [
            "Create Manual Invoice",
            "Create PO",
            "Create GRN",
        ]
    )

    # ===================================
    # CREATE INVOICE
    # ===================================
    with tab1:
        try:
            st.subheader("Create Manual Invoice")

            col1, col2 = st.columns(2)
            with col1:
                invoice_number = st.text_input("Invoice Number", key="invoice_number_input")
                po_number = st.text_input("Related PO Number", key="invoice_po_number_input")
                vendor_name = st.text_input("Vendor Name", key="invoice_vendor_name_input")
            with col2:
                invoice_date = st.date_input("Invoice Date", key="invoice_date_input")
                currency = st.selectbox(
                    "Currency",
                    options=CURRENCY_OPTIONS,
                    index=0,
                    key="invoice_currency_input",
                )
                payment_status = st.selectbox(
                    "Payment Status",
                    options=["Pending", "Paid", "Rejected", "Overdue"],
                    key="invoice_payment_status_input",
                )

            invoice_subtotal, invoice_line_items = render_line_items_editor(
                state_key="invoice_line_items",
                prefix="invoice",
                title="Invoice Line Items",
                qty_label="Quantity",
                qty_min=1,
            )

            st.subheader("Invoice Totals")
            vat_percent = st.number_input(
                "VAT %",
                min_value=0.0,
                max_value=100.0,
                value=18.0,
                key="invoice_vat_percent_input",
            )
            tax_amount = invoice_subtotal * (vat_percent / 100)
            document_total = invoice_subtotal + tax_amount

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Subtotal", f"{invoice_subtotal:.2f} {currency}")
            with col2:
                st.metric("Tax Amount", f"{tax_amount:.2f} {currency}")
            with col3:
                st.metric("Total", f"{document_total:.2f} {currency}")

            if st.button("Create Invoice", key="create_invoice_button"):
                try:
                    payload = {
                        "document_type": "invoice",
                        "invoice_number": invoice_number,
                        "po_number": po_number,
                        "vendor_name": vendor_name,
                        "invoice_date": str(invoice_date),
                        "currency": currency,
                        "document_subtotal": invoice_subtotal,
                        "tax_amount": tax_amount,
                        "vat_percent": vat_percent,
                        "document_total": document_total,
                        "amount": document_total,
                        "payment_status": payment_status,
                        "line_items": invoice_line_items,
                        "last_modified": datetime.now().isoformat(),
                    }

                    if not invoice_number:
                        st.error("Invoice Number is required.")
                        st.stop()

                    if not po_number:
                        st.error("Related PO Number is required.")
                        st.stop()

                    if not vendor_name:
                        st.error("Vendor Name is required.")
                        st.stop()

                    result = save_manual_invoice_to_master(
                        payload
                    )

                    st.success(
                        "Manual invoice saved to invoice_master."
                    )

                    st.json(result)

                    if result.get("ap_agent_trigger_error"):
                        st.warning(
                            "Invoice was saved, but AP Agent trigger failed. "
                            "Make sure AP Agent is running on port 8000, then trigger processing."
                        )
                        st.code(
                            result.get("ap_agent_trigger_error"),
                            language="text",
                        )
                    else:
                        st.success(
                            "AP Agent trigger completed."
                        )
                except Exception as e:
                    st.exception(e)
        except Exception as invoice_exception:
            st.error(f"Error in Invoice creation form: {invoice_exception}")

    # ===================================
    # CREATE PO
    # ===================================
    with tab2:
        try:
            st.subheader("Create Mock Purchase Order")

            col1, col2 = st.columns(2)
            with col1:
                po_number = st.text_input("PO Number", key="po_number_input")
                po_vendor_name = st.text_input("Vendor Name", key="po_vendor_name_input")
                po_date = st.date_input("PO Date", key="po_date_input")
            with col2:
                po_currency = st.selectbox(
                    "Currency",
                    options=CURRENCY_OPTIONS,
                    index=0,
                    key="po_currency_input",
                )
                vat_percent = st.number_input("VAT %", min_value=0.0, max_value=100.0, value=18.0, key="po_vat_percent_input")
                po_status = st.selectbox("PO Status", options=["Open", "Closed", "Cancelled", "Partially Received"], key="po_status_input")

            po_subtotal, po_line_items = render_line_items_editor(
                state_key="po_line_items",
                prefix="po",
                title="PO Line Items",
                qty_label="Quantity",
                qty_min=1,
            )

            st.subheader("PO Totals")
            tax_amount = po_subtotal * (vat_percent / 100)
            po_document_total = po_subtotal + tax_amount

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Subtotal", f"{po_subtotal:.2f} {po_currency}")
            with col2:
                st.metric("Tax Amount", f"{tax_amount:.2f} {po_currency}")
            with col3:
                st.metric("Total", f"{po_document_total:.2f} {po_currency}")

            if st.button("Create Purchase Order", key="create_po_button"):
                try:
                    payload = {
                        "document_type": "po",
                        "po_number": po_number,
                        "vendor_name": po_vendor_name,
                        "po_date": str(po_date),
                        "currency": po_currency,
                        "document_subtotal": po_subtotal,
                        "tax_amount": tax_amount,
                        "vat_percent": vat_percent,
                        "document_total": po_document_total,
                        "amount": po_document_total,
                        "po_status": po_status,
                        "line_items": po_line_items,
                        "last_modified": datetime.now().isoformat(),
                    }

                    response = requests.post(
                        f"{API_BASE_URL}/sap/po",
                        json=payload,
                        auth=(SAP_USERNAME, SAP_PASSWORD),
                        timeout=60,
                    )

                    if response.status_code == 200:
                        st.success("Purchase Order created successfully.")
                        st.json(response.json())
                    else:
                        st.error(f"Error creating PO: {response.text}")
                except Exception as e:
                    st.exception(e)
        except Exception as po_exception:
            st.error(f"Error in PO creation form: {po_exception}")

    # ===================================
    # CREATE GRN
    # ===================================
    with tab3:
        try:
            st.subheader("Create Mock GRN")

            col1, col2 = st.columns(2)
            with col1:
                gr_number = st.text_input("GRN Number", key="grn_number_input")
                po_number = st.text_input("Related PO Number", key="grn_po_number_input")
                vendor_name = st.text_input("Vendor Name", key="grn_vendor_name_input")
            with col2:
                gr_date = st.date_input("GRN Date", key="grn_date_input")
                currency = st.selectbox(
                    "Currency",
                    options=CURRENCY_OPTIONS,
                    index=0,
                    key="grn_currency_input",
                )
                gr_status = st.selectbox(
                    "GRN Status",
                    options=["Received", "Partially Received", "Pending"],
                    key="grn_status_input",
                )

            grn_subtotal, grn_line_items = render_line_items_editor(
                state_key="grn_line_items",
                prefix="grn",
                title="GRN Line Items",
                qty_label="Received Quantity",
                qty_min=0,
            )

            st.subheader("GRN Total")
            grn_document_total = grn_subtotal

            col1, col2 = st.columns(2)
            with col1:
                st.metric("Document Subtotal", f"{grn_subtotal:.2f} {currency}")
            with col2:
                st.metric("Total Amount", f"{grn_document_total:.2f} {currency}")

            if st.button("Create GRN", key="create_grn_button"):
                try:
                    payload = {
                        "document_type": "grn",
                        "gr_number": gr_number,
                        "po_number": po_number,
                        "vendor_name": vendor_name,
                        "gr_date": str(gr_date),
                        "currency": currency,
                        "document_subtotal": grn_subtotal,
                        "document_total": grn_document_total,
                        "amount": grn_document_total,
                        "gr_status": gr_status,
                        "line_items": grn_line_items,
                        "last_modified": datetime.now().isoformat(),
                    }

                    response = requests.post(
                        f"{API_BASE_URL}/sap/gr",
                        json=payload,
                        auth=(SAP_USERNAME, SAP_PASSWORD),
                        timeout=60,
                    )

                    if response.status_code == 200:
                        st.success("GRN created successfully.")
                        st.json(response.json())
                    else:
                        st.error(f"Error creating GRN: {response.text}")
                except Exception as e:
                    st.exception(e)
        except Exception as grn_exception:
            st.error(f"Error in GRN creation form: {grn_exception}")

# -----------------------------------
# ADMIN DATA MANAGER
# -----------------------------------

elif selected_module == "Admin Data Manager":

    st.header(
        "Admin Data Manager"
    )

    st.warning(
        "Danger Zone - Database Operations"
    )

    st.divider()

    # ==========================
    # RESET DEMO
    # ==========================

    st.subheader(
        "Reset Demo Environment"
    )

    st.info(
        "Use the first reset before a client demo. It clears invoice-flow data but keeps PO and GRN reference data. "
        "Use Master Reset only when you want to wipe everything."
    )

    reset_col1, reset_col2 = st.columns(2)
    with reset_col1:
        st.markdown("#### Reset invoice demo flow")
        st.success("Keeps PO and GRN data intact.")
        render_invoice_flow_reset_panel(location="admin")

    with reset_col2:
        st.markdown("#### Master reset")
        st.error("Deletes invoice, posted invoice, PO and GRN data.")
        render_master_reset_panel(location="admin")

    st.divider()

    # ==========================
    # DELETE SINGLE RECORDS
    # ==========================

    st.subheader(
        "Delete Single Records"
    )

    delete_invoice_no = st.text_input(
        "Invoice Number",
        key="delete_invoice"
    )

    if st.button(
        "Delete Invoice",
        key="delete_invoice_btn"
    ):

        if not database_settings.allow_destructive_master_reset:
            show_master_reset_blocked_message()
        elif not delete_invoice_no:
            st.warning("Enter an invoice number first.")
        else:
            try:
                delete_invoice(
                    delete_invoice_no
                )

                st.success(
                    "Invoice deleted."
                )

            except Exception as e:
                st.exception(e)

    delete_posted_invoice_no = st.text_input(
        "Posted Invoice Number",
        key="delete_posted_invoice"
    )

    if st.button(
        "Delete Posted Invoice",
        key="delete_posted_invoice_btn"
    ):

        if not database_settings.allow_destructive_master_reset:
            show_master_reset_blocked_message()
        elif not delete_posted_invoice_no:
            st.warning("Enter a posted invoice number first.")
        else:
            try:
                delete_posted_invoice(
                    delete_posted_invoice_no
                )

                st.success(
                    "Posted invoice deleted."
                )

            except Exception as e:
                st.exception(e)

    delete_po_no = st.text_input(
        "PO Number",
        key="delete_po"
    )

    if st.button(
        "Delete PO",
        key="delete_po_btn"
    ):

        if not database_settings.allow_destructive_master_reset:
            show_master_reset_blocked_message()
        elif not delete_po_no:
            st.warning("Enter a PO number first.")
        else:
            try:
                delete_po(
                    delete_po_no
                )

                st.success(
                    "PO deleted."
                )

            except Exception as e:
                st.exception(e)

    delete_grn_no = st.text_input(
        "GRN Number",
        key="delete_grn"
    )

    if st.button(
        "Delete GRN",
        key="delete_grn_btn"
    ):

        if not database_settings.allow_destructive_master_reset:
            show_master_reset_blocked_message()
        elif not delete_grn_no:
            st.warning("Enter a GRN number first.")
        else:
            try:
                delete_grn(
                    delete_grn_no
                )

                st.success(
                    "GRN deleted."
                )

            except Exception as e:
                st.exception(e)

    st.divider()

    # ==========================
    # CLEAR TABLES
    # ==========================

    st.subheader(
        "Clear Tables"
    )

    # Your clear table code
    col1, col2, col3, col4 = st.columns(4)

    with col1:

        if st.button(
            "Clear Invoice Table"
        ):

            if not database_settings.allow_destructive_master_reset:
                show_master_reset_blocked_message()
            else:
                clear_invoice_table()

                st.success(
                    "Invoice table cleared."
                )

    with col2:

        if st.button(
            "Clear PO Table"
        ):

            if not database_settings.allow_destructive_master_reset:
                show_master_reset_blocked_message()
            else:
                clear_po_table()

                st.success(
                    "PO table cleared."
                )

    with col3:

        if st.button(
            "Clear GRN Table"
        ):

            if not database_settings.allow_destructive_master_reset:
                show_master_reset_blocked_message()
            else:
                clear_grn_table()

                st.success(
                    "GRN table cleared."
                )

    with col4:

        if st.button(
            "Clear Posted Invoice Table"
        ):

            if not database_settings.allow_destructive_master_reset:
                show_master_reset_blocked_message()
            else:
                clear_posted_invoice_table()

                st.success(
                    "Posted invoice table cleared."
                )
    st.divider()

    # ==========================
    # KEEP LATEST ROWS
    # ==========================

    st.subheader(
        "Keep Latest Rows"
    )

    keep_count = st.number_input(

        "Rows to Keep",

        min_value=1,

        value=10
    )

    selected_table = st.selectbox(

        "Select Table",

        [
    "invoice_master",
    "sap_po_master",
    "sap_grn_master"
]
    )

    if st.button(
        "Apply Cleanup"
    ):

        if not database_settings.allow_destructive_master_reset:
            show_master_reset_blocked_message()
        else:
            try:
                keep_latest_rows(

                    selected_table,

                    keep_count
                )

                st.success(
                    "Cleanup completed."
                )

            except Exception as e:
                st.exception(e)
