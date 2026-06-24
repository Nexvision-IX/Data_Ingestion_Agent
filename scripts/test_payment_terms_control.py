"""Focused deterministic tests for payment terms and due dates."""

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
from app.services.payment_terms_control import (
    PaymentTermsControl,
    normalize_payment_terms,
)


INVOICE_DATE = date(2026, 6, 1)


def main() -> int:
    control = PaymentTermsControl()

    assert normalize_payment_terms("NET 30") == "NET30"
    assert normalize_payment_terms("N30") == "NET30"

    normalized = _by_code(
        control.evaluate(
            _invoice("NET 30"),
            _context(po_terms="N30"),
        )
    )
    assert normalized["PAY-001"].passed is True
    assert normalized["PAY-002"].passed is True

    missing = _by_code(
        control.evaluate(_invoice(None), _context(po_terms="NET30"))
    )
    assert missing["PAY-001"].passed is False

    unrecognized = _by_code(
        control.evaluate(
            _invoice("SOMEDAY"),
            _context(po_terms="NET30"),
        )
    )
    assert unrecognized["PAY-001"].passed is False

    mismatch = _by_code(
        control.evaluate(
            _invoice("NET30"),
            _context(po_terms="NET45"),
        )
    )
    assert mismatch["PAY-002"].passed is False

    unavailable = _by_code(
        control.evaluate(
            _invoice("NET30"),
            _context(po_terms=None, vendor_terms=None),
        )
    )
    assert unavailable["PAY-002"].passed is True
    assert unavailable["PAY-002"].details["warning"]
    assert unavailable["PAY-002"].details[
        "comparison_performed"
    ] is False

    expected_dates = {
        "NET30": "2026-07-01",
        "NET45": "2026-07-16",
        "NET60": "2026-07-31",
        "DUE ON RECEIPT": "2026-06-01",
        "IMMEDIATE": "2026-06-01",
    }
    for terms, expected in expected_dates.items():
        results = _by_code(
            control.evaluate(
                _invoice(terms),
                _context(po_terms=terms),
            )
        )
        assert results["PAY-003"].passed is True, terms
        assert results["PAY-003"].details[
            "calculated_due_date"
        ] == expected

    bad_due_date = _invoice("NET30", due_date="2026-05-31")
    bad_due = _by_code(
        control.evaluate(
            bad_due_date,
            _context(po_terms="NET30"),
        )
    )
    assert bad_due["PAY-003"].passed is False
    assert bad_due["PAY-004"].passed is False

    early = _by_code(
        control.evaluate(
            _invoice("NET30"),
            _context(po_terms="NET60"),
        )
    )
    assert early["PAY-005"].passed is False

    later = _by_code(
        control.evaluate(
            _invoice("NET60"),
            _context(po_terms="NET30"),
        )
    )
    assert later["PAY-005"].passed is True

    vendor_fallback = _by_code(
        control.evaluate(
            _invoice("NET45"),
            _context(po_terms=None, vendor_terms="NET45"),
        )
    )
    assert vendor_fallback["PAY-002"].passed is True
    assert vendor_fallback["PAY-002"].details[
        "reference_source"
    ] == "VENDOR"

    print("[SUCCESS] Payment terms control tests passed.")
    return 0


def _invoice(
    payment_terms,
    *,
    due_date=None,
) -> Invoice:
    invoice = Invoice(
        source="TEST",
        original_filename="payment-test.json",
        file_path=None,
        vendor_name="Payment Test Vendor",
        vendor_number="PAY_VENDOR",
        invoice_number="PAY-TEST-INVOICE",
        invoice_date=INVOICE_DATE,
        po_number="PO-PAY-001",
        currency="INR",
        subtotal=100,
        tax_amount=0,
        total_amount=100,
        payment_terms=payment_terms,
        status="VALIDATION_IN_PROGRESS",
        extraction_confidence=1,
        extraction_raw=(
            {"due_date": due_date}
            if due_date is not None
            else {}
        ),
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            description="Payment terms test line",
            quantity=1,
            unit_price=100,
            tax_rate=0,
            po_item="00001",
        )
    )
    return invoice


def _context(*, po_terms, vendor_terms="NET30") -> dict:
    return {
        "po": {
            "po_number": "PO-PAY-001",
            "payment_terms": po_terms,
        },
        "vendor": {
            "vendor_number": "PAY_VENDOR",
            "payment_terms": vendor_terms,
        },
    }


def _by_code(results):
    return {result.rule_code: result for result in results}


if __name__ == "__main__":
    raise SystemExit(main())
