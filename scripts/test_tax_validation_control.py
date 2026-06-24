"""Focused deterministic tests for tax validation controls."""

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
from app.services.tax_validation_control import TaxValidationControl


def main() -> int:
    control = TaxValidationControl(tolerance="0.01")

    valid = _by_code(
        control.evaluate(
            _invoice(tax_amount=18, tax_rate=18),
            _context(po_vat_percent=18, tax_id="GST-100"),
        )
    )
    assert all(result.passed for result in valid.values())

    missing_tax = _invoice(tax_amount=18, tax_rate=18)
    missing_tax.tax_amount = None
    missing = _by_code(
        control.evaluate(
            missing_tax,
            _context(po_vat_percent=18, tax_id="GST-100"),
        )
    )
    assert missing["TAX-001"].passed is False

    negative = _by_code(
        control.evaluate(
            _invoice(tax_amount=-1, tax_rate=18),
            _context(po_vat_percent=18, tax_id="GST-100"),
        )
    )
    assert negative["TAX-001"].passed is False

    missing_rate_invoice = _invoice(tax_amount=18, tax_rate=18)
    missing_rate_invoice.lines[0].tax_rate = None
    missing_rate = _by_code(
        control.evaluate(
            missing_rate_invoice,
            _context(po_vat_percent=18, tax_id="GST-100"),
        )
    )
    assert missing_rate["TAX-002"].passed is False

    for rate in (-1, 101):
        unrealistic = _by_code(
            control.evaluate(
                _invoice(tax_amount=18, tax_rate=rate),
                _context(po_vat_percent=18, tax_id="GST-100"),
            )
        )
        assert unrealistic["TAX-002"].passed is False, rate

    mismatch = _by_code(
        control.evaluate(
            _invoice(tax_amount=17, tax_rate=18),
            _context(po_vat_percent=18, tax_id="GST-100"),
        )
    )
    assert mismatch["TAX-003"].passed is False

    po_mismatch = _by_code(
        control.evaluate(
            _invoice(tax_amount=18, tax_rate=18),
            _context(po_vat_percent=5, tax_id="GST-100"),
        )
    )
    assert po_mismatch["TAX-004"].passed is False

    unavailable = _by_code(
        control.evaluate(
            _invoice(tax_amount=18, tax_rate=18),
            _context(po_vat_percent=None, tax_id="GST-100"),
        )
    )
    assert unavailable["TAX-004"].passed is True
    assert unavailable["TAX-004"].details["warning"]
    assert unavailable["TAX-004"].details[
        "comparison_performed"
    ] is False

    vendor_missing = _by_code(
        control.evaluate(
            _invoice(tax_amount=18, tax_rate=18),
            _context(po_vat_percent=18, tax_id=None),
        )
    )
    assert vendor_missing["TAX-005"].passed is True
    assert vendor_missing["TAX-005"].severity == "WARNING"
    assert vendor_missing["TAX-005"].details[
        "missing_tax_registration"
    ] is True

    zero_tax = _by_code(
        control.evaluate(
            _invoice(tax_amount=0, tax_rate=0),
            _context(po_vat_percent=0, tax_id="GST-100"),
        )
    )
    assert all(result.passed for result in zero_tax.values())

    print("[SUCCESS] Tax validation control tests passed.")
    return 0


def _invoice(*, tax_amount, tax_rate) -> Invoice:
    invoice = Invoice(
        source="TEST",
        original_filename="tax-test.json",
        file_path=None,
        vendor_name="Tax Test Vendor",
        vendor_number="TAX_VENDOR",
        invoice_number="TAX-TEST-INVOICE",
        invoice_date=date(2026, 6, 24),
        po_number="PO-TAX-001",
        currency="INR",
        subtotal=100,
        tax_amount=tax_amount,
        total_amount=100 + (tax_amount or 0),
        payment_terms="NET30",
        status="VALIDATION_IN_PROGRESS",
        extraction_confidence=1,
        extraction_raw={},
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            description="Tax test line",
            quantity=1,
            unit_price=100,
            tax_rate=tax_rate,
            po_item="00001",
        )
    )
    return invoice


def _context(*, po_vat_percent, tax_id) -> dict:
    return {
        "po": {
            "po_number": "PO-TAX-001",
            "vat_percent": po_vat_percent,
        },
        "vendor": {
            "vendor_number": "TAX_VENDOR",
            "vendor_name": "Tax Test Vendor",
            "status": "ACTIVE",
            "tax_id": tax_id,
            "source": "TEST_VENDOR_CONTEXT",
        },
    }


def _by_code(results):
    return {result.rule_code: result for result in results}


if __name__ == "__main__":
    raise SystemExit(main())
