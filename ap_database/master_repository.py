"""SQLAlchemy Core repository helpers for AP master tables."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from sqlalchemy import delete, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ap_database.engines import (
    get_master_engine,
    get_master_session_factory,
)
from ap_database.master_models import (
    MASTER_TABLE_MODELS,
    MasterBase,
)


ALLOWED_MASTER_TABLES = frozenset(MASTER_TABLE_MODELS)


def _get_model(table_name: str):
    try:
        return MASTER_TABLE_MODELS[table_name]
    except KeyError as exc:
        allowed = ", ".join(sorted(ALLOWED_MASTER_TABLES))
        raise ValueError(
            f"Unsupported master table: {table_name!r}. Allowed tables: {allowed}"
        ) from exc


def _qualified_table_name(table_name: str) -> str:
    model = _get_model(table_name)
    table = model.__table__
    preparer = get_master_engine().dialect.identifier_preparer

    parts = []
    if table.schema:
        parts.append(preparer.quote_identifier(table.schema))
    parts.append(preparer.quote_identifier(table.name))
    return ".".join(parts)


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _as_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _common_document_values(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "po_number": payload.get("po_number"),
        "vendor_name": payload.get("vendor_name"),
        "currency": payload.get("currency"),
        "document_subtotal": _as_decimal(payload.get("document_subtotal")),
        "tax_amount": _as_decimal(payload.get("tax_amount")),
        "vat_percent": _as_decimal(payload.get("vat_percent")),
        "document_total": _as_decimal(payload.get("document_total")),
        "items_json": _as_json(
            payload.get("line_items", payload.get("items_json")), []
        ),
        "raw_json": _as_json(payload.get("raw_json"), payload),
        "updated_at": datetime.now(timezone.utc),
    }


def _upsert(
    table_name: str,
    values: dict[str, Any],
    connection: Connection | None = None,
) -> None:
    table = _get_model(table_name).__table__
    engine = get_master_engine()
    primary_key_columns = [column.name for column in table.primary_key.columns]

    # PostgreSQL and SQLite both support ON CONFLICT, but SQLAlchemy exposes
    # dialect-specific insert objects for their respective implementations.
    if engine.dialect.name == "postgresql":
        statement = postgresql_insert(table).values(**values)
    elif engine.dialect.name == "sqlite":
        statement = sqlite_insert(table).values(**values)
    else:
        raise RuntimeError(
            f"Unsupported master database dialect: {engine.dialect.name}"
        )

    update_values = {
        column_name: statement.excluded[column_name]
        for column_name in values
        if column_name not in primary_key_columns
    }
    statement = statement.on_conflict_do_update(
        index_elements=primary_key_columns,
        set_=update_values,
    )

    if connection is not None:
        connection.execute(statement)
        return

    with engine.begin() as managed_connection:
        managed_connection.execute(statement)


def init_master_schema_if_needed() -> None:
    """Create the configured master schema/tables when explicitly requested."""
    engine = get_master_engine()

    if engine.dialect.name == "postgresql":
        # PostgreSQL uses a dedicated schema. SQLite has no schema namespace
        # and creates the same compatible table names in its main database.
        with engine.begin() as connection:
            connection.execute(text('CREATE SCHEMA IF NOT EXISTS "master"'))

    MasterBase.metadata.create_all(bind=engine)


def test_master_repository_connection() -> bool:
    """Run a minimal query through the configured master engine."""
    with get_master_engine().connect() as connection:
        return connection.execute(text("SELECT 1")).scalar_one() == 1


def get_table_count(table_name: str) -> int:
    """Return a row count for one allowlisted master table."""
    qualified_name = _qualified_table_name(table_name)
    session_factory = get_master_session_factory()

    with session_factory() as session:
        value = session.execute(
            text(f"SELECT COUNT(*) FROM {qualified_name}")
        ).scalar_one()

    return int(value)


def load_table_data(table_name: str, limit: int = 10) -> pd.DataFrame:
    """Load recent rows from one allowlisted master table."""
    model = _get_model(table_name)
    qualified_name = _qualified_table_name(table_name)
    primary_key = next(iter(model.__table__.primary_key.columns)).name
    preparer = get_master_engine().dialect.identifier_preparer
    updated_at = preparer.quote_identifier("updated_at")
    primary_key = preparer.quote_identifier(primary_key)
    normalized_limit = max(0, int(limit))

    statement = text(
        f"SELECT * FROM {qualified_name} "
        f"ORDER BY {updated_at} DESC, {primary_key} DESC "
        "LIMIT :limit"
    )

    with get_master_engine().connect() as connection:
        return pd.read_sql_query(
            statement,
            connection,
            params={"limit": normalized_limit},
        )


def upsert_invoice(
    payload: dict[str, Any],
    connection: Connection | None = None,
) -> None:
    values = _common_document_values(payload)
    values.update(
        invoice_number=payload.get("invoice_number"),
        invoice_date=_as_date(payload.get("invoice_date")),
        payment_status=payload.get("payment_status"),
        last_modified=_as_datetime(payload.get("last_modified")),
    )
    _upsert("invoice_master", values, connection)


def upsert_po(
    payload: dict[str, Any],
    connection: Connection | None = None,
) -> None:
    values = _common_document_values(payload)
    values.update(
        po_number=payload.get("po_number"),
        po_date=_as_date(payload.get("po_date")),
        po_status=payload.get("po_status"),
        last_modified=_as_datetime(payload.get("last_modified")),
    )
    _upsert("sap_po_master", values, connection)


def upsert_grn(
    payload: dict[str, Any],
    connection: Connection | None = None,
) -> None:
    values = _common_document_values(payload)
    # GRNs do not contain tax columns in the existing master table.
    values.pop("tax_amount", None)
    values.pop("vat_percent", None)
    values.update(
        gr_number=payload.get("gr_number"),
        gr_date=_as_date(payload.get("gr_date")),
        gr_status=payload.get("gr_status"),
        last_modified=_as_datetime(payload.get("last_modified")),
    )
    _upsert("sap_grn_master", values, connection)


def upsert_posted_invoice(
    payload: dict[str, Any],
    connection: Connection | None = None,
) -> None:
    values = _common_document_values(payload)
    values.update(
        invoice_number=payload.get("invoice_number"),
        invoice_date=_as_date(payload.get("invoice_date")),
        payment_status=payload.get("payment_status"),
        sap_document_number=payload.get("sap_document_number"),
        posting_status=payload.get("posting_status", "POSTED"),
        posting_message=payload.get("posting_message"),
        source_system=payload.get("source_system", "AP_AGENT"),
        posted_at=_as_datetime(payload.get("posted_at"))
        or datetime.now(timezone.utc),
    )
    _upsert("sap_posted_invoice_master", values, connection)


def _delete_by_primary_key(table_name: str, value: str) -> None:
    table = _get_model(table_name).__table__
    primary_key = next(iter(table.primary_key.columns))
    statement = delete(table).where(primary_key == value)

    with get_master_engine().begin() as connection:
        connection.execute(statement)


def delete_invoice(invoice_number: str) -> None:
    _delete_by_primary_key("invoice_master", invoice_number)


def delete_posted_invoice(invoice_number: str) -> None:
    _delete_by_primary_key("sap_posted_invoice_master", invoice_number)


def delete_po(po_number: str) -> None:
    _delete_by_primary_key("sap_po_master", po_number)


def delete_grn(gr_number: str) -> None:
    _delete_by_primary_key("sap_grn_master", gr_number)


def _clear_table(table_name: str) -> None:
    table = _get_model(table_name).__table__
    with get_master_engine().begin() as connection:
        connection.execute(delete(table))


def clear_invoice_table() -> None:
    _clear_table("invoice_master")


def clear_posted_invoice_table() -> None:
    _clear_table("sap_posted_invoice_master")


def clear_po_table() -> None:
    _clear_table("sap_po_master")


def clear_grn_table() -> None:
    _clear_table("sap_grn_master")


def keep_latest_rows(table_name: str, keep_count: int) -> None:
    """Keep a deterministic set of the most recently updated rows."""
    table = _get_model(table_name).__table__
    normalized_count = max(0, int(keep_count))
    primary_key = next(iter(table.primary_key.columns))

    ordering = [table.c.updated_at.desc()]
    if "last_modified" in table.c:
        ordering.append(table.c.last_modified.desc())
    ordering.append(primary_key.desc())

    retained_keys = select(primary_key).order_by(*ordering).limit(normalized_count)
    statement = delete(table).where(primary_key.not_in(retained_keys))

    # Unlike the old SQLite implementation, this does not rely on rowid.
    # The same deterministic updated/modified/primary-key ordering is used by
    # both PostgreSQL and SQLite.
    with get_master_engine().begin() as connection:
        connection.execute(statement)


def reset_demo_environment() -> dict[str, str]:
    """Clear all master tables in one database transaction."""
    engine = get_master_engine()
    delete_order = (
        "invoice_master",
        "sap_po_master",
        "sap_grn_master",
        "sap_posted_invoice_master",
    )

    with engine.begin() as connection:
        for table_name in delete_order:
            connection.execute(delete(_get_model(table_name).__table__))

    return {"status": "success"}
