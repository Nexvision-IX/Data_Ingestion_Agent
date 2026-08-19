"""Non-destructive compatibility migration for the consumption ledger."""

from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import Engine, inspect, text

from app.services.business_invoice_identity import build_business_invoice_key


def ensure_po_grn_ledger_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    table = "po_grn_consumption_ledger"
    if not inspector.has_table(table):
        return

    columns = {item["name"] for item in inspector.get_columns(table)}
    additions = {
        "business_invoice_key": "VARCHAR(255)",
        "company_code": "VARCHAR(20)",
        "fiscal_year": "INTEGER",
    }
    foreign_keys = inspector.get_foreign_keys(table)
    sqlite_needs_rebuild = (
        engine.dialect.name == "sqlite"
        and not any(
            fk.get("referred_table") == "invoices"
            and str((fk.get("options") or {}).get("ondelete", "")).upper()
            == "CASCADE"
            for fk in foreign_keys
        )
    )
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
                )

        # Legacy SQLite databases may have foreign-key enforcement disabled;
        # remove any existing orphans before establishing/querying integrity.
        connection.execute(
            text(
                "DELETE FROM po_grn_consumption_ledger "
                "WHERE invoice_id NOT IN (SELECT id FROM invoices)"
            )
        )

        rows = connection.execute(
            text(
                "SELECT l.id, l.invoice_number, l.po_number, l.po_item, "
                "l.ledger_status, i.vendor_number, i.vendor_name, "
                "i.invoice_date, i.extraction_raw "
                "FROM po_grn_consumption_ledger l "
                "JOIN invoices i ON i.id = l.invoice_id"
            )
        ).mappings().all()
        seen_reservations: set[tuple[str, str, str]] = set()
        for row in rows:
            invoice_date = row["invoice_date"]
            fiscal_year = _year(invoice_date)
            raw = row["extraction_raw"]
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (TypeError, ValueError):
                    raw = {}
            company_code = (
                raw.get("company_code")
                if isinstance(raw, dict)
                else None
            ) or "1000"
            identity = build_business_invoice_key(
                company_code=company_code,
                vendor_number=row["vendor_number"] or row["vendor_name"],
                invoice_number=row["invoice_number"],
                fiscal_year=fiscal_year,
            )
            active_key = None
            status = str(row["ledger_status"] or "").upper()
            reservation_key = (
                identity,
                str(row["po_number"] or ""),
                str(row["po_item"] or ""),
            )
            if status == "RESERVED":
                if reservation_key in seen_reservations:
                    connection.execute(
                        text(
                            "UPDATE po_grn_consumption_ledger "
                            "SET ledger_status = 'RELEASED', active_key = NULL, "
                            "reason = 'Released by business-key migration: "
                            "duplicate active reservation.' WHERE id = :id"
                        ),
                        {"id": row["id"]},
                    )
                    continue
                seen_reservations.add(reservation_key)
                active_key = ":".join(reservation_key)

            values = {
                "id": row["id"],
                "identity": identity,
                "company_code": company_code,
                "fiscal_year": fiscal_year,
            }
            connection.execute(
                text(
                    "UPDATE po_grn_consumption_ledger SET "
                    "business_invoice_key = :identity, "
                    "company_code = :company_code, "
                    "fiscal_year = :fiscal_year "
                    "WHERE id = :id"
                ),
                values,
            )
            if active_key is not None:
                connection.execute(
                    text(
                        "UPDATE po_grn_consumption_ledger "
                        "SET active_key = :active_key WHERE id = :id"
                    ),
                    {"id": row["id"], "active_key": active_key},
                )

        if sqlite_needs_rebuild:
            _rebuild_sqlite_ledger_with_cascade(connection)
        elif engine.dialect.name == "postgresql":
            connection.execute(
                text(
                    "ALTER TABLE po_grn_consumption_ledger "
                    "ALTER COLUMN active_key TYPE VARCHAR(500)"
                )
            )

        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_po_grn_consumption_ledger_po_number "
                "ON po_grn_consumption_ledger (po_number)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_po_grn_consumption_ledger_invoice_id "
                "ON po_grn_consumption_ledger (invoice_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_po_grn_ledger_business_invoice_key "
                "ON po_grn_consumption_ledger (business_invoice_key)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_po_grn_active_business_reservation "
                "ON po_grn_consumption_ledger "
                "(business_invoice_key, po_number, po_item) "
                "WHERE ledger_status = 'RESERVED'"
            )
        )


def _rebuild_sqlite_ledger_with_cascade(connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE po_grn_consumption_ledger "
            "RENAME TO po_grn_consumption_ledger_legacy"
        )
    )
    connection.execute(
        text(
            "CREATE TABLE po_grn_consumption_ledger ("
            "id VARCHAR(36) NOT NULL PRIMARY KEY, "
            "invoice_id VARCHAR(36) NOT NULL REFERENCES invoices(id) "
            "ON DELETE CASCADE, "
            "invoice_number VARCHAR(100) NOT NULL, "
            "business_invoice_key VARCHAR(255) NOT NULL, "
            "company_code VARCHAR(20) NOT NULL, "
            "fiscal_year INTEGER NOT NULL, "
            "po_number VARCHAR(100) NOT NULL, "
            "po_item VARCHAR(20) NOT NULL, "
            "active_key VARCHAR(500) UNIQUE, "
            "grn_number VARCHAR(100), "
            "quantity NUMERIC(18, 4) NOT NULL, "
            "amount NUMERIC(18, 2) NOT NULL, "
            "ledger_status VARCHAR(30) NOT NULL, "
            "source VARCHAR(40) NOT NULL, "
            "reason TEXT NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL)"
        )
    )
    columns = (
        "id, invoice_id, invoice_number, business_invoice_key, "
        "company_code, fiscal_year, po_number, po_item, active_key, "
        "grn_number, quantity, amount, ledger_status, source, reason, "
        "created_at, updated_at"
    )
    connection.execute(
        text(
            f"INSERT INTO po_grn_consumption_ledger ({columns}) "
            f"SELECT {columns} FROM po_grn_consumption_ledger_legacy"
        )
    )
    connection.execute(
        text("DROP TABLE po_grn_consumption_ledger_legacy")
    )


def _year(value) -> int:
    if isinstance(value, (date, datetime)):
        return value.year
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return 1900
