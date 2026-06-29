
import json
import uuid
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st
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
from ap_database.master_repository import get_table_count, load_table_data
from ap_database.settings import settings as database_settings
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
    reset_demo_environment,

    init_db,
    get_conn,
    upsert_invoice,
)

from ingestion.ap_agent_trigger import trigger_ap_agent_process_new
from pipeline_runner import process_invoice_pipeline, sync_structured_sources

# -----------------------------------
# DATABASE PATH
# -----------------------------------

import os
from dotenv import load_dotenv

load_dotenv()

APP_ROOT = Path(__file__).resolve().parent
DEFAULT_AGENT_DB_PATH = APP_ROOT / "agent_app" / "ap_agent.db"
DEFAULT_INPUT_DIR = APP_ROOT / "unstructured_ingestion" / "unstructured_inputs"

SAP_API_PORT = int(os.getenv("SAP_API_PORT", "8001"))
API_BASE_URL = os.getenv(
    "MOCK_API_BASE_URL",
    f"http://127.0.0.1:{SAP_API_PORT}",
).rstrip("/")
SAP_USERNAME = os.getenv(
    "SAP_USERNAME",
    ""
)

SAP_PASSWORD = os.getenv(
    "SAP_PASSWORD",
    ""
)
# -----------------------------------
# INPUT DIRECTORY
# -----------------------------------

def resolve_path_from_env(env_name, default_path):
    configured = os.getenv(env_name)
    if not configured:
        return Path(default_path)
    path = Path(configured)
    if path.is_absolute():
        return path
    return APP_ROOT / path


INPUT_DIR = resolve_path_from_env(
    "UNSTRUCTURED_INPUT_DIR",
    DEFAULT_INPUT_DIR,
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


def clean_invoice_demo_run_only(clear_posted_references=False):
    result = {
        "invoice_master_cleared": False,
        "posted_references_cleared": False,
        "agent_db_deleted_paths": [],
        "agent_db_delete_errors": [],
        "uploaded_files_deleted_count": 0,
        "po_grn_preserved": True,
        "message": "",
    }

    if not database_settings.allow_destructive_master_reset:
        result["status"] = "blocked"
        result["message"] = (
            "Destructive cleanup is disabled. Enable the local demo reset "
            "flag before running cleanup."
        )
        return result

    try:
        init_db()
        clear_invoice_table()
        result["invoice_master_cleared"] = True
        if clear_posted_references:
            clear_posted_invoice_table()
            result["posted_references_cleared"] = True
    except Exception as exc:
        result["status"] = "failed"
        result["message"] = f"Master database cleanup failed: {exc}"
        result["master_cleanup_error"] = str(exc)
        return result

    agent_db_candidates = {
        resolve_path_from_env("AP_AGENT_DB_PATH", DEFAULT_AGENT_DB_PATH)
    }
    try:
        for path in APP_ROOT.rglob("ap_agent.db"):
            agent_db_candidates.add(path)
    except Exception as exc:
        result["agent_db_delete_errors"].append(
            {
                "path": str(APP_ROOT),
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )

    for path in sorted(agent_db_candidates, key=lambda item: str(item)):
        try:
            if path.exists():
                path.unlink()
                result["agent_db_deleted_paths"].append(str(path))
        except PermissionError as exc:
            result["agent_db_delete_errors"].append(
                {
                    "path": str(path),
                    "error_type": "PermissionError",
                    "message": (
                        "Stop the AP Agent API and run cleanup again."
                    ),
                    "raw_error": str(exc),
                }
            )
        except Exception as exc:
            result["agent_db_delete_errors"].append(
                {
                    "path": str(path),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )

    input_dirs = {
        resolve_path_from_env("UNSTRUCTURED_INPUT_DIR", DEFAULT_INPUT_DIR),
        DEFAULT_INPUT_DIR,
    }
    deleted_count = 0
    for input_dir in input_dirs:
        try:
            if not input_dir.exists():
                continue
            for path in input_dir.iterdir():
                if path.is_file() and path.suffix.lower() in {
                    ".pdf",
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".txt",
                }:
                    try:
                        path.unlink()
                        deleted_count += 1
                    except PermissionError as exc:
                        result["agent_db_delete_errors"].append(
                            {
                                "path": str(path),
                                "error_type": "PermissionError",
                                "message": str(exc),
                            }
                        )
        except Exception as exc:
            result["agent_db_delete_errors"].append(
                {
                    "path": str(input_dir),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )

    result["uploaded_files_deleted_count"] = deleted_count
    result["status"] = "success"
    result["message"] = (
        "Invoice run data cleared. PO and GRN reference data were preserved."
    )
    return result


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

# -----------------------------------
# DEMO SHELL HELPERS
# -----------------------------------

AGENT_API_PORT = int(os.getenv("AGENT_API_PORT", "8000"))
AP_AGENT_BASE_URL = os.getenv(
    "AGENT_API_BASE_URL",
    os.getenv("AP_AGENT_BASE_URL", f"http://127.0.0.1:{AGENT_API_PORT}"),
).rstrip("/")


def render_section_help(text):
    st.info(text)


def render_status_badge(status):
    normalized = str(status or "UNKNOWN")
    palette = {
        "ok": ("#027a48", "#ecfdf3"),
        "healthy": ("#027a48", "#ecfdf3"),
        "available": ("#027a48", "#ecfdf3"),
        "unavailable": ("#b42318", "#fef3f2"),
        "error": ("#b42318", "#fef3f2"),
        "unknown": ("#344054", "#f2f4f7"),
    }
    color, background = palette.get(normalized.lower(), ("#344054", "#f2f4f7"))
    st.markdown(
        f"<span class='status-badge' style='color:{color};background:{background};'>"
        f"{normalized}</span>",
        unsafe_allow_html=True,
    )


def render_info_card(title, value, help_text=None):
    st.markdown(
        f"""
        <div class="info-card">
            <div class="info-card-label">{title}</div>
            <div class="info-card-value">{value}</div>
            <div class="info-card-help">{help_text or ""}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def safe_dataframe(df, empty_message):
    if df is None or getattr(df, "empty", False):
        st.info(empty_message)
    else:
        st.dataframe(df, use_container_width=True)


def safe_table_count(table_name):
    try:
        return get_table_count(table_name)
    except Exception:
        return 0


def safe_load_table(table_name, limit=10):
    try:
        return load_table_data(table_name, limit=limit)
    except Exception as exc:
        st.error(f"{table_name} table error: {exc}")
        return None


def normalize_status_label(status):
    return str(status or "UNKNOWN").replace("_", " ").title()


def get_status_counts(summary_df=None):
    if summary_df is None:
        if not ap_agent_db_exists():
            return {}
        summary_df = load_ap_agent_summary()
    if summary_df is None or getattr(summary_df, "empty", False):
        return {}
    if "status" not in summary_df.columns or "total" not in summary_df.columns:
        return {}
    return {
        str(row["status"]): int(row["total"])
        for _, row in summary_df.iterrows()
    }


def render_metric_cards(cards, columns=4):
    rows = [cards[index:index + columns] for index in range(0, len(cards), columns)]
    for row in rows:
        cols = st.columns(len(row))
        for col, card in zip(cols, row):
            with col:
                render_info_card(
                    card.get("title"),
                    card.get("value"),
                    card.get("help_text"),
                )


def business_dataframe(df, columns, empty_message):
    if df is None or getattr(df, "empty", False):
        st.info(empty_message)
        return
    available_columns = [column for column in columns if column in df.columns]
    if not available_columns:
        st.info(empty_message)
        return
    st.dataframe(df[available_columns], use_container_width=True)


def current_row_value(row, candidate_columns, default="—"):
    for column in candidate_columns:
        try:
            value = row.get(column, None)
        except AttributeError:
            value = None
        if value is not None and str(value) not in {"", "nan", "NaT", "None"}:
            return value
    return default


def validation_contains(validation_df, tokens):
    if validation_df is None or getattr(validation_df, "empty", False):
        return False
    token_list = [token.upper() for token in tokens]
    for _, row in validation_df.iterrows():
        haystack = " ".join(
            str(row.get(column, ""))
            for column in ["rule_code", "rule_name", "message"]
            if column in validation_df.columns
        ).upper()
        if any(token in haystack for token in token_list):
            return True
    return False


def validation_group_outcome(validation_df, tokens):
    if validation_df is None or getattr(validation_df, "empty", False):
        return "Not started"
    token_list = [token.upper() for token in tokens]
    matched = []
    for _, row in validation_df.iterrows():
        haystack = " ".join(
            str(row.get(column, ""))
            for column in ["rule_code", "rule_name", "message"]
            if column in validation_df.columns
        ).upper()
        if any(token in haystack for token in token_list):
            matched.append(row)
    if not matched:
        return "Not started"
    for row in matched:
        severity = str(row.get("severity", "")).upper()
        passed = row.get("passed")
        if severity == "ERROR" and str(passed).lower() in {"false", "0", "no"}:
            return "Failed"
    for row in matched:
        severity = str(row.get("severity", "")).upper()
        passed = row.get("passed")
        if severity in {"WARNING", "WARN"} or str(passed).lower() in {"false", "0", "no"}:
            return "Warning / Action Required"
    return "Passed"


def event_contains(events_df, tokens):
    if events_df is None or getattr(events_df, "empty", False):
        return False
    token_list = [token.upper() for token in tokens]
    for _, row in events_df.iterrows():
        haystack = " ".join(
            str(row.get(column, ""))
            for column in ["event_type", "agent_name", "message"]
            if column in events_df.columns
        ).upper()
        if any(token in haystack for token in token_list):
            return True
    return False


def render_stage_cards(stages):
    status_help = {
        "Passed": "Complete",
        "Warning / Action Required": "Needs action",
        "Failed": "Failed",
        "Skipped": "Not required",
        "Not started": "Waiting",
    }
    cards = [
        {
            "title": stage,
            "value": status,
            "help_text": status_help.get(status, ""),
        }
        for stage, status in stages
    ]
    render_metric_cards(cards, columns=3)


def extracted_value(payload, candidate_keys, default="—"):
    payload = payload or {}
    for key in candidate_keys:
        value = payload.get(key)
        if value is not None and str(value) != "":
            return value
    return default


def normalize_parsed_invoice_payload(parsed_json):
    parsed_json = parsed_json or {}
    if isinstance(parsed_json, dict):
        for key in ["invoice", "invoice_data", "parsed_invoice", "data"]:
            nested = parsed_json.get(key)
            if isinstance(nested, dict):
                return nested
    return parsed_json if isinstance(parsed_json, dict) else {}


def render_extracted_business_fields(parsed_json):
    payload = normalize_parsed_invoice_payload(parsed_json)
    fields = [
        ("Invoice number", ["invoice_number", "invoice_no", "number"]),
        ("Vendor", ["vendor_name", "supplier_name", "vendor"]),
        ("Vendor number", ["vendor_number", "supplier_number"]),
        ("PO number", ["po_number", "purchase_order", "po_no"]),
        ("Invoice date", ["invoice_date", "date"]),
        ("Currency", ["currency"]),
        ("Subtotal", ["document_subtotal", "subtotal"]),
        ("Tax amount", ["tax_amount", "tax"]),
        ("VAT %", ["vat_percent", "tax_rate", "vat_rate"]),
        ("Total", ["document_total", "total_amount", "amount"]),
        ("Payment terms", ["payment_terms", "terms"]),
    ]
    cards = [
        {
            "title": label,
            "value": extracted_value(payload, keys),
            "help_text": "",
        }
        for label, keys in fields
    ]
    render_metric_cards(cards, columns=4)
    line_items = extracted_value(
        payload,
        ["line_items", "items", "lines"],
        default=[],
    )
    if isinstance(line_items, list) and line_items:
        st.subheader("Line Items")
        st.dataframe(line_items, use_container_width=True)


def render_invoice_journey_tracker(selected_invoice, validation_df, communication_df, events_df):
    workflow_status = str(
        current_row_value(selected_invoice, ["status", "workflow_status", "agent_status"], "")
    )
    posting_status = str(current_row_value(selected_invoice, ["posting_status"], ""))
    payment_status = str(current_row_value(selected_invoice, ["payment_status"], "UNKNOWN"))
    has_exception = workflow_status == "EXCEPTION_IDENTIFIED"
    stages = [
        ("Invoice received", "Passed"),
        ("OCR / AI extraction completed", "Passed"),
        (
            "Extraction quality checked",
            "Warning / Action Required"
            if workflow_status == "EXTRACTION_REVIEW_REQUIRED"
            else "Failed"
            if workflow_status == "EXTRACTION_FAILED"
            else "Passed",
        ),
        ("PO checked", validation_group_outcome(validation_df, ["PO"])),
        ("GRN checked", validation_group_outcome(validation_df, ["GRN"])),
        ("Vendor checked", validation_group_outcome(validation_df, ["VENDOR"])),
        ("Duplicate checked", validation_group_outcome(validation_df, ["DUP"])),
        ("Financial totals checked", validation_group_outcome(validation_df, ["FIN", "AMOUNT", "PRICE", "TOTAL"])),
        ("Tax checked", validation_group_outcome(validation_df, ["TAX", "VAT", "GST"])),
        ("Payment terms checked", validation_group_outcome(validation_df, ["PAYMENT", "TERMS"])),
        ("Date sequence checked", validation_group_outcome(validation_df, ["DATE"])),
        ("PO / GRN consumption checked", validation_group_outcome(validation_df, ["CONSUMPTION", "LEDGER", "CUMULATIVE"])),
        (
            "Exception created or invoice ready",
            "Warning / Action Required"
            if workflow_status == "EXCEPTION_IDENTIFIED"
            else "Passed"
            if workflow_status in {"READY_FOR_POSTING", "POSTED"} or posting_status == "POSTED"
            else "Not started",
        ),
        (
            "Communication drafted if required",
            "Passed"
            if communication_df is not None and not communication_df.empty
            else "Not started"
            if has_exception
            else "Skipped",
        ),
        (
            "Response received if required",
            "Passed"
            if event_contains(events_df, ["RESPONSE", "EVIDENCE", "FIELD_UPDATED"])
            else "Skipped"
            if not has_exception
            else "Not started",
        ),
        (
            "Controlled recheck completed if required",
            "Passed"
            if event_contains(events_df, ["RECHECK"])
            else "Skipped"
            if not has_exception
            else "Not started",
        ),
        (
            "Posted if eligible",
            "Passed"
            if workflow_status == "POSTED" or posting_status == "POSTED"
            else "Not started",
        ),
        ("Payment status tracked separately", str(payment_status)),
        (
            "Audit trail recorded",
            "Passed"
            if events_df is not None and not events_df.empty
            else "Not started",
        ),
    ]
    render_stage_cards(stages)


def api_health(url):
    try:
        response = requests.get(f"{url.rstrip('/')}/health", timeout=3)
        if response.status_code < 400:
            return "Healthy"
        return "Unavailable"
    except requests.RequestException:
        return "Unavailable"


def render_technical_details(label, payload):
    with st.expander(label):
        st.json(payload or {})

# -----------------------------------
# PAGE CONFIG
# -----------------------------------

st.set_page_config(page_title="AP Automation Platform", layout="wide")
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.5rem;}
    .info-card {
        background: #ffffff;
        border: 1px solid #e4e7ec;
        border-radius: 8px;
        padding: 0.85rem 1rem;
        min-height: 88px;
        margin-bottom: 0.75rem;
    }
    .info-card-label {
        color: #667085;
        font-size: 0.85rem;
        margin-bottom: 0.35rem;
    }
    .info-card-value {
        color: #1d2939;
        font-size: 1.15rem;
        font-weight: 650;
        line-height: 1.3;
        overflow-wrap: anywhere;
        word-break: break-word;
    }
    .info-card-help {
        color: #667085;
        font-size: 0.8rem;
        margin-top: 0.35rem;
        line-height: 1.3;
        overflow-wrap: anywhere;
    }
    .status-badge {
        display: inline-block;
        border-radius: 999px;
        padding: 0.18rem 0.55rem;
        font-size: 0.8rem;
        font-weight: 650;
        margin-bottom: 0.3rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

def render_dashboard_control_tower():
    st.title("Dashboard / Control Tower")
    render_section_help(
        "This page shows the overall AP automation status: invoice intake, validation progress, exceptions, posting, and reference data readiness."
    )

    invoice_count = safe_table_count("invoice_master")
    po_count = safe_table_count("sap_po_master")
    grn_count = safe_table_count("sap_grn_master")
    posted_reference_count = safe_table_count("sap_posted_invoice_master")

    st.subheader("Demo Readiness")
    render_metric_cards(
        [
            {
                "title": "Invoice records",
                "value": invoice_count,
                "help_text": "Invoices received through upload or test entry",
            },
            {
                "title": "PO records",
                "value": po_count,
                "help_text": "Purchase orders available for validation",
            },
            {
                "title": "GRN records",
                "value": grn_count,
                "help_text": "Goods receipts available for matching",
            },
            {
                "title": "Posted invoice references",
                "value": posted_reference_count,
                "help_text": "Used for duplicate / already-posted checks",
            },
        ],
    )

    st.subheader("AP Agent Workflow Summary")
    status_counts = {}
    total_agent_invoices = 0
    if not ap_agent_db_exists():
        st.info("AP Agent records will appear after an invoice is processed.")
    else:
        summary_df = load_ap_agent_summary()
        status_counts = get_status_counts(summary_df)
        if not status_counts:
            st.info("AP Agent records will appear after an invoice is processed.")
        else:
            total_agent_invoices = sum(status_counts.values())
            render_metric_cards(
                [
                    {
                        "title": "Total Agent Invoices",
                        "value": total_agent_invoices,
                        "help_text": "Invoices tracked by the AP Agent",
                    },
                    {
                        "title": "Posted",
                        "value": status_counts.get("POSTED", 0),
                        "help_text": "Invoices posted successfully",
                    },
                    {
                        "title": "Ready for Posting",
                        "value": status_counts.get("READY_FOR_POSTING", 0),
                        "help_text": "Invoices clean and ready to post",
                    },
                    {
                        "title": "Exceptions",
                        "value": status_counts.get("EXCEPTION_IDENTIFIED", 0),
                        "help_text": "Invoices needing business action",
                    },
                    {
                        "title": "Extraction Review",
                        "value": status_counts.get("EXTRACTION_REVIEW_REQUIRED", 0),
                        "help_text": "Invoices needing extraction review",
                    },
                    {
                        "title": "Extraction Failed",
                        "value": status_counts.get("EXTRACTION_FAILED", 0),
                        "help_text": "Invoices not extracted reliably",
                    },
                    {
                        "title": "Posting Failed",
                        "value": status_counts.get("POSTING_FAILED", 0),
                        "help_text": "Invoices that failed posting",
                    },
                ],
            )

    st.subheader("Where Invoices Are Stuck")
    stuck_meanings = {
        "EXTRACTION_REVIEW_REQUIRED": "Invoice was extracted but needs review before AP validation.",
        "EXTRACTION_FAILED": "Invoice could not be extracted reliably.",
        "VALIDATION_IN_PROGRESS": "Invoice is still being checked by AP controls.",
        "EXCEPTION_IDENTIFIED": "Invoice failed one or more blocking AP controls.",
        "POSTING_FAILED": "Invoice passed validation but posting failed.",
        "REPROCESS_REQUESTED": "Invoice is waiting for controlled recheck.",
        "REPROCESS_FAILED": "Recheck was attempted but invoice still failed.",
    }
    stuck_cards = [
        {
            "title": normalize_status_label(status),
            "value": count,
            "help_text": stuck_meanings[status],
        }
        for status, count in (
            (status, status_counts.get(status, 0))
            for status in stuck_meanings
        )
        if count > 0
    ]
    if stuck_cards:
        render_metric_cards(stuck_cards)
    else:
        st.success(
            "No stuck invoices currently. All processed invoices are either posted, ready, or awaiting next upload."
        )

    st.subheader("Process Funnel")
    received_count = max(invoice_count, total_agent_invoices)
    extracted_count = sum(
        status_counts.get(status, 0)
        for status in [
            "EXTRACTED",
            "READY_FOR_POSTING",
            "EXCEPTION_IDENTIFIED",
            "POSTED",
        ]
    )
    exception_ready_count = (
        status_counts.get("EXCEPTION_IDENTIFIED", 0)
        + status_counts.get("READY_FOR_POSTING", 0)
    )
    render_metric_cards(
        [
            {
                "title": "Received",
                "value": received_count,
                "help_text": "Invoices available to the AP process",
            },
            {
                "title": "Extracted",
                "value": extracted_count,
                "help_text": "Invoices extracted and ready for workflow checks",
            },
            {
                "title": "Exception / Ready",
                "value": exception_ready_count,
                "help_text": "Invoices needing action or ready to post",
            },
            {
                "title": "Posted",
                "value": status_counts.get("POSTED", 0),
                "help_text": "Invoices posted by the AP Agent",
            },
        ],
    )

    st.subheader("Recent Invoice Status")
    if ap_agent_db_exists():
        agent_df = load_ap_agent_invoices(limit=20)
        business_dataframe(
            agent_df,
            [
                "invoice_number",
                "vendor_name",
                "po_number",
                "total_amount",
                "status",
                "posting_status",
                "payment_status",
                "exception_category",
                "updated_at",
            ],
            "No AP Agent invoice records available yet.",
        )
    else:
        st.info("AP Agent records will appear after an invoice is processed.")

    st.subheader("Reference Data Tables")
    tabs = st.tabs(
        [
            "Invoices",
            "POs",
            "GRNs",
            "Posted Invoice References",
        ]
    )
    with tabs[0]:
        invoice_df = safe_load_table("invoice_master", limit=10)
        safe_dataframe(invoice_df, "No invoice records found.")
    with tabs[1]:
        po_df = safe_load_table("sap_po_master", limit=10)
        safe_dataframe(po_df, "No purchase order records found.")
    with tabs[2]:
        grn_df = safe_load_table("sap_grn_master", limit=10)
        safe_dataframe(grn_df, "No GRN records found.")
    with tabs[3]:
        posted_invoice_df = safe_load_table("sap_posted_invoice_master", limit=10)
        safe_dataframe(posted_invoice_df, "No posted invoice references found.")

    st.subheader("How to Use This Dashboard")
    st.info(
        "Use this page before a demo to confirm PO/GRN data is loaded. After processing invoices, use it to see which invoices posted successfully and which invoices need action."
    )


def render_invoice_journey():
    st.title("Invoice Journey")
    render_section_help(
        "Use this page to upload one invoice and follow what happens from document intake through extraction, AP validation, exception handling, recheck, posting, and audit."
    )

    st.subheader("Demo Readiness Check")
    po_count = safe_table_count("sap_po_master")
    grn_count = safe_table_count("sap_grn_master")
    agent_available = ap_agent_db_exists()
    render_metric_cards(
        [
            {
                "title": "PO records available",
                "value": po_count,
                "help_text": "Reference data for PO matching",
            },
            {
                "title": "GRN records available",
                "value": grn_count,
                "help_text": "Reference data for goods receipt matching",
            },
            {
                "title": "AP Agent status",
                "value": "Available" if agent_available else "Waiting",
                "help_text": "Records appear after first invoice processing",
            },
        ],
        columns=3,
    )
    if po_count == 0:
        st.warning("Load or create PO reference data before testing PO matching.")
    if grn_count == 0:
        st.warning("Load or create GRN reference data before testing GRN matching.")
    if not agent_available:
        st.info("AP Agent records will appear after the first invoice is processed.")

    st.subheader("Upload Invoice")
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
                    render_technical_details("Processing details", result)

                    status = result.get("status", "failed")
                    if status == "success":
                        st.success("Invoice uploaded and processed.")
                        parsed_payload = result.get("parsed_json", {})
                        normalized_payload = normalize_parsed_invoice_payload(parsed_payload)
                        invoice_number = extracted_value(
                            normalized_payload,
                            ["invoice_number", "invoice_no", "number"],
                            default=None,
                        )
                        if invoice_number:
                            st.session_state["last_processed_invoice_number"] = invoice_number

                        render_metric_cards(
                            [
                                {
                                    "title": "OCR time",
                                    "value": f"{result.get('ocr_time_sec', 'N/A')} sec",
                                    "help_text": "Document text extraction",
                                },
                                {
                                    "title": "AI extraction time",
                                    "value": f"{result.get('groq_time_sec', 'N/A')} sec",
                                    "help_text": "Structured invoice parsing",
                                },
                                {
                                    "title": "Total time",
                                    "value": f"{result.get('total_time_sec', 'N/A')} sec",
                                    "help_text": "End-to-end processing time",
                                },
                            ],
                            columns=3,
                        )

                        st.subheader("Extracted Invoice Data")
                        render_extracted_business_fields(parsed_payload)
                        render_technical_details("Technical details — extracted payload", parsed_payload)
                    else:
                        st.error(
                            f"Processing failed: {result.get('error', result.get('message', 'Unknown error'))}"
                        )
                        render_technical_details("Technical details — processing result", result)
                except Exception as e:
                    st.exception(e)

    st.subheader("Follow an Existing Invoice")
    if not ap_agent_db_exists():
        st.info("AP Agent records will appear after processing.")
        return

    agent_df = load_ap_agent_invoices(limit=100)
    if agent_df.empty or "invoice_number" not in agent_df.columns:
        st.info("No AP Agent invoice records are available yet.")
        return

    invoice_options = agent_df["invoice_number"].dropna().unique().tolist()
    if not invoice_options:
        st.info("No AP Agent invoice records are available yet.")
        return

    default_invoice = st.session_state.get("last_processed_invoice_number")
    selected_index = 0
    if default_invoice in invoice_options:
        selected_index = invoice_options.index(default_invoice)
    selected_invoice_number = st.selectbox(
        "Select invoice",
        invoice_options,
        index=selected_index,
        key="invoice_journey_selected_invoice",
    )
    selected_rows = agent_df[agent_df["invoice_number"] == selected_invoice_number]
    if selected_rows.empty:
        st.info("Selected invoice is no longer available.")
        return
    selected_invoice = selected_rows.iloc[0]

    validation_df = load_ap_agent_validation_results(selected_invoice_number)
    communication_df = load_ap_agent_communications(selected_invoice_number)
    events_df = load_ap_agent_events(selected_invoice_number)

    st.subheader("Selected Invoice")
    workflow_status = current_row_value(selected_invoice, ["status", "workflow_status", "agent_status"])
    posting_status = current_row_value(selected_invoice, ["posting_status"])
    payment_status = current_row_value(selected_invoice, ["payment_status"])
    render_metric_cards(
        [
            {
                "title": "Invoice number",
                "value": current_row_value(selected_invoice, ["invoice_number"]),
                "help_text": "",
            },
            {
                "title": "Vendor",
                "value": current_row_value(selected_invoice, ["vendor_name"]),
                "help_text": "",
            },
            {
                "title": "PO number",
                "value": current_row_value(selected_invoice, ["po_number"]),
                "help_text": "",
            },
            {
                "title": "Amount",
                "value": current_row_value(selected_invoice, ["total_amount", "document_total", "amount"]),
                "help_text": "",
            },
            {
                "title": "Workflow status",
                "value": workflow_status,
                "help_text": "Current AP Agent state",
            },
            {
                "title": "Posting status",
                "value": posting_status,
                "help_text": "Posting outcome, if attempted",
            },
            {
                "title": "Payment status",
                "value": payment_status,
                "help_text": "Tracked separately from posting",
            },
        ],
        columns=4,
    )

    st.subheader("Invoice Journey Tracker")
    render_invoice_journey_tracker(
        selected_invoice,
        validation_df,
        communication_df,
        events_df,
    )

    st.subheader("Extraction Quality")
    quality_values = [
        current_row_value(selected_invoice, ["extraction_quality_status"], default=None),
        current_row_value(selected_invoice, ["extraction_confidence"], default=None),
        current_row_value(selected_invoice, ["extraction_retry_count"], default=None),
        current_row_value(selected_invoice, ["extraction_review_reason"], default=None),
    ]
    if all(value is None for value in quality_values):
        st.info(
            "Extraction quality details are not available in the current AP Agent table, but validation and workflow status are shown below."
        )
    else:
        render_metric_cards(
            [
                {
                    "title": "Quality status",
                    "value": quality_values[0] or "—",
                    "help_text": "Extraction gate result",
                },
                {
                    "title": "Confidence",
                    "value": quality_values[1] or "—",
                    "help_text": "AI extraction confidence",
                },
                {
                    "title": "Retry count",
                    "value": quality_values[2] or "—",
                    "help_text": "Extraction retry attempts",
                },
                {
                    "title": "Review reason",
                    "value": quality_values[3] or "—",
                    "help_text": "Reason review is needed",
                },
            ],
        )

    st.subheader("AP Validation Summary")
    passed_controls = 0
    failed_blocking = 0
    warning_controls = 0
    if validation_df.empty:
        st.info("No validation results found for this invoice yet.")
    else:
        for _, row in validation_df.iterrows():
            severity = str(row.get("severity", "")).upper()
            passed = str(row.get("passed")).lower() in {"true", "1", "yes"}
            if severity in {"WARNING", "WARN"}:
                warning_controls += 1
            elif passed:
                passed_controls += 1
            else:
                failed_blocking += 1
        render_metric_cards(
            [
                {
                    "title": "Passed controls",
                    "value": passed_controls,
                    "help_text": "Controls that passed",
                },
                {
                    "title": "Failed blocking controls",
                    "value": failed_blocking,
                    "help_text": "Controls that block progress",
                },
                {
                    "title": "Warning/advisory controls",
                    "value": warning_controls,
                    "help_text": "Advisory or warning checks",
                },
            ],
            columns=3,
        )
        validation_groups = [
            ("PO", ["PO"]),
            ("GRN", ["GRN"]),
            ("Vendor", ["VENDOR"]),
            ("Duplicate", ["DUP"]),
            ("Financial", ["FIN", "AMOUNT", "PRICE", "TOTAL"]),
            ("Tax", ["TAX", "VAT", "GST"]),
            ("Payment Terms", ["PAYMENT", "TERMS"]),
            ("Date", ["DATE"]),
            ("Consumption", ["CONSUMPTION", "LEDGER", "CUMULATIVE"]),
            ("Other", []),
        ]
        tabs = st.tabs([group[0] for group in validation_groups])
        assigned_indexes = set()
        for tab, (group_name, tokens) in zip(tabs, validation_groups):
            with tab:
                if tokens:
                    matching_indexes = []
                    for index, row in validation_df.iterrows():
                        haystack = " ".join(
                            str(row.get(column, ""))
                            for column in ["rule_code", "rule_name", "message"]
                            if column in validation_df.columns
                        ).upper()
                        if any(token in haystack for token in tokens):
                            matching_indexes.append(index)
                            assigned_indexes.add(index)
                    group_df = validation_df.loc[matching_indexes]
                else:
                    group_df = validation_df.drop(index=list(assigned_indexes))
                business_dataframe(
                    group_df,
                    ["rule_code", "rule_name", "passed", "severity", "message"],
                    f"No {group_name.lower()} validation results.",
                )

    st.subheader("Exception / Status")
    status_text = str(workflow_status)
    status_messages = {
        "POSTED": "Invoice was posted. Payment status is tracked separately.",
        "READY_FOR_POSTING": "All blocking controls passed. Invoice is ready for posting.",
        "EXCEPTION_IDENTIFIED": "Invoice failed one or more blocking controls. Review failed rules and communication.",
        "EXTRACTION_REVIEW_REQUIRED": "Invoice extraction needs review before AP validation.",
        "EXTRACTION_FAILED": "Invoice could not be extracted reliably.",
        "POSTING_FAILED": "Invoice passed validation but posting failed.",
    }
    render_metric_cards(
        [
            {
                "title": "Current workflow status",
                "value": workflow_status,
                "help_text": "Current AP Agent status",
            },
            {
                "title": "What it means",
                "value": status_messages.get(status_text, "Review workflow events for latest status."),
                "help_text": "Business explanation",
            },
            {
                "title": "Recommended next action",
                "value": status_messages.get(status_text, "Review workflow events for latest status."),
                "help_text": "Next step",
            },
        ],
        columns=3,
    )

    st.subheader("Communication and Audit Preview")
    business_dataframe(
        communication_df,
        ["status", "recipient", "subject", "created_at", "direction"],
        "No communication has been drafted for this invoice.",
    )
    if communication_df is not None and not communication_df.empty and "body" in communication_df.columns:
        latest_body = communication_df.iloc[0].get("body", "")
        with st.expander("Latest communication body"):
            st.code(str(latest_body), language="text")
    business_dataframe(
        events_df.head(10) if events_df is not None and not events_df.empty else events_df,
        ["created_at", "event_type", "agent_name", "message"],
        "No audit events found for this invoice.",
    )

    st.subheader("Posting and Payment")
    render_metric_cards(
        [
            {
                "title": "Posting status",
                "value": posting_status,
                "help_text": "Whether the invoice was posted",
            },
            {
                "title": "Payment status",
                "value": payment_status,
                "help_text": "ERP/payment-run state",
            },
        ],
        columns=2,
    )
    st.info(
        "Posting confirms invoice posting. Payment status is separate and should come from ERP/payment-run data. The demo should not mark an invoice as paid just because it was posted."
    )

    st.subheader("Technical Details")
    render_technical_details("Selected invoice raw row", selected_invoice.to_dict())
    render_technical_details(
        "Validation raw data",
        validation_df.to_dict(orient="records") if validation_df is not None else {},
    )
    render_technical_details(
        "Communication raw data",
        communication_df.to_dict(orient="records") if communication_df is not None else {},
    )
    render_technical_details(
        "Events raw data",
        events_df.to_dict(orient="records") if events_df is not None else {},
    )


def render_ap_agent_workbench():
    render_section_help(
        "Use this page to select any invoice and inspect everything the AP Agent did for that invoice."
    )

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
            "AP Agent database not found yet. "
            "Run invoice processing first so agent_app/ap_agent.db is created."
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


def render_response_recheck_demo():
    render_section_help(
        "Use this page to simulate supplier/procurement responses and show how controlled recheck works."
    )
    if not ap_agent_db_exists():
        st.warning("AP Agent database not found yet. Run invoice processing first so agent_app/ap_agent.db is created.")
        return
    agent_df = load_ap_agent_invoices(limit=100)
    if agent_df.empty:
        st.info("No AP Agent invoice records available yet.")
        return
    invoice_options = agent_df["invoice_number"].dropna().unique().tolist()
    selected_invoice = st.selectbox("Select Invoice", invoice_options, key="response_recheck_invoice")
    if selected_invoice:
        st.markdown(f"### Selected Invoice: `{selected_invoice}`")
        selected_row = agent_df[agent_df["invoice_number"] == selected_invoice]
        st.dataframe(selected_row, use_container_width=True)
        st.info("Full response capture and controlled recheck controls will be added in the next step. Use AP Agent Workbench for the current invoice drilldown.")

def render_reference_data_test_setup():
    render_section_help(
        "Use this page to prepare PO and GRN reference data before processing invoices."
    )
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
                render_technical_details("Sync details", result)

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
                currency = st.selectbox("Currency", options=["INR", "USD", "EUR", "GBP"], index=0, key="invoice_currency_input")
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

                    render_technical_details("Invoice save details", result)

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
                po_currency = st.selectbox("Currency", options=["INR", "USD", "EUR", "GBP"], index=0, key="po_currency_input")
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
                        render_technical_details("PO API response", response.json())
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
                currency = st.selectbox("Currency", options=["INR", "USD", "EUR", "GBP"], index=0, key="grn_currency_input")
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
                        render_technical_details("GRN API response", response.json())
                    else:
                        st.error(f"Error creating GRN: {response.text}")
                except Exception as e:
                    st.exception(e)
        except Exception as grn_exception:
            st.error(f"Error in GRN creation form: {grn_exception}")

    # -----------------------------------
    # ADMIN DATA MANAGER
    # -----------------------------------


def render_admin_demo_cleanup():
    render_section_help(
        "Use this page to clean demo invoice data and reset the environment safely."
    )

    st.header(
        "Admin Data Manager"
    )

    st.warning(
        "Danger Zone - Database Operations"
    )

    st.divider()

    st.subheader("Recommended Demo Cleanup")
    st.info(
        "Use this before a client demo when you want to reuse PO/GRN reference data but clear prior invoice runs, AP Agent history, uploaded invoice files, validations, emails, posting attempts, and audit events."
    )
    confirm_invoice_cleanup = st.checkbox(
        "I understand this will clear invoice run data but preserve PO and GRN reference data.",
        key="confirm_invoice_demo_run_cleanup",
    )
    clear_posted_references = st.checkbox(
        "Also clear posted invoice reference table",
        value=False,
        key="clear_posted_references_with_invoice_cleanup",
    )
    if st.button(
        "Clean Invoice Demo Run Only",
        key="clean_invoice_demo_run_only_btn",
        disabled=not confirm_invoice_cleanup,
    ):
        cleanup_result = clean_invoice_demo_run_only(
            clear_posted_references=clear_posted_references
        )
        if cleanup_result.get("status") == "blocked":
            show_master_reset_blocked_message()
        elif cleanup_result.get("status") == "failed":
            st.error(cleanup_result.get("message", "Cleanup failed."))
        else:
            st.success(cleanup_result.get("message"))
            summary_cards = [
                {
                    "title": "Invoice master",
                    "value": (
                        "Cleared"
                        if cleanup_result.get("invoice_master_cleared")
                        else "Not cleared"
                    ),
                    "help_text": "Prior invoice run rows",
                },
                {
                    "title": "AP Agent DB",
                    "value": (
                        "Deleted"
                        if cleanup_result.get("agent_db_deleted_paths")
                        else "Not found"
                    ),
                    "help_text": "AP Agent invoice history",
                },
                {
                    "title": "Uploaded files",
                    "value": cleanup_result.get("uploaded_files_deleted_count", 0),
                    "help_text": "Invoice upload artifacts removed",
                },
                {
                    "title": "PO / GRN data",
                    "value": "Preserved",
                    "help_text": "Reference data kept for the next demo",
                },
                {
                    "title": "Posted references",
                    "value": (
                        "Cleared"
                        if cleanup_result.get("posted_references_cleared")
                        else "Preserved"
                    ),
                    "help_text": "Duplicate/already-posted reference table",
                },
            ]
            render_metric_cards(summary_cards, columns=3)
        delete_errors = cleanup_result.get("agent_db_delete_errors", [])
        if any(
            item.get("error_type") == "PermissionError"
            for item in delete_errors
        ):
            st.warning("Stop AP Agent API and run cleanup again.")
        render_technical_details("Technical cleanup details", cleanup_result)

    st.divider()

    st.subheader("Mock API / Reference Data Readiness")
    readiness_cards = [
        {
            "title": "Mock SAP API",
            "value": api_health(API_BASE_URL),
            "help_text": "Reference API used for PO and GRN setup",
        },
        {
            "title": "Current PO count",
            "value": safe_table_count("sap_po_master"),
            "help_text": "PO records in master reference table",
        },
        {
            "title": "Current GRN count",
            "value": safe_table_count("sap_grn_master"),
            "help_text": "GRN records in master reference table",
        },
    ]
    render_metric_cards(readiness_cards, columns=3)
    st.info(
        "If Mock SAP API has no PO/GRN data, create PO and GRN records in Reference Data & Test Setup or run the seed script, then click Start Structured Sync."
    )

    st.divider()

    # ==========================
    # RESET DEMO
    # ==========================

    st.subheader(
        "Full Master Reset"
    )
    st.warning(
        "This clears invoice, PO, GRN, posted invoice reference tables and sync state. Use only when rebuilding all reference data."
    )

    confirm_reset = st.checkbox(
    "I understand this will clear invoice_master, PO, GRN, posted invoice tables, and reset sync state.",
    key="confirm_reset_demo"
        )

    if confirm_reset:

        if st.button(
            "Full Master Reset",
            key="reset_demo_btn"
        ):

            if not database_settings.allow_destructive_master_reset:
                show_master_reset_blocked_message()
            else:
                try:

                    result = reset_demo_environment()

                    if result.get("status") == "success":

                        st.success(
                            "Demo environment reset."
                        )

                        st.rerun()

                    else:

                        st.error(
                            result.get(
                                "error",
                                "Reset failed"
                            )
                        )

                except Exception as e:

                    st.exception(e)

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

st.sidebar.header("AP Automation Demo")
selected_module = st.sidebar.radio(
    "Navigate",
    [
        "Dashboard / Control Tower",
        "Invoice Journey",
        "AP Agent Workbench",
        "Response & Recheck Demo",
        "Reference Data & Test Setup",
        "Admin / Demo Cleanup",
    ],
)

st.sidebar.divider()
st.sidebar.caption("Service Status")
with st.sidebar:
    st.write("AP Agent")
    render_status_badge(api_health(AP_AGENT_BASE_URL))
    st.caption(AP_AGENT_BASE_URL)
    st.write("Mock SAP API")
    render_status_badge(api_health(API_BASE_URL))
    st.caption(API_BASE_URL)

if selected_module == "Dashboard / Control Tower":
    render_dashboard_control_tower()
elif selected_module == "Invoice Journey":
    render_invoice_journey()
elif selected_module == "AP Agent Workbench":
    render_ap_agent_workbench()
elif selected_module == "Response & Recheck Demo":
    render_response_recheck_demo()
elif selected_module == "Reference Data & Test Setup":
    render_reference_data_test_setup()
elif selected_module == "Admin / Demo Cleanup":
    render_admin_demo_cleanup()
