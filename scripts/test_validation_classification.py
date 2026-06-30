"""Focused validation/classification checks for scenario root causes."""

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

from app.agents.classification_agent import ClassificationAgent
from app.integrations.llm.mock import MockLLMClient
from app.models import Invoice, InvoiceLine
from app.rules.validation import APValidationEngine
from app.services.invoice_financial_control import InvoiceFinancialControl


def main() -> int:
    _check_s02_missing_grn_is_primary()
    _check_invalid_grn_status_is_primary()
    _check_valid_grn_status_passes()
    _check_s03_vendor_mismatch_is_primary()
    _check_financial_vs_amount_mismatch()
    print("[SUCCESS] validation classification tests passed.")
    return 0


def _check_s02_missing_grn_is_primary() -> None:
    invoice = _invoice(
        invoice_number="INV-GRN-MISSING-001",
        po_number="PO-GRN-MISSING-001",
        vendor_name="SUPPLIER GRN MISSING VENDOR LTD",
        vendor_number="SUPPLIER_GRN_MISSING_VENDOR_LTD",
        description="Consulting services",
        quantity=5,
        unit_price=2000,
        subtotal=10000,
        tax_amount=1800,
        total_amount=11800,
    )
    results = _run_rules(invoice, _context(
        po_number="PO-GRN-MISSING-001",
        po_vendor_name="GRN Missing Vendor Ltd",
        po_vendor_number="GRN_MISSING_VENDOR_LTD",
        po_quantity=5,
        po_unit_price=2000,
        grns=[],
    ))
    assert results["AP-006"].passed is False
    assert results["GRN-001"].passed is True
    category = _primary_category(results)
    assert category == "GRN_MISSING", category


def _check_invalid_grn_status_is_primary() -> None:
    invoice = _invoice(
        invoice_number="INV-PENDING-GRN-001",
        po_number="PO-PENDING-GRN-001",
        vendor_name="Pending GRN Vendor Ltd",
        vendor_number="PENDING_GRN_VENDOR_LTD",
        description="Goods awaiting receipt",
        quantity=8,
        unit_price=1250,
        subtotal=10000,
        tax_amount=1800,
        total_amount=11800,
    )
    results = _run_rules(invoice, _context(
        po_number="PO-PENDING-GRN-001",
        po_vendor_name="Pending GRN Vendor Ltd",
        po_vendor_number="PENDING_GRN_VENDOR_LTD",
        po_quantity=8,
        po_unit_price=1250,
        grns=[_grn("GRN-PENDING-001", "PO-PENDING-GRN-001", "Pending", 8)],
    ))
    assert results["AP-006"].passed is True
    assert results["GRN-001"].passed is False
    assert "Pending" in results["GRN-001"].message
    category = _primary_category(results)
    assert category == "GRN_STATUS_INVALID", category


def _check_valid_grn_status_passes() -> None:
    invoice = _invoice(
        invoice_number="INV-CLEAN-001",
        po_number="PO-CLEAN-001",
        vendor_name="Clean Supplies Ltd",
        vendor_number="CLEAN_SUPPLIES_LTD",
        description="Office supplies",
        quantity=10,
        unit_price=1000,
        subtotal=10000,
        tax_amount=1800,
        total_amount=11800,
    )
    results = _run_rules(invoice, _context(
        po_number="PO-CLEAN-001",
        po_vendor_name="Clean Supplies Ltd",
        po_vendor_number="CLEAN_SUPPLIES_LTD",
        po_quantity=10,
        po_unit_price=1000,
        grns=[_grn("GRN-CLEAN-001", "PO-CLEAN-001", "Received", 10)],
    ))
    assert results["GRN-001"].passed is True


def _check_s03_vendor_mismatch_is_primary() -> None:
    invoice = _invoice(
        invoice_number="INV-VENDOR-MISMATCH-001",
        po_number="PO-VENDOR-001",
        vendor_name="Wrong Vendor Ltd",
        vendor_number="WRONG_VENDOR_LTD",
        description="Laptop accessories",
        quantity=4,
        unit_price=2500,
        subtotal=10000,
        tax_amount=1800,
        total_amount=11800,
    )
    results = _run_rules(invoice, _context(
        po_number="PO-VENDOR-001",
        po_vendor_name="Correct Vendor Ltd",
        po_vendor_number="CORRECT_VENDOR_LTD",
        po_quantity=4,
        po_unit_price=2500,
        grns=[_grn("GRN-VENDOR-001", "PO-VENDOR-001", "Received", 4)],
    ))
    assert results["AP-004"].passed is False
    category = _primary_category(results)
    assert category == "VENDOR_MISMATCH", category


def _check_financial_vs_amount_mismatch() -> None:
    inconsistent = _invoice(
        invoice_number="INV-INTERNAL-MATH-001",
        po_number="PO-AMOUNT-001",
        vendor_name="Amount Test Vendor Ltd",
        vendor_number="AMOUNT_TEST_VENDOR_LTD",
        description="Professional services",
        quantity=6,
        unit_price=1750,
        subtotal=11000,
        tax_amount=1980,
        total_amount=12980,
    )
    inconsistent_results = _run_rules(
        inconsistent,
        _context(
            po_number="PO-AMOUNT-001",
            po_vendor_name="Amount Test Vendor Ltd",
            po_vendor_number="AMOUNT_TEST_VENDOR_LTD",
            po_quantity=6,
            po_unit_price=1750,
            grns=[_grn("GRN-AMOUNT-001", "PO-AMOUNT-001", "Received", 6)],
        ),
    )
    assert inconsistent_results["FIN-002"].passed is False
    category = _primary_category(inconsistent_results)
    assert category == "FINANCIAL_MISMATCH", category

    s04 = _invoice(
        invoice_number="INV-AMOUNT-MISMATCH-001",
        po_number="PO-AMOUNT-001",
        vendor_name="Amount Test Vendor Ltd",
        vendor_number="AMOUNT_TEST_VENDOR_LTD",
        description="Professional services",
        quantity=6,
        unit_price=1750,
        subtotal=10500,
        tax_amount=1890,
        total_amount=12390,
    )
    s04_results = _run_rules(s04, _amount_context())
    for code in ("FIN-001", "FIN-002", "FIN-003", "FIN-004", "FIN-005"):
        assert s04_results[code].passed is True, code
    assert s04_results["AP-008"].passed is False
    category = _primary_category(s04_results)
    assert category == "PRICE_AMOUNT_MISMATCH", category


def _run_rules(invoice: Invoice, context: dict) -> dict:
    results = APValidationEngine().validate(invoice, context)
    results.extend(InvoiceFinancialControl(tolerance="0.01").evaluate(invoice))
    return {result.rule_code: result for result in results}


def _primary_category(results: dict) -> str:
    failed = [
        result.to_dict()
        for result in results.values()
        if result.severity == "ERROR"
        and not result.passed
    ]
    classification = ClassificationAgent(MockLLMClient()).classify(
        {"invoice_number": "TEST"},
        failed,
    )
    return classification.category


def _invoice(
    *,
    invoice_number: str,
    po_number: str,
    vendor_name: str,
    vendor_number: str,
    description: str,
    quantity: float,
    unit_price: float,
    subtotal: float,
    tax_amount: float,
    total_amount: float,
) -> Invoice:
    invoice = Invoice(
        source="TEST",
        original_filename=f"{invoice_number}.json",
        file_path=None,
        vendor_name=vendor_name,
        vendor_number=vendor_number,
        invoice_number=invoice_number,
        invoice_date=date(2026, 6, 29),
        po_number=po_number,
        currency="INR",
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount,
        payment_terms="NET 30",
        status="VALIDATION_IN_PROGRESS",
        extraction_confidence=1,
        extraction_raw={},
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            tax_rate=18,
            po_item="00001",
        )
    )
    return invoice


def _context(
    *,
    po_number: str,
    po_vendor_name: str,
    po_vendor_number: str,
    po_quantity: float,
    po_unit_price: float,
    grns: list[dict],
) -> dict:
    return {
        "po": {
            "po_number": po_number,
            "vendor_name": po_vendor_name,
            "vendor_number": po_vendor_number,
            "currency": "INR",
            "payment_terms": "NET 30",
            "status": "Open",
            "items": [
                {
                    "po_item": "00001",
                    "ordered_quantity": po_quantity,
                    "unit_price": po_unit_price,
                }
            ],
        },
        "vendor": {
            "vendor_name": po_vendor_name,
            "vendor_number": po_vendor_number,
            "status": "Active",
            "payment_terms": "NET 30",
        },
        "grns": grns,
        "invoice_history": [],
    }


def _amount_context() -> dict:
    return _context(
        po_number="PO-AMOUNT-001",
        po_vendor_name="Amount Test Vendor Ltd",
        po_vendor_number="AMOUNT_TEST_VENDOR_LTD",
        po_quantity=6,
        po_unit_price=1666.6666666666667,
        grns=[_grn("GRN-AMOUNT-001", "PO-AMOUNT-001", "Received", 6)],
    )


def _grn(
    grn_number: str,
    po_number: str,
    status: str,
    quantity: float,
) -> dict:
    return {
        "grn_number": grn_number,
        "po_number": po_number,
        "po_item": "00001",
        "received_quantity": quantity,
        "status": status,
        "raw_status": status,
    }


if __name__ == "__main__":
    raise SystemExit(main())
