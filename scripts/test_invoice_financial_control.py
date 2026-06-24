"""Focused deterministic tests for invoice financial validation."""

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
from app.services.invoice_financial_control import InvoiceFinancialControl


def main() -> int:
    control = InvoiceFinancialControl(tolerance="0.01")

    clean = _invoice(
        subtotal=25,
        tax_amount=4.5,
        total_amount=29.5,
        lines=[(2, 10, 18), (1, 5, 18)],
    )
    clean_results = _by_code(control.evaluate(clean))
    assert all(result.passed for result in clean_results.values())

    incorrect_subtotal = _invoice(
        subtotal=24,
        tax_amount=4.5,
        total_amount=28.5,
        lines=[(2, 10, 18), (1, 5, 18)],
    )
    assert (
        _by_code(control.evaluate(incorrect_subtotal))["FIN-002"].passed
        is False
    )

    incorrect_tax = _invoice(
        subtotal=25,
        tax_amount=4,
        total_amount=29,
        lines=[(2, 10, 18), (1, 5, 18)],
    )
    assert (
        _by_code(control.evaluate(incorrect_tax))["FIN-003"].passed
        is False
    )

    incorrect_total = _invoice(
        subtotal=25,
        tax_amount=4.5,
        total_amount=30,
        lines=[(2, 10, 18), (1, 5, 18)],
    )
    assert (
        _by_code(control.evaluate(incorrect_total))["FIN-004"].passed
        is False
    )

    rounding = _invoice(
        subtotal=1,
        tax_amount=0.18,
        total_amount=1.18,
        lines=[(3, 0.333, 18)],
    )
    rounding_results = _by_code(control.evaluate(rounding))
    assert all(result.passed for result in rounding_results.values())

    missing_total = _invoice(
        subtotal=25,
        tax_amount=4.5,
        total_amount=0,
        lines=[(2, 10, 18), (1, 5, 18)],
    )
    missing_results = _by_code(control.evaluate(missing_total))
    assert missing_results["FIN-004"].passed is False
    assert missing_results["FIN-005"].passed is False

    print("[SUCCESS] Invoice financial control tests passed.")
    return 0


def _invoice(
    *,
    subtotal: float,
    tax_amount: float,
    total_amount: float,
    lines: list[tuple[float, float, float]],
) -> Invoice:
    invoice = Invoice(
        source="TEST",
        original_filename="financial-test.json",
        file_path=None,
        vendor_name="Financial Test Vendor",
        vendor_number="FIN_TEST",
        invoice_number="FIN-TEST-001",
        invoice_date=date(2026, 6, 24),
        po_number="PO-FIN-001",
        currency="INR",
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount,
        payment_terms="NET30",
        status="VALIDATION_IN_PROGRESS",
        extraction_confidence=1,
        extraction_raw={},
    )
    for index, (quantity, unit_price, tax_rate) in enumerate(lines, start=1):
        invoice.lines.append(
            InvoiceLine(
                line_number=index,
                description=f"Financial line {index}",
                quantity=quantity,
                unit_price=unit_price,
                tax_rate=tax_rate,
                po_item=f"{index:05d}",
            )
        )
    return invoice


def _by_code(results):
    return {result.rule_code: result for result in results}


if __name__ == "__main__":
    raise SystemExit(main())
