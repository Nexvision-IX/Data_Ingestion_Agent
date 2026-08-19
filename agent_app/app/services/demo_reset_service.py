"""Transactional AP Agent invoice-flow reset primitives."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text


AGENT_INVOICE_FLOW_DELETE_ORDER = (
    "po_grn_consumption_ledger",
    "invoice_artifacts",
    "invoice_lines",
    "validation_results",
    "communications",
    "exception_cases",
    "workflow_events",
    "posting_attempts",
    "invoices",
)


def reset_agent_invoice_flow(engine: Engine) -> dict[str, int]:
    """Delete all agent invoice-flow rows atomically, children first."""
    existing_tables = set(inspect(engine).get_table_names())
    preparer = engine.dialect.identifier_preparer
    deleted_rows: dict[str, int] = {}
    with engine.begin() as connection:
        for table_name in AGENT_INVOICE_FLOW_DELETE_ORDER:
            if table_name not in existing_tables:
                continue
            quoted = preparer.quote_identifier(table_name)
            result = connection.execute(text(f"DELETE FROM {quoted}"))
            deleted_rows[table_name] = result.rowcount
    return deleted_rows
