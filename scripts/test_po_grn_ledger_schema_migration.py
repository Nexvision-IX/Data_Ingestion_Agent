"""Regression test for upgrading a legacy SQLite consumption ledger."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent_app"), str(ROOT)]


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        url = f"sqlite:///{Path(temp_dir, 'legacy.db').as_posix()}"
        os.environ["DATABASE_URL"] = url
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE invoices ("
                    "id VARCHAR(36) PRIMARY KEY, vendor_number VARCHAR(50), "
                    "vendor_name VARCHAR(255), invoice_date DATE, "
                    "extraction_raw JSON)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE po_grn_consumption_ledger ("
                    "id VARCHAR(36) PRIMARY KEY, invoice_id VARCHAR(36), "
                    "invoice_number VARCHAR(100), po_number VARCHAR(100), "
                    "po_item VARCHAR(20), active_key VARCHAR(100), "
                    "grn_number VARCHAR(100), quantity NUMERIC, amount NUMERIC, "
                    "ledger_status VARCHAR(30), source VARCHAR(40), "
                    "reason TEXT, created_at DATETIME, updated_at DATETIME)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO invoices VALUES "
                    "('invoice-1', 'V100', 'Vendor', '2026-07-20', '{}')"
                )
            )
            for ledger_id, invoice_id in (
                ("valid-ledger", "invoice-1"),
                ("orphan-ledger", "missing-invoice"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO po_grn_consumption_ledger VALUES "
                        "(:id, :invoice_id, 'INV-001', 'PO-001', '00001', "
                        ":id, 'GRN-001', 10, 100, 'RESERVED', 'AP_AGENT', "
                        "'legacy', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"id": ledger_id, "invoice_id": invoice_id},
                )

        from app.services.po_grn_ledger_schema_service import (
            ensure_po_grn_ledger_schema,
        )

        ensure_po_grn_ledger_schema(engine)
        schema = inspect(engine)
        columns = {
            item["name"]
            for item in schema.get_columns("po_grn_consumption_ledger")
        }
        assert {
            "business_invoice_key",
            "company_code",
            "fiscal_year",
        } <= columns
        indexes = {
            item["name"]
            for item in schema.get_indexes("po_grn_consumption_ledger")
        }
        assert "ix_po_grn_ledger_business_invoice_key" in indexes
        with engine.connect() as connection:
            foreign_keys = connection.execute(
                text(
                    "PRAGMA foreign_key_list("
                    "'po_grn_consumption_ledger')"
                )
            ).all()
            assert any(
                row[2] == "invoices" and str(row[6]).upper() == "CASCADE"
                for row in foreign_keys
            )
            rows = connection.execute(
                text(
                    "SELECT id, business_invoice_key "
                    "FROM po_grn_consumption_ledger"
                )
            ).mappings().all()
        assert len(rows) == 1
        assert rows[0]["id"] == "valid-ledger"
        assert rows[0]["business_invoice_key"] == "1000|V100|INV001|2026"
        engine.dispose()

    print("[SUCCESS] PO/GRN ledger schema migration test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
