"""Focused checks for S02 vendor cleanup and payment-term consistency."""

from __future__ import annotations

import ast
import json
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
from app.services.orchestrator import APOrchestrator
from app.services.payment_terms_control import PaymentTermsControl
from app.services.vendor_master_control import normalize_vendor_identity


def main() -> int:
    _check_vendor_label_normalization()
    _check_s02_missing_grn_vendor_passes()
    _check_s03_vendor_mismatch_still_fails()
    _check_seeded_po_payment_terms()
    _check_net60_still_fails_against_net30()
    _check_posted_invoice_payment_terms_payload()
    print("[SUCCESS] S02 vendor and payment-term consistency checks passed.")
    return 0


def _check_vendor_label_normalization() -> None:
    assert (
        normalize_vendor_identity("SUPPLIER GRN MISSING VENDOR LTD")
        == normalize_vendor_identity("GRN Missing Vendor Ltd")
    )
    assert (
        normalize_vendor_identity("Supplier Clean Supplies Ltd")
        == normalize_vendor_identity("Clean Supplies Limited")
    )
    assert (
        normalize_vendor_identity("Wrong Vendor Ltd")
        != normalize_vendor_identity("Correct Vendor Ltd")
    )


def _check_s02_missing_grn_vendor_passes() -> None:
    results = _by_code(
        APValidationEngine().validate(
            _invoice(
                invoice_number="INV-GRN-MISSING-001",
                po_number="PO-GRN-MISSING-001",
                vendor_name="SUPPLIER GRN MISSING VENDOR LTD",
                vendor_number="SUPPLIER_GRN_MISSING_VENDOR_LTD",
                payment_terms="NET 30",
            ),
            _context(
                po_number="PO-GRN-MISSING-001",
                po_vendor_name="GRN Missing Vendor Ltd",
                po_vendor_number="GRN_MISSING_VENDOR_LTD",
                grns=[],
            ),
        )
    )

    assert results["AP-001"].passed is True
    assert results["AP-004"].passed is True
    assert results["VND-003"].passed is True
    assert results["AP-006"].passed is False
    assert results["GRN-001"].passed is True
    assert results["GRN-001"].message == "Skipped because no GRN exists."
    category = _primary_category(results)
    assert category == "GRN_MISSING", category
    assert results["AP-010"].passed is True

    unexpected_failures = [
        code
        for code, result in results.items()
        if result.severity == "ERROR"
        and not result.passed
        and code not in {"AP-006", "AP-007"}
    ]
    assert unexpected_failures == []


def _check_s03_vendor_mismatch_still_fails() -> None:
    results = _by_code(
        APValidationEngine().validate(
            _invoice(
                invoice_number="INV-VENDOR-MISMATCH-001",
                po_number="PO-VENDOR-001",
                vendor_name="Wrong Vendor Ltd",
                vendor_number="WRONG_VENDOR_LTD",
                payment_terms="NET 30",
            ),
            _context(
                po_number="PO-VENDOR-001",
                po_vendor_name="Correct Vendor Ltd",
                po_vendor_number="CORRECT_VENDOR_LTD",
                grns=[
                    {
                        "grn_number": "GRN-VENDOR-001",
                        "po_number": "PO-VENDOR-001",
                        "po_item": "00001",
                        "received_quantity": 5,
                        "status": "POSTED",
                    }
                ],
            ),
        )
    )

    assert results["AP-004"].passed is False
    assert results["VND-003"].passed is False


def _check_seeded_po_payment_terms() -> None:
    pos = json.loads(
        (PROJECT_ROOT / "mock_api" / "mock_data" / "pos.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(pos) == 23
    assert [
        po.get("po_number")
        for po in pos
        if po.get("payment_terms") != "NET 30"
    ] == []

    seed_source = (PROJECT_ROOT / "seed_mock_api_test_pos_grns.py").read_text(
        encoding="utf-8"
    )
    seed_tree = ast.parse(seed_source)
    assigned_names = {
        target.id
        for node in seed_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "PO_PAYLOADS" in assigned_names
    assert "GRN_PAYLOADS" in assigned_names

    grns = json.loads(
        (PROJECT_ROOT / "mock_api" / "mock_data" / "grns.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(grns) == 21
    missing_grn_po_numbers = {"PO-GRN-MISSING-001", "PO-RESP-GENERAL-001"}
    assert {
        grn.get("po_number")
        for grn in grns
        if grn.get("po_number") in missing_grn_po_numbers
    } == set()


def _check_net60_still_fails_against_net30() -> None:
    for invoice_number in ("INV-PAYTERMS-001", "INV-RESP-TERMS-001"):
        results = _by_code(
            PaymentTermsControl().evaluate(
                _invoice(
                    invoice_number=invoice_number,
                    po_number="PO-PAYTERMS-001",
                    vendor_name="Payment Terms Vendor Ltd",
                    vendor_number="PAYMENT_TERMS_VENDOR_LTD",
                    payment_terms="NET 60",
                ),
                {
                    "po": {"payment_terms": "NET 30"},
                    "vendor": {"payment_terms": "NET 30"},
                },
            )
        )
        assert results["PAY-002"].passed is False


def _check_posted_invoice_payment_terms_payload() -> None:
    orchestrator = object.__new__(APOrchestrator)
    invoice_terms_payload = APOrchestrator._posted_invoice_payload(
        orchestrator,
        _invoice(
            invoice_number="INV-POSTED-TERMS-001",
            po_number="PO-POSTED-TERMS-001",
            vendor_name="Posted Terms Vendor Ltd",
            vendor_number="POSTED_TERMS_VENDOR_LTD",
            payment_terms="NET 60",
        ),
        "SAP-POSTED-001",
        "Posted.",
        {"po": {"payment_terms": "NET 30"}},
    )
    assert invoice_terms_payload["payment_terms"] == "NET 60"

    po_terms_payload = APOrchestrator._posted_invoice_payload(
        orchestrator,
        _invoice(
            invoice_number="INV-POSTED-PO-TERMS-001",
            po_number="PO-POSTED-PO-TERMS-001",
            vendor_name="Posted PO Terms Vendor Ltd",
            vendor_number="POSTED_PO_TERMS_VENDOR_LTD",
            payment_terms=None,
        ),
        "SAP-POSTED-002",
        "Posted.",
        {"po": {"payment_terms": "NET 30"}},
    )
    assert po_terms_payload["payment_terms"] == "NET 30"


def _invoice(
    *,
    invoice_number: str,
    po_number: str,
    vendor_name: str,
    vendor_number: str,
    payment_terms: str | None,
) -> Invoice:
    invoice = Invoice(
        source="TEST",
        original_filename=f"{invoice_number}.json",
        file_path=None,
        vendor_name=vendor_name,
        vendor_number=vendor_number,
        invoice_number=invoice_number,
        invoice_date=date(2026, 6, 15),
        po_number=po_number,
        currency="INR",
        subtotal=10000,
        tax_amount=1800,
        total_amount=11800,
        payment_terms=payment_terms,
        status="VALIDATION_IN_PROGRESS",
        extraction_confidence=1,
        extraction_raw={},
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            description="Scenario test line",
            quantity=5,
            unit_price=2000,
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
    grns: list[dict],
) -> dict:
    return {
        "po": {
            "po_number": po_number,
            "vendor_number": po_vendor_number,
            "vendor_name": po_vendor_name,
            "currency": "INR",
            "payment_terms": "NET 30",
            "status": "Open",
            "items": [
                {
                    "po_item": "00001",
                    "ordered_quantity": 5,
                    "unit_price": 2000,
                }
            ],
        },
        "vendor": {
            "vendor_number": po_vendor_number,
            "vendor_name": po_vendor_name,
            "status": "Active",
            "tax_id": "GST-SCENARIO-001",
            "payment_terms": "NET 30",
            "source": "TEST_VENDOR_CONTEXT",
        },
        "grns": grns,
        "invoice_history": [],
    }


def _by_code(results):
    return {result.rule_code: result for result in results}


def _primary_category(results: dict) -> str:
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


if __name__ == "__main__":
    raise SystemExit(main())
