"""Read-only, database-neutral queries for the AP Agent Monitor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from ap_database.engines import (
    get_agent_engine,
    get_agent_session_factory,
)
from ap_database.settings import settings


def _sqlite_database_file_exists() -> bool:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite":
        return True

    database = url.database
    if not database or database == ":memory:":
        return True
    return Path(database).expanduser().exists()


def agent_db_available() -> bool:
    """Return whether the configured agent database and invoices table exist."""
    if not _sqlite_database_file_exists():
        return False

    try:
        with get_agent_engine().connect() as connection:
            return inspect(connection).has_table("invoices")
    except SQLAlchemyError:
        return False


def _read_dataframe(statement: str, params: dict[str, Any] | None = None):
    if not agent_db_available():
        return pd.DataFrame()

    session_factory = get_agent_session_factory()
    with session_factory() as session:
        return pd.read_sql_query(
            text(statement),
            session.connection(),
            params=params or {},
        )


def load_ap_agent_summary() -> pd.DataFrame:
    return _read_dataframe(
        """
        SELECT
            status,
            COUNT(*) AS total
        FROM invoices
        GROUP BY status
        ORDER BY total DESC, status ASC
        """
    )


def load_ap_agent_invoices(limit: int = 50) -> pd.DataFrame:
    return _read_dataframe(
        """
        SELECT
            i.invoice_number,
            i.vendor_name,
            i.po_number,
            i.currency,
            i.total_amount,
            i.status AS agent_status,
            i.source,

            (
                SELECT COUNT(*)
                FROM validation_results vr
                WHERE vr.invoice_id = i.id
                  AND vr.passed IS FALSE
            ) AS failed_rule_count,

            ec.category AS exception_category,
            ec.priority AS exception_priority,
            ec.owner_team AS exception_owner,
            ec.status AS exception_status,

            c.status AS email_status,
            c.recipient AS email_recipient,
            c.subject AS email_subject,
            c.smtp_message_id,
            c.created_at AS email_created_at,

            pa.status AS posting_status,
            pa.sap_document_number,
            pa.message AS posting_message,

            we.event_type AS latest_event,
            we.agent_name AS latest_agent,
            we.message AS latest_message,

            i.created_at,
            i.updated_at

        FROM invoices i

        LEFT JOIN exception_cases ec
            ON ec.id = (
                SELECT ec2.id
                FROM exception_cases ec2
                WHERE ec2.invoice_id = i.id
                ORDER BY ec2.created_at DESC, ec2.id DESC
                LIMIT 1
            )

        LEFT JOIN communications c
            ON c.id = (
                SELECT c2.id
                FROM communications c2
                WHERE c2.invoice_id = i.id
                ORDER BY c2.created_at DESC, c2.id DESC
                LIMIT 1
            )

        LEFT JOIN posting_attempts pa
            ON pa.id = (
                SELECT pa2.id
                FROM posting_attempts pa2
                WHERE pa2.invoice_id = i.id
                ORDER BY pa2.created_at DESC, pa2.id DESC
                LIMIT 1
            )

        LEFT JOIN workflow_events we
            ON we.id = (
                SELECT we2.id
                FROM workflow_events we2
                WHERE we2.invoice_id = i.id
                ORDER BY we2.created_at DESC, we2.id DESC
                LIMIT 1
            )

        ORDER BY i.created_at DESC, i.id DESC
        LIMIT :limit
        """,
        {"limit": max(0, int(limit))},
    )


def load_ap_agent_events(invoice_number: str) -> pd.DataFrame:
    return _read_dataframe(
        """
        SELECT
            we.created_at,
            we.event_type,
            we.agent_name,
            we.message
        FROM workflow_events we
        JOIN invoices i
            ON i.id = we.invoice_id
        WHERE i.invoice_number = :invoice_number
        ORDER BY we.created_at DESC, we.id DESC
        """,
        {"invoice_number": invoice_number},
    )


def load_ap_agent_validation_results(invoice_number: str) -> pd.DataFrame:
    return _read_dataframe(
        """
        SELECT
            vr.rule_code,
            vr.rule_name,
            vr.passed,
            vr.severity,
            vr.message,
            vr.created_at
        FROM validation_results vr
        JOIN invoices i
            ON i.id = vr.invoice_id
        WHERE i.invoice_number = :invoice_number
        ORDER BY vr.created_at DESC, vr.id DESC
        """,
        {"invoice_number": invoice_number},
    )


def load_ap_agent_communications(invoice_number: str) -> pd.DataFrame:
    return _read_dataframe(
        """
        SELECT
            c.created_at,
            c.direction,
            c.recipient,
            c.subject,
            c.body,
            c.status,
            c.smtp_message_id
        FROM communications c
        JOIN invoices i
            ON i.id = c.invoice_id
        WHERE i.invoice_number = :invoice_number
        ORDER BY c.created_at DESC, c.id DESC
        """,
        {"invoice_number": invoice_number},
    )
