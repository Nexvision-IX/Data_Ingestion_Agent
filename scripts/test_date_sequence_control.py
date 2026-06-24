"""Focused deterministic tests for invoice date sequence controls."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = PROJECT_ROOT / "agent_app"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(AGENT_ROOT))
os.environ.setdefault("APP_ENV", "test")

from app.models import Invoice, InvoiceLine
from app.services.date_sequence_control import DateSequenceControl


TODAY = date(2026, 6, 24)


def main() -> int:
    control = DateSequenceControl(
        max_invoice_age_days=365,
        today=TODAY,
    )

    valid = _by_code(
        control.evaluate(
            _invoice(TODAY - timedelta(days=10)),
            _context(
                po_date=TODAY - timedelta(days=30),
                gr_date=TODAY - timedelta(days=20),
            ),
        )
    )
    assert all(result.passed for result in valid.values())

    missing_invoice = _invoice(TODAY)
    missing_invoice.invoice_date = None
    missing = _by_code(
        control.evaluate(missing_invoice, _context(TODAY, TODAY))
    )
    assert missing["DATE-001"].passed is False

    before_po = _by_code(
        control.evaluate(
            _invoice(TODAY - timedelta(days=20)),
            _context(
                po_date=TODAY - timedelta(days=10),
                gr_date=TODAY - timedelta(days=25),
            ),
        )
    )
    assert before_po["DATE-002"].passed is False

    before_grn = _by_code(
        control.evaluate(
            _invoice(TODAY - timedelta(days=20)),
            _context(
                po_date=TODAY - timedelta(days=30),
                gr_date=TODAY - timedelta(days=10),
            ),
        )
    )
    assert before_grn["DATE-003"].passed is False

    future = _by_code(
        control.evaluate(
            _invoice(TODAY + timedelta(days=1)),
            _context(
                po_date=TODAY - timedelta(days=30),
                gr_date=TODAY - timedelta(days=20),
            ),
        )
    )
    assert future["DATE-004"].passed is False

    old = _by_code(
        control.evaluate(
            _invoice(TODAY - timedelta(days=366)),
            _context(
                po_date=TODAY - timedelta(days=400),
                gr_date=TODAY - timedelta(days=390),
            ),
        )
    )
    assert old["DATE-005"].passed is False

    unavailable = _by_code(
        control.evaluate(
            _invoice(TODAY - timedelta(days=10)),
            _context(po_date=None, gr_date=None),
        )
    )
    assert unavailable["DATE-002"].passed is True
    assert unavailable["DATE-002"].details["warning"]
    assert unavailable["DATE-002"].details["sequence_checked"] is False
    assert unavailable["DATE-003"].passed is True
    assert unavailable["DATE-003"].details["warning"]
    assert unavailable["DATE-003"].details["sequence_checked"] is False

    invalid_grn_ignored = _by_code(
        control.evaluate(
            _invoice(TODAY - timedelta(days=20)),
            _context(
                po_date=TODAY - timedelta(days=30),
                gr_date=TODAY - timedelta(days=10),
                grn_status="PENDING",
            ),
        )
    )
    assert invalid_grn_ignored["DATE-003"].passed is True
    assert invalid_grn_ignored["DATE-003"].details[
        "valid_grn_count"
    ] == 0

    print("[SUCCESS] Date sequence control tests passed.")
    return 0


def _context(
    po_date,
    gr_date,
    grn_status: str = "POSTED",
) -> dict:
    return {
        "po": {
            "po_number": "PO-DATE-001",
            "po_date": po_date,
            "status": "OPEN",
        },
        "grns": [
            {
                "grn_number": "GRN-DATE-001",
                "po_number": "PO-DATE-001",
                "po_item": "00001",
                "gr_date": gr_date,
                "received_quantity": 1,
                "status": grn_status,
            }
        ],
    }


def _invoice(invoice_date) -> Invoice:
    invoice = Invoice(
        source="TEST",
        original_filename="date-test.json",
        file_path=None,
        vendor_name="Date Test Vendor",
        vendor_number="DATE_VENDOR",
        invoice_number="DATE-TEST-INVOICE",
        invoice_date=invoice_date,
        po_number="PO-DATE-001",
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
            description="Date test line",
            quantity=1,
            unit_price=100,
            tax_rate=18,
            po_item="00001",
        )
    )
    return invoice


def _by_code(results):
    return {result.rule_code: result for result in results}


if __name__ == "__main__":
    raise SystemExit(main())
