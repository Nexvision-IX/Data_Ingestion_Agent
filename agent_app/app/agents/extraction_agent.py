from __future__ import annotations

from datetime import date
from pathlib import Path

from app.schemas import ExtractedInvoice, ExtractedLine


SCENARIOS: dict[str, dict] = {
    "clean": {
        "vendor_name": "Acme Industrial Supplies",
        "vendor_number": "V100",
        "vendor_email": "supplier.acme@example.com",
        "invoice_number": "INV-CLEAN-001",
        "invoice_date": date(2026, 6, 15),
        "po_number": "4500000010",
        "currency": "INR",
        "subtotal": 10000,
        "tax_amount": 1800,
        "total_amount": 11800,
        "payment_terms": "NET30",
        "lines": [
            ExtractedLine(
                line_number=1,
                description="Industrial component",
                quantity=10,
                unit_price=1000,
                tax_rate=18,
                po_item="00010",
            )
        ],
    },
    "missing_grn": {
        "vendor_name": "Acme Industrial Supplies",
        "vendor_number": "V100",
        "vendor_email": "supplier.acme@example.com",
        "invoice_number": "INV-MISSING-GRN-001",
        "invoice_date": date(2026, 6, 15),
        "po_number": "4500000020",
        "currency": "INR",
        "subtotal": 2500,
        "tax_amount": 450,
        "total_amount": 2950,
        "payment_terms": "NET30",
        "lines": [
            ExtractedLine(
                line_number=1,
                description="Maintenance service",
                quantity=5,
                unit_price=500,
                tax_rate=18,
                po_item="00010",
            )
        ],
    },
    "price_mismatch": {
        "vendor_name": "Acme Industrial Supplies",
        "vendor_number": "V100",
        "vendor_email": "supplier.acme@example.com",
        "invoice_number": "INV-PRICE-001",
        "invoice_date": date(2026, 6, 15),
        "po_number": "4500000030",
        "currency": "INR",
        "subtotal": 1600,
        "tax_amount": 288,
        "total_amount": 1888,
        "payment_terms": "NET30",
        "lines": [
            ExtractedLine(
                line_number=1,
                description="Calibration unit",
                quantity=2,
                unit_price=800,
                tax_rate=18,
                po_item="00010",
            )
        ],
    },
    "blocked_vendor": {
        "vendor_name": "Blocked Demo Vendor",
        "vendor_number": "V200",
        "vendor_email": "blocked.vendor@example.com",
        "invoice_number": "INV-BLOCKED-001",
        "invoice_date": date(2026, 6, 15),
        "po_number": "4500000040",
        "currency": "INR",
        "subtotal": 1000,
        "tax_amount": 180,
        "total_amount": 1180,
        "payment_terms": "NET30",
        "lines": [
            ExtractedLine(
                line_number=1,
                description="Demo material",
                quantity=1,
                unit_price=1000,
                tax_rate=18,
                po_item="00010",
            )
        ],
    },
    "duplicate": {
        "vendor_name": "Acme Industrial Supplies",
        "vendor_number": "V100",
        "vendor_email": "supplier.acme@example.com",
        "invoice_number": "INV-DUP-001",
        "invoice_date": date(2026, 6, 15),
        "po_number": "4500000010",
        "currency": "INR",
        "subtotal": 10000,
        "tax_amount": 1800,
        "total_amount": 11800,
        "payment_terms": "NET30",
        "lines": [
            ExtractedLine(
                line_number=1,
                description="Industrial component",
                quantity=10,
                unit_price=1000,
                tax_rate=18,
                po_item="00010",
            )
        ],
    },
}


class MockExtractionAgent:
    """
    Mock invoice extraction.

    Future OCR or document-AI adapters should return the same
    ExtractedInvoice contract.
    """

    def extract(
        self,
        file_path: Path | None = None,
        scenario: str | None = None,
    ) -> ExtractedInvoice:
        selected = scenario
        if not selected and file_path:
            lowered = file_path.name.lower()
            selected = next(
                (
                    name
                    for name in SCENARIOS
                    if name in lowered
                ),
                None,
            )
        selected = selected or "clean"
        if selected not in SCENARIOS:
            raise ValueError(
                f"Unknown scenario: {selected}. "
                f"Options: {sorted(SCENARIOS)}"
            )

        payload = dict(SCENARIOS[selected])
        payload["confidence"] = 0.97
        payload["raw"] = {
            "extractor": "MOCK_EXTRACTION_AGENT",
            "scenario": selected,
            "source_file": (
                str(file_path)
                if file_path
                else None
            ),
        }
        return ExtractedInvoice(**payload)
