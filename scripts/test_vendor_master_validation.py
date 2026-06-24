"""Focused deterministic tests for vendor master validation."""

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
    active = _validate("Approved")
    assert active["VND-001"].passed is True
    assert active["VND-002"].passed is True
    assert active["VND-003"].passed is True

    missing = _validate(None, include_vendor=False)
    assert missing["VND-001"].passed is False
    assert missing["VND-002"].passed is False
    assert missing["VND-003"].passed is False

    for status in (
        "Blocked",
        "Suspended",
        "On Hold",
        "Payment Hold",
        "Inactive",
        "Disabled",
        "Closed",
        "Pending",
        "Draft",
        "Under Review",
    ):
        results = _validate(status)
        assert results["VND-001"].passed is True, status
        assert results["VND-002"].passed is False, status

    mismatch = _validate(
        "Active",
        invoice_vendor_number="V999",
        invoice_vendor_name="Different Vendor",
    )
    assert mismatch["VND-003"].passed is False

    incomplete = _validate(
        "Active",
        tax_id=None,
        payment_terms=None,
    )
    assert incomplete["VND-004"].passed is True
    assert incomplete["VND-004"].severity == "WARNING"
    assert set(
        incomplete["VND-004"].details["missing_fields"]
    ) == {"tax_details", "payment_details"}

    complete = _validate(
        "Enabled",
        tax_id="GST-TEST-001",
        payment_terms="NET30",
    )
    assert complete["VND-004"].passed is True
    assert complete["VND-004"].details["missing_fields"] == []

    print("[SUCCESS] Vendor master validation tests passed.")
    return 0


def _validate(
    vendor_status,
    *,
    include_vendor: bool = True,
    invoice_vendor_number: str = "V100",
    invoice_vendor_name: str = "Acme Industrial Supplies",
    tax_id: str | None = "GST-100",
    payment_terms: str | None = "NET30",
):
    invoice = _invoice(
        invoice_vendor_number,
        invoice_vendor_name,
    )
    vendor = None
    if include_vendor:
        vendor = {
            "vendor_number": "V100",
            "vendor_name": "Acme Industrial Supplies",
            "status": vendor_status,
            "tax_id": tax_id,
            "payment_terms": payment_terms,
            "source": "TEST_VENDOR_CONTEXT",
        }
    context = {
        "po": {
            "po_number": invoice.po_number,
            "vendor_number": "V100",
            "vendor_name": "Acme Industrial Supplies",
            "currency": invoice.currency,
            "payment_terms": "NET30",
            "status": "OPEN",
            "items": [
                {
                    "po_item": "00001",
                    "ordered_quantity": 10,
                    "unit_price": 10,
                }
            ],
        },
        "vendor": vendor,
        "grns": [
            {
                "grn_number": "GRN-VENDOR-001",
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


def _invoice(
    vendor_number: str,
    vendor_name: str,
) -> Invoice:
    invoice = Invoice(
        source="TEST",
        original_filename="vendor-test.json",
        file_path=None,
        vendor_name=vendor_name,
        vendor_number=vendor_number,
        invoice_number="VENDOR-TEST-INVOICE",
        invoice_date=date(2026, 6, 24),
        po_number="PO-VENDOR-001",
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
            description="Vendor test line",
            quantity=10,
            unit_price=10,
            tax_rate=18,
            po_item="00001",
        )
    )
    return invoice


if __name__ == "__main__":
    raise SystemExit(main())
