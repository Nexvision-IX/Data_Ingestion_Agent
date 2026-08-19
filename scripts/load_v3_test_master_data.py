"""Load v3 demo master data from source JSON fixtures.

Demo/local use only. This script upserts source-controlled PO/GRN fixtures into
the configured master database and seeds deterministic posted-invoice duplicate
references directly into sap_posted_invoice_master.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("APP_ENV", "development")

from ap_database.master_repository import (  # noqa: E402
    get_table_count,
    init_master_schema_if_needed,
    upsert_grn,
    upsert_po,
    upsert_posted_invoice,
)
from ingestion.master_ingestion import get_conn  # noqa: E402


MOCK_DATA_DIR = PROJECT_ROOT / "mock_api" / "mock_data"

POSTED_DUPLICATE_SEEDS = [
    {
        "document_type": "posted_invoice",
        "invoice_number": "INV-POSTED-DUP-001",
        "po_number": "PO-POSTED-DUP-001",
        "vendor_name": "Posted Duplicate Vendor Ltd",
        "vendor_number": "POSTED_DUPLICATE_VENDOR_LTD",
        "invoice_date": "2026-06-29",
        "currency": "INR",
        "document_subtotal": 10000.0,
        "tax_amount": 1800.0,
        "vat_percent": 18.0,
        "document_total": 11800.0,
        "payment_terms": "NET 30",
        "payment_status": "UNKNOWN",
        "posting_status": "POSTED",
        "sap_document_number": "5100000001",
        "posting_message": "Deterministic v3 duplicate seed.",
        "source_system": "V3_TEST_SEED",
        "posted_at": "2026-06-29T10:00:00+00:00",
        "line_items": [
            {
                "line_no": 1,
                "description": "Previously posted service",
                "qty": 10,
                "unit_price": 1000.0,
                "line_amount": 10000.0,
            }
        ],
    }
]


def _load_list(filename: str) -> list[dict]:
    rows = json.loads((MOCK_DATA_DIR / filename).read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError(f"{filename} must contain a top-level list.")
    return rows


def _validate_source(pos: list[dict], grns: list[dict]) -> None:
    bad_terms = [
        po.get("po_number")
        for po in pos
        if po.get("payment_terms") != "NET 30"
    ]
    if bad_terms:
        raise AssertionError(f"POs without NET 30: {bad_terms}")

    s02_grns = [
        grn.get("gr_number")
        for grn in grns
        if grn.get("po_number") == "PO-GRN-MISSING-001"
    ]
    if s02_grns:
        raise AssertionError(f"S02 must not have GRNs: {s02_grns}")


def main() -> int:
    pos = _load_list("pos.json")
    grns = _load_list("grns.json")
    _validate_source(pos, grns)

    init_master_schema_if_needed()

    with get_conn() as connection:
        for po in pos:
            upsert_po(po, connection=connection)
        for grn in grns:
            upsert_grn(grn, connection=connection)
        for posted in POSTED_DUPLICATE_SEEDS:
            upsert_posted_invoice(posted, connection=connection)
        connection.commit()

    print("[SUCCESS] v3 test master data loaded.")
    print(f"sap_po_master rows: {get_table_count('sap_po_master')}")
    print(f"sap_grn_master rows: {get_table_count('sap_grn_master')}")
    print(
        "sap_posted_invoice_master rows: "
        f"{get_table_count('sap_posted_invoice_master')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


