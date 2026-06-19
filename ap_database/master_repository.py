"""Read-only repository helpers for AP master tables."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from ap_database.engines import (
    get_master_engine,
    get_master_session_factory,
)
from ap_database.master_models import MASTER_TABLE_MODELS


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
