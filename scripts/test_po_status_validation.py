"""Focused deterministic tests for PO status validation."""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = PROJECT_ROOT / "agent_app"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(AGENT_ROOT))
os.environ.setdefault("APP_ENV", "test")

from app.models import Invoice, InvoiceLine
from app.rules.validation import APValidationEngine


def main() -> int:
    for status in (
        "Open",
        "Released",
        "Approved",
        "Active",
        "Partially Open",
        "Partially Received",
    ):
        _assert_status_passes(status)

    for status in (
        "Pending",
        "Draft",
        "Awaiting Approval",
        "Unreleased",
        "Closed",
        "Completed",
        "Fully Invoiced",
        "Cancelled",
        "Canceled",
        "Rejected",
        "Blocked",
        "On Hold",
        "Void",
        None,
        "Unexpected Status",
    ):
        _assert_status_fails(status)

    missing_po = _validate(None, include_po=False)
    assert missing_po["AP-001"].passed is False
    assert missing_po["PO-001"].passed is False

    print("[SUCCESS] PO status validation tests passed.")
    return 0


def _assert_status_passes(status) -> None:
    results = _validate(status)
    assert results["AP-001"].passed is True, status
    assert results["PO-001"].passed is True, status


def _assert_status_fails(status) -> None:
    results = _validate(status)
    assert results["AP-001"].passed is True, status
    assert results["PO-001"].passed is False, status


def _validate(status, include_po: bool = True):
    invoice = _invoice()
    po = None
    if include_po:
        po = {
            "po_number": invoice.po_number,
            "vendor_number": invoice.vendor_number,
            "currency": invoice.currency,
            "payment_terms": invoice.payment_terms,
            "status": status,
            "items": [
                {
                    "po_item": "00001",
                    "unit_price": 10,
                }
            ],
        }

    context = {
        "po": po,
        "vendor": {
            "vendor_number": invoice.vendor_number,
            "status": "ACTIVE",
        },
        "grns": [
            {
                "grn_number": "GRN-PO-TEST-001",
                "po_number": invoice.po_number,
                "po_item": "00001",
                "received_quantity": 10,
                "status": "POSTED",
            }
        ],
        "invoice_history": [],
    }
    return {
        result.rule_code: result
        for result in APValidationEngine().validate(invoice, context)
    }


def _invoice() -> Invoice:
    invoice = Invoice(
        source="TEST",
        original_filename="po-test.json",
        file_path=None,
        vendor_name="PO Test Vendor",
        vendor_number="PO_VENDOR",
        invoice_number="PO-TEST-INVOICE",
        invoice_date=date(2026, 6, 24),
        po_number="PO-STATUS-001",
        currency="INR",
        subtotal=100,
        tax_amount=18,
        total_amount=118,
        payment_terms="NET30",
        status="VALIDATION_IN_PROGRESS",
        extraction_confidence=1,
        extraction_raw={},
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            description="PO status test line",
            quantity=10,
            unit_price=10,
            tax_rate=18,
            po_item="00001",
        )
    )
    return invoice


if __name__ == "__main__":
    raise SystemExit(main())
