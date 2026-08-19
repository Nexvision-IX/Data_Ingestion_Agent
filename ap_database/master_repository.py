"""SQLAlchemy Core repository helpers for AP master tables."""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from pathlib import Path

from sqlalchemy import delete, func, inspect, select, text, update
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
from ap_database.settings import is_postgres_url, settings

AGENT_APP_ROOT = Path(__file__).resolve().parents[1] / "agent_app"
if str(AGENT_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_APP_ROOT))

from app.services.date_normalization_service import normalize_date
from app.services.payment_terms_control import calculate_due_date
from ap_database.extraction_confidence import (
    canonicalize_extraction_confidence,
)

ALLOWED_MASTER_TABLES = frozenset(MASTER_TABLE_MODELS)

LOCAL_SQLITE_COLUMN_TYPES = {
    "invoice_master": {
        "vendor_number": "TEXT",
        "due_date": "TEXT",
        "payment_terms": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "sap_po_master": {
        "vendor_number": "TEXT",
        "payment_terms": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "sap_grn_master": {
        "vendor_number": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "sap_posted_invoice_master": {
        "vendor_number": "TEXT",
        "due_date": "TEXT",
        "payment_terms": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
}


class DestructiveMasterOperationBlocked(RuntimeError):
    """Raised when a destructive master-data operation is blocked."""


class MasterSchemaNotInitialized(RuntimeError):
    """Raised when required master tables are missing at runtime."""


SAFE_RUNTIME_ENVIRONMENTS = {
    "production",
    "prod",
    "staging",
    "stage",
    "aws",
}


def require_destructive_master_reset_allowed(operation_name: str) -> None:
    """Block destructive master-data operations unless explicitly enabled."""
    if settings.allow_destructive_master_reset:
        return

    raise DestructiveMasterOperationBlocked(
        f"Destructive master operation '{operation_name}' is disabled for "
        f"APP_ENV={settings.app_env!r}. Set "
        "ALLOW_DESTRUCTIVE_MASTER_RESET=true only for an intentional local/demo "
        "maintenance action. Keep it false for normal AWS deployment."
    )


def _is_safe_runtime_environment() -> bool:
    return (
        settings.app_env.strip().lower() in SAFE_RUNTIME_ENVIRONMENTS
        or is_postgres_url(settings.master_database_url)
    )


def assert_master_schema_initialized() -> None:
    """Verify master tables exist without creating them."""
    engine = get_master_engine()
    inspector = inspect(engine)

    missing_tables = []
    for table_name, model in MASTER_TABLE_MODELS.items():
        table = model.__table__
        schema = table.schema if engine.dialect.name == "postgresql" else None
        if not inspector.has_table(table.name, schema=schema):
            missing_tables.append(table_name)

    if missing_tables:
        raise MasterSchemaNotInitialized(
            "Master schema is not initialized. Missing tables: "
            + ", ".join(sorted(missing_tables))
            + ". Run `python scripts/init_rds_schema.py` and "
            "`python scripts/check_rds_schema.py` before starting runtime services."
        )

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
    return normalize_date(value).normalized_date


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


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(_json_safe(key)): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _normalize_payment_terms(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if not raw:
        return None

    compact = re.sub(r"[^A-Z0-9]+", "", raw)
    if compact in {"NET30", "N30", "NET30DAYS", "NET30DAY"}:
        return "NET 30"
    if compact in {"NET45", "N45", "NET45DAYS", "NET45DAY"}:
        return "NET 45"
    if compact in {"NET60", "N60", "NET60DAYS", "NET60DAY"}:
        return "NET 60"
    if compact in {"IMMEDIATE", "DUEONRECEIPT", "PAYABLEONRECEIPT"}:
        return "DUE_ON_RECEIPT"

    due_in_match = re.search(r"\bDUE\s+IN\s+(\d{1,3})\s+DAYS?\b", raw)
    if due_in_match:
        return f"NET {int(due_in_match.group(1))}"

    net_match = re.search(r"\bNET\s*(\d{1,3})\s*(?:DAYS?)?\b", raw)
    if net_match:
        return f"NET {int(net_match.group(1))}"

    return str(value).strip()


def _extract_payment_terms_from_text(*values: Any) -> str | None:
    text_value = "\n".join(str(value or "") for value in values).upper()
    patterns = (
        r"\bPAYMENT\s+TERMS?\s*[:\-]?\s*(NET\s*\d{1,3}(?:\s*DAYS?)?)",
        r"\bTERMS?\s*[:\-]?\s*(NET\s*\d{1,3}(?:\s*DAYS?)?)",
        r"\b(NET\s*\d{1,3}(?:\s*DAYS?)?)\b",
        r"\b(DUE\s+IN\s+\d{1,3}\s+DAYS?)\b",
        r"\b(DUE\s+ON\s+RECEIPT)\b",
        r"\b(IMMEDIATE)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text_value)
        if match:
            return _normalize_payment_terms(match.group(1))
    return None


def _merge_raw_json(
    payload: dict[str, Any],
    existing_raw_json: Any = None,
) -> dict[str, Any]:
    existing = _as_json(existing_raw_json, {})
    if not isinstance(existing, dict):
        existing = {}

    incoming = _as_json(payload.get("raw_json"), payload)
    if not isinstance(incoming, dict):
        incoming = {}

    merged = dict(existing)
    for key, value in incoming.items():
        if value not in (None, ""):
            merged[key] = value

    for key in (
        "payment_terms",
        "due_date",
        "vendor_number",
        "confidence",
        "extraction_confidence",
        "field_confidence",
        "warnings",
        "ocr_provider",
        "ocr_version",
        "extraction_provider",
        "extraction_model",
        "model",
        "prompt_version",
        "schema_version",
        "retry_count",
        "vendor_number_genuinely_extracted",
    ):
        value = payload.get(key)
        if key not in {
            "confidence",
            "extraction_confidence",
            "field_confidence",
            "warnings",
            "retry_count",
            "vendor_number_genuinely_extracted",
        }:
            value = _clean_text(value)
        if value not in (None, ""):
            merged[key] = value
    if payload.get("invoice_date") not in (None, ""):
        merged["raw_invoice_date"] = _json_safe(payload["invoice_date"])
    if payload.get("currency") not in (None, ""):
        merged["raw_currency"] = _json_safe(payload["currency"])

    confidence = canonicalize_extraction_confidence({
        **merged,
        **{
            key: value
            for key, value in payload.items()
            if value not in (None, "")
        },
    })
    confidence_data = confidence.to_dict()
    merged["extraction_confidence"] = (
        confidence.extraction_confidence
    )
    merged["extraction_confidence_source"] = (
        confidence.confidence_source
    )
    merged["confidence_supplied"] = confidence.confidence_supplied
    merged["field_confidence"] = confidence.field_confidence
    merged["warnings"] = confidence.warnings
    merged["ocr_provider"] = confidence.ocr_provider
    merged["ocr_version"] = confidence.ocr_version
    merged["extraction_provider"] = confidence.extraction_provider
    merged["extraction_model"] = confidence.extraction_model
    merged["extraction_version"] = confidence.extraction_version
    merged["extraction_attempt_number"] = confidence.attempt_number
    merged["retry_count"] = confidence.retry_count
    merged["raw_extraction_quality_evidence"] = confidence_data[
        "raw_quality_evidence"
    ]

    terms = _first_non_empty(
        _normalize_payment_terms(merged.get("payment_terms")),
        _extract_payment_terms_from_text(
            merged.get("structured_ocr_text"),
            merged.get("raw_ocr_text"),
        ),
    )
    if terms:
        merged["payment_terms"] = terms

    return _json_safe(merged)


def _fetch_existing_row(
    table_name: str,
    primary_key_value: Any,
    connection: Connection | None = None,
) -> dict[str, Any]:
    if primary_key_value in (None, ""):
        return {}

    table = _get_model(table_name).__table__
    primary_key = next(iter(table.primary_key.columns))
    statement = select(table).where(primary_key == primary_key_value)

    if connection is not None:
        row = connection.execute(statement).mappings().first()
        return dict(row) if row else {}

    with get_master_engine().connect() as managed_connection:
        row = managed_connection.execute(statement).mappings().first()
        return dict(row) if row else {}


def _common_document_values(
    payload: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = existing or {}
    raw_json = _merge_raw_json(payload, existing.get("raw_json"))
    return {
        "po_number": payload.get("po_number"),
        "vendor_name": payload.get("vendor_name"),
        "vendor_number": _first_non_empty(
            _clean_text(payload.get("vendor_number")),
            existing.get("vendor_number"),
            raw_json.get("vendor_number"),
        ),
        "currency": payload.get("currency"),
        "document_subtotal": _as_decimal(payload.get("document_subtotal")),
        "tax_amount": _as_decimal(payload.get("tax_amount")),
        "vat_percent": _as_decimal(payload.get("vat_percent")),
        "document_total": _as_decimal(payload.get("document_total")),
        "items_json": _as_json(
            payload.get("line_items", payload.get("items_json")), []
        ),
        "raw_json": raw_json,
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
    """Create local SQLite schema, but only verify schema in AWS/PostgreSQL runtime."""
    if _is_safe_runtime_environment():
        assert_master_schema_initialized()
        return

    engine = get_master_engine()

    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text('CREATE SCHEMA IF NOT EXISTS "master"'))

    MasterBase.metadata.create_all(bind=engine)
    _migrate_local_sqlite_schema(engine)


def _migrate_local_sqlite_schema(engine) -> None:
    """Add missing local SQLite columns without dropping or recreating data."""
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        for table_name, required_columns in LOCAL_SQLITE_COLUMN_TYPES.items():
            existing_columns = {
                row[1]
                for row in connection.execute(
                    text(f'PRAGMA table_info("{table_name}")')
                )
            }
            for column_name, column_type in required_columns.items():
                if column_name in existing_columns:
                    continue
                connection.execute(
                    text(
                        f'ALTER TABLE "{table_name}" '
                        f'ADD COLUMN "{column_name}" {column_type}'
                    )
                )
        _backfill_local_sqlite_payment_terms(connection)


def _backfill_local_sqlite_payment_terms(connection: Connection) -> None:
    table_configs = {
        "invoice_master": "invoice_number",
        "sap_po_master": "po_number",
        "sap_posted_invoice_master": "invoice_number",
    }

    for table_name, primary_key in table_configs.items():
        columns = {
            row[1]
            for row in connection.execute(
                text(f'PRAGMA table_info("{table_name}")')
            )
        }
        if "payment_terms" not in columns or "raw_json" not in columns:
            continue

        date_columns = (
            ', "invoice_date", "due_date"'
            if {"invoice_date", "due_date"}.issubset(columns)
            else ""
        )
        rows = connection.execute(
            text(
                f'SELECT "{primary_key}", "payment_terms", "raw_json"'
                f'{date_columns} '
                f'FROM "{table_name}"'
            )
        ).mappings().all()

        for row in rows:
            raw_json = _as_json(row.get("raw_json"), {})
            if not isinstance(raw_json, dict):
                raw_json = {}

            terms = _first_non_empty(
                _normalize_payment_terms(row.get("payment_terms")),
                _normalize_payment_terms(raw_json.get("payment_terms")),
                _extract_payment_terms_from_text(
                    raw_json.get("structured_ocr_text"),
                    raw_json.get("raw_ocr_text"),
                ),
            )
            if not terms:
                continue

            updates = {}
            if terms != row.get("payment_terms"):
                updates["payment_terms"] = terms
            if date_columns and row.get("due_date") in (None, ""):
                calculated_due_date = calculate_due_date(
                    row.get("invoice_date"),
                    terms,
                )
                if calculated_due_date is not None:
                    updates["due_date"] = calculated_due_date.isoformat()
            if raw_json.get("payment_terms") != terms:
                raw_json["payment_terms"] = terms
                updates["raw_json"] = json.dumps(raw_json, default=str)
            if not updates:
                continue

            assignments = ", ".join(f'"{column}" = :{column}' for column in updates)
            updates["primary_key_value"] = row.get(primary_key)
            connection.execute(
                text(
                    f'UPDATE "{table_name}" SET {assignments} '
                    f'WHERE "{primary_key}" = :primary_key_value'
                ),
                updates,
            )


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


def load_table_data(table_name: str, limit: int = 10) -> "pd.DataFrame":
    import pandas as pd

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
    existing = _fetch_existing_row(
        "invoice_master",
        payload.get("invoice_number"),
        connection,
    )
    values = _common_document_values(payload, existing)
    raw_json = values["raw_json"]
    invoice_date = _as_date(payload.get("invoice_date"))
    payment_terms = _first_non_empty(
        _normalize_payment_terms(payload.get("payment_terms")),
        raw_json.get("payment_terms") if isinstance(raw_json, dict) else None,
        existing.get("payment_terms"),
    )
    supplied_due_date = _first_non_empty(
        payload.get("due_date"),
        raw_json.get("due_date") if isinstance(raw_json, dict) else None,
        existing.get("due_date"),
    )
    values.update(
        invoice_number=payload.get("invoice_number"),
        invoice_date=invoice_date,
        due_date=(
            _as_date(supplied_due_date)
            or calculate_due_date(invoice_date, payment_terms)
        ),
        payment_terms=payment_terms,
        payment_status=payload.get("payment_status"),
        last_modified=_as_datetime(payload.get("last_modified")),
    )
    _upsert("invoice_master", values, connection)


def upsert_po(
    payload: dict[str, Any],
    connection: Connection | None = None,
) -> None:
    existing = _fetch_existing_row(
        "sap_po_master",
        payload.get("po_number"),
        connection,
    )
    values = _common_document_values(payload, existing)
    raw_json = values["raw_json"]
    values.update(
        po_number=payload.get("po_number"),
        po_date=_as_date(payload.get("po_date")),
        payment_terms=_first_non_empty(
            _normalize_payment_terms(payload.get("payment_terms")),
            raw_json.get("payment_terms") if isinstance(raw_json, dict) else None,
            existing.get("payment_terms"),
        ),
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
    existing = _fetch_existing_row(
        "sap_posted_invoice_master",
        payload.get("invoice_number"),
        connection,
    )
    values = _common_document_values(payload, existing)
    raw_json = values["raw_json"]
    values.update(
        invoice_number=payload.get("invoice_number"),
        invoice_date=_as_date(payload.get("invoice_date")),
        due_date=_as_date(
            _first_non_empty(
                payload.get("due_date"),
                raw_json.get("due_date") if isinstance(raw_json, dict) else None,
                existing.get("due_date"),
            )
        ),
        payment_terms=_first_non_empty(
            _normalize_payment_terms(payload.get("payment_terms")),
            raw_json.get("payment_terms") if isinstance(raw_json, dict) else None,
            existing.get("payment_terms"),
        ),
        payment_status=payload.get("payment_status"),
        sap_document_number=payload.get("sap_document_number"),
        posting_status=payload.get("posting_status", "POSTED"),
        posting_message=payload.get("posting_message"),
        source_system=payload.get("source_system", "AP_AGENT"),
        posted_at=_as_datetime(payload.get("posted_at"))
        or datetime.now(timezone.utc),
    )
    _upsert("sap_posted_invoice_master", values, connection)


def update_payment_terms(
    table_name: str,
    primary_key_value: str,
    payment_terms: str,
) -> dict:
    """Update payment terms on supported AP master tables."""
    supported_tables = {
        "invoice_master",
        "sap_po_master",
        "sap_posted_invoice_master",
    }
    if table_name not in supported_tables:
        allowed = ", ".join(sorted(supported_tables))
        raise ValueError(
            f"Unsupported payment terms table: {table_name!r}. "
            f"Allowed tables: {allowed}"
        )
    if not primary_key_value:
        raise ValueError("primary_key_value is required.")

    normalized_terms = _normalize_payment_terms(payment_terms)
    if not normalized_terms:
        raise ValueError("payment_terms is required.")

    init_master_schema_if_needed()

    table = _get_model(table_name).__table__
    primary_key = next(iter(table.primary_key.columns))

    with get_master_engine().begin() as connection:
        existing_row = connection.execute(
            select(table).where(primary_key == primary_key_value)
        ).mappings().first()
        if existing_row is None:
            return {
                "table_name": table_name,
                "primary_key_value": primary_key_value,
                "payment_terms": normalized_terms,
                "rows_updated": 0,
                "status": "not_found",
            }

        raw_json = _as_json(existing_row.get("raw_json"), {})
        if not isinstance(raw_json, dict):
            raw_json = {}
        raw_json["payment_terms"] = normalized_terms
        raw_json["terms"] = normalized_terms

        values = {
            "payment_terms": normalized_terms,
            "raw_json": _json_safe(raw_json),
        }
        if "updated_at" in table.c:
            values["updated_at"] = datetime.now(timezone.utc)

        result = connection.execute(
            update(table)
            .where(primary_key == primary_key_value)
            .values(**values)
        )

    rows_updated = int(result.rowcount or 0)
    return {
        "table_name": table_name,
        "primary_key_value": primary_key_value,
        "payment_terms": normalized_terms,
        "rows_updated": rows_updated,
        "status": "updated" if rows_updated else "not_found",
    }


def _delete_by_primary_key(table_name: str, value: str) -> None:
    require_destructive_master_reset_allowed(f"delete from {table_name}")

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
    require_destructive_master_reset_allowed(f"clear {table_name}")

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
    require_destructive_master_reset_allowed(f"keep latest rows in {table_name}")

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


def reset_invoice_flow_data() -> dict[str, Any]:
    """Clear invoice data while retaining PO and GRN reference rows."""
    require_destructive_master_reset_allowed("reset invoice flow")
    engine = get_master_engine()
    delete_order = (
        "invoice_master",
        "sap_posted_invoice_master",
    )
    deleted: dict[str, int] = {}
    retained: dict[str, int] = {}
    with engine.begin() as connection:
        for table_name in delete_order:
            result = connection.execute(
                delete(_get_model(table_name).__table__)
            )
            deleted[table_name] = int(result.rowcount or 0)
        for table_name in ("sap_po_master", "sap_grn_master"):
            retained[table_name] = int(
                connection.scalar(
                    select(func.count()).select_from(
                        _get_model(table_name).__table__
                    )
                )
                or 0
            )
    return {
        "status": "success",
        "deleted": deleted,
        "retained": retained,
    }


def reset_demo_environment() -> dict[str, Any]:
    """Clear all master tables in one database transaction."""
    require_destructive_master_reset_allowed("reset demo environment")

    engine = get_master_engine()
    delete_order = (
        "invoice_master",
        "sap_posted_invoice_master",
        "sap_po_master",
        "sap_grn_master",
    )

    deleted: dict[str, int] = {}
    with engine.begin() as connection:
        for table_name in delete_order:
            result = connection.execute(
                delete(_get_model(table_name).__table__)
            )
            deleted[table_name] = int(result.rowcount or 0)

    return {
        "status": "success",
        "deleted": deleted,
        "retained": {},
    }
