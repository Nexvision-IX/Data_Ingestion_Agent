"""Focused deterministic tests for GRN status validation."""

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
from app.agents.classification_agent import ClassificationAgent
from app.integrations.llm.mock import MockLLMClient
from app.rules.validation import APValidationEngine


def main() -> int:
    _assert_status_passes("Posted")
    _assert_status_passes("Received")
    _assert_status_passes("Partially Received")
    _assert_status_passes("Partial")
    _assert_status_passes("Completed")
    _assert_status_passes("Approved")
    _assert_status_fails("Open")

    for status in (
        "Pending",
        "Draft",
        "Open",
        "Cancelled",
        "Canceled",
        "Reversed",
        "Rejected",
        "Void",
        None,
        "Unexpected Status",
    ):
        _assert_status_fails(status)

    missing_results = _validate_without_grn()
    assert missing_results["GRN-001"].passed is True
    assert missing_results["GRN-001"].message == (
        "Skipped because no GRN exists."
    )
    assert missing_results["AP-006"].passed is False
    assert missing_results["AP-007"].passed is False
    category = _primary_category(missing_results)
    assert category == "GRN_MISSING", category

    invalid_results = _validate("Open", received_quantity=10)
    assert invalid_results["GRN-001"].passed is False
    assert "Open" in invalid_results["GRN-001"].message
    category = _primary_category(invalid_results)
    assert category == "GRN_STATUS_INVALID", category

    valid_results = _validate("Posted", received_quantity=10)
    assert valid_results["GRN-001"].passed is True

    vendor_results = _validate_vendor_mismatch()
    assert vendor_results["AP-004"].passed is False
    category = _primary_category(vendor_results)
    assert category == "VENDOR_MISMATCH", category

    print("[SUCCESS] GRN status validation tests passed.")
    return 0


def _assert_status_passes(status) -> None:
    results = _validate(status, received_quantity=10)
    assert results["GRN-001"].passed is True, status
    assert results["AP-006"].passed is True, status
    assert results["AP-007"].passed is True, status


def _assert_status_fails(status) -> None:
    results = _validate(status, received_quantity=10)
    assert results["GRN-001"].passed is False, status
    assert results["AP-006"].passed is True, status
    assert results["AP-007"].passed is False, status


def _validate(status, received_quantity: float):
    invoice = _invoice()
    context = {
        "po": {
            "po_number": invoice.po_number,
            "vendor_number": invoice.vendor_number,
            "currency": invoice.currency,
            "payment_terms": invoice.payment_terms,
            "status": "Open",
            "items": [
                {
                    "po_item": "00001",
                    "unit_price": 10,
                }
            ],
        },
        "vendor": {
            "vendor_number": invoice.vendor_number,
            "status": "ACTIVE",
        },
        "grns": [
            {
                "grn_number": "GRN-TEST-001",
                "po_number": invoice.po_number,
                "po_item": "00001",
                "received_quantity": received_quantity,
                "status": status,
            }
        ],
        "invoice_history": [],
    }
    return {
        result.rule_code: result
        for result in APValidationEngine().validate(invoice, context)
    }


def _validate_without_grn():
    invoice = _invoice()
    context = {
        "po": {
            "po_number": invoice.po_number,
            "vendor_number": invoice.vendor_number,
            "currency": invoice.currency,
            "payment_terms": invoice.payment_terms,
            "status": "Open",
            "items": [
                {
                    "po_item": "00001",
                    "unit_price": 10,
                }
            ],
        },
        "vendor": {
            "vendor_number": invoice.vendor_number,
            "status": "ACTIVE",
        },
        "grns": [],
        "invoice_history": [],
    }
    return {
        result.rule_code: result
        for result in APValidationEngine().validate(invoice, context)
    }


def _validate_vendor_mismatch():
    invoice = _invoice()
    invoice.invoice_number = "INV-VENDOR-MISMATCH-001"
    invoice.po_number = "PO-VENDOR-001"
    invoice.vendor_name = "Wrong Vendor Ltd"
    invoice.vendor_number = "WRONG_VENDOR_LTD"
    context = {
        "po": {
            "po_number": invoice.po_number,
            "vendor_name": "Correct Vendor Ltd",
            "vendor_number": "CORRECT_VENDOR_LTD",
            "currency": invoice.currency,
            "payment_terms": invoice.payment_terms,
            "status": "Open",
            "items": [
                {
                    "po_item": "00001",
                    "unit_price": 10,
                }
            ],
        },
        "vendor": {
            "vendor_name": "Correct Vendor Ltd",
            "vendor_number": "CORRECT_VENDOR_LTD",
            "status": "ACTIVE",
        },
        "grns": [
            {
                "grn_number": "GRN-VENDOR-001",
                "po_number": invoice.po_number,
                "po_item": "00001",
                "received_quantity": 10,
                "status": "Posted",
            }
        ],
        "invoice_history": [],
    }
    return {
        result.rule_code: result
        for result in APValidationEngine().validate(invoice, context)
    }


def _primary_category(results: dict):
    failures = [
        result.to_dict()
        for result in results.values()
        if result.severity == "ERROR"
        and not result.passed
    ]
    classification = ClassificationAgent(MockLLMClient()).classify(
        {"invoice_number": "TEST"},
        failures,
    )
    return classification.category


def _invoice() -> Invoice:
    invoice = Invoice(
        source="TEST",
        original_filename="grn-test.json",
        file_path=None,
        vendor_name="GRN Test Vendor",
        vendor_number="GRN_VENDOR",
        invoice_number="GRN-TEST-INVOICE",
        invoice_date=date(2026, 6, 24),
        po_number="PO-GRN-001",
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
            description="GRN test line",
            quantity=10,
            unit_price=10,
            tax_rate=18,
            po_item="00001",
        )
    )
    return invoice


if __name__ == "__main__":
    raise SystemExit(main())
