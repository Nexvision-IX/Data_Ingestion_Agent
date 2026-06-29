"""Validate that S01 clean invoice can move beyond SAP fetch.

This script is intentionally diagnostic. It does not seed data, upload PDFs,
or change schemas beyond invoking the normal local master schema compatibility
initializer, which only adds missing SQLite columns.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_APP_ROOT = PROJECT_ROOT / "agent_app"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(AGENT_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_APP_ROOT))

from ap_database.master_repository import init_master_schema_if_needed


S01_INVOICE_NUMBER = "INV-CLEAN-001"
S01_PO_NUMBER = "PO-CLEAN-001"
EXPECTED_TERMS = "NET 30"
SUCCESS_STATUSES = {"READY_FOR_POSTING", "POSTED"}


def _sqlite_path(env_key: str, default: Path) -> Path:
    value = os.getenv(env_key)
    if value:
        return Path(value)
    return default


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row[1]
        for row in connection.execute(f'PRAGMA table_info("{table_name}")')
    }


def _one(
    connection: sqlite3.Connection,
    query: str,
    params: tuple[Any, ...],
) -> sqlite3.Row | None:
    return connection.execute(query, params).fetchone()


def _post_process_new() -> dict[str, Any]:
    base_url = os.getenv(
        "AGENT_API_BASE_URL",
        os.getenv("AP_AGENT_BASE_URL", "http://127.0.0.1:8000"),
    ).rstrip("/")
    url = f"{base_url}/api/v1/integrations/ap-master/process-new?limit=50"
    request = urllib.request.Request(
        url,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"AP Agent process-new failed at {url}: {exc}") from exc


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"OK   {message}")


def main() -> int:
    init_master_schema_if_needed()

    master_db = _sqlite_path(
        "MASTER_DB_PATH",
        PROJECT_ROOT / "data" / "master" / "ap_master.db",
    )
    agent_db = _sqlite_path(
        "AP_AGENT_DB_PATH",
        PROJECT_ROOT / "agent_app" / "ap_agent.db",
    )

    print(f"Master DB: {master_db}")
    print(f"Agent DB:  {agent_db}")

    with sqlite3.connect(master_db) as master:
        master.row_factory = sqlite3.Row
        invoice_columns = _columns(master, "invoice_master")
        po_columns = _columns(master, "sap_po_master")
        _assert(
            "payment_terms" in invoice_columns,
            "invoice_master has payment_terms column",
        )
        _assert(
            "payment_terms" in po_columns,
            "sap_po_master has payment_terms column",
        )

        invoice = _one(
            master,
            "SELECT * FROM invoice_master WHERE invoice_number = ?",
            (S01_INVOICE_NUMBER,),
        )
        po = _one(
            master,
            "SELECT * FROM sap_po_master WHERE po_number = ?",
            (S01_PO_NUMBER,),
        )
        _assert(invoice is not None, f"{S01_INVOICE_NUMBER} exists")
        _assert(po is not None, f"{S01_PO_NUMBER} exists")
        _assert(
            invoice["payment_terms"] == EXPECTED_TERMS,
            f"{S01_INVOICE_NUMBER} payment_terms = {EXPECTED_TERMS}",
        )
        _assert(
            po["payment_terms"] == EXPECTED_TERMS,
            f"{S01_PO_NUMBER} payment_terms = {EXPECTED_TERMS}",
        )
        _assert(
            _json_object(invoice["raw_json"]).get("payment_terms")
            == EXPECTED_TERMS,
            f"{S01_INVOICE_NUMBER} raw_json contains payment_terms",
        )
        _assert(
            _json_object(po["raw_json"]).get("payment_terms")
            == EXPECTED_TERMS,
            f"{S01_PO_NUMBER} raw_json contains payment_terms",
        )

    process_result = _post_process_new()
    print("process-new result:")
    print(json.dumps(process_result, indent=2))

    with sqlite3.connect(agent_db) as agent:
        agent.row_factory = sqlite3.Row
        invoice = _one(
            agent,
            """
            SELECT *
            FROM invoices
            WHERE invoice_number = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (S01_INVOICE_NUMBER,),
        )
        _assert(invoice is not None, "AP Agent invoice row exists")
        _assert(
            invoice["payment_terms"] == EXPECTED_TERMS,
            f"AP Agent invoice payment_terms = {EXPECTED_TERMS}",
        )
        _assert(
            invoice["status"] in SUCCESS_STATUSES,
            f"final status is one of {sorted(SUCCESS_STATUSES)}",
        )
        validation_count = agent.execute(
            "SELECT COUNT(*) FROM validation_results WHERE invoice_id = ?",
            (invoice["id"],),
        ).fetchone()[0]
        _assert(validation_count > 0, "validation_results rows exist")
        latest_event = _one(
            agent,
            """
            SELECT event_type
            FROM workflow_events
            WHERE invoice_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (invoice["id"],),
        )
        _assert(latest_event is not None, "workflow event exists")
        _assert(
            latest_event["event_type"] != "SAP_FETCH_STARTED",
            "latest workflow event is beyond SAP_FETCH_STARTED",
        )

    print("S01 clean invoice validation completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
