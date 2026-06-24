"""Validate required AP master and Agent API tables."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import inspect


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from ap_database.engines import get_agent_engine, get_master_engine
from ap_database.master_models import MASTER_SCHEMA
from ap_database.settings import (
    is_postgres_url,
    mask_database_url,
    settings,
)


REQUIRED_MASTER_TABLES = {
    "invoice_master",
    "sap_po_master",
    "sap_grn_master",
    "sap_posted_invoice_master",
}

REQUIRED_AGENT_TABLES = {
    "invoice_artifacts",
    "invoices",
    "invoice_lines",
    "validation_results",
    "exception_cases",
    "communications",
    "posting_attempts",
    "workflow_events",
    "po_grn_consumption_ledger",
}


def _database_mode(url: str) -> str:
    return "PostgreSQL/RDS" if is_postgres_url(url) else "SQLite/local"


def _missing_tables(engine, required: set[str], schema: str | None) -> list[str]:
    inspector = inspect(engine)
    return sorted(
        table_name
        for table_name in required
        if not inspector.has_table(table_name, schema=schema)
    )


def main() -> int:
    print(f"Environment: {settings.app_env}")
    print(
        "Master database: "
        f"{mask_database_url(settings.master_database_url)} "
        f"({_database_mode(settings.master_database_url)})"
    )
    print(
        "Agent database: "
        f"{mask_database_url(settings.database_url)} "
        f"({_database_mode(settings.database_url)})"
    )

    success = True

    try:
        missing_master = _missing_tables(
            get_master_engine(),
            REQUIRED_MASTER_TABLES,
            MASTER_SCHEMA,
        )
        if missing_master:
            success = False
            print(
                "[FAILURE] Missing master tables: "
                + ", ".join(missing_master)
            )
        else:
            print("[SUCCESS] All required master tables exist.")
    except Exception as exc:
        success = False
        print(
            "[FAILURE] Master schema validation failed "
            f"({type(exc).__name__})."
        )

    try:
        missing_agent = _missing_tables(
            get_agent_engine(),
            REQUIRED_AGENT_TABLES,
            None,
        )
        if missing_agent:
            success = False
            print(
                "[FAILURE] Missing agent tables: "
                + ", ".join(missing_agent)
            )
        else:
            print("[SUCCESS] All required agent tables exist.")
    except Exception as exc:
        success = False
        print(
            "[FAILURE] Agent schema validation failed "
            f"({type(exc).__name__})."
        )

    if success:
        print("[SUCCESS] Schema validation completed.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
