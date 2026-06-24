from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from app.config import settings
from app.models import Invoice
from app.rules.validation import RuleResult


CENT = Decimal("0.01")
RATE_TOLERANCE = Decimal("0.0001")
MAX_TAX_RATE = Decimal("100")
_VENDOR_TAX_FIELDS = ("tax_id", "tax_number", "gstin", "vat_number")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _format(value: Decimal | None) -> str | None:
    if value is None or not value.is_finite():
        return None
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


class TaxValidationControl:
    """Deterministic tax-rate, tax-amount, and tax-context controls."""

    def __init__(self, tolerance: Decimal | float | str | None = None):
        configured = (
            settings.tax_tolerance_amount
            if tolerance is None
            else tolerance
        )
        parsed = _decimal(configured)
        if parsed is None or parsed < 0:
            raise ValueError(
                "Tax validation tolerance must be a non-negative number."
            )
        self.tolerance = parsed

    def evaluate(
        self,
        invoice: Invoice,
        context: dict[str, Any],
    ) -> list[RuleResult]:
        header_tax = _decimal(invoice.tax_amount)
        line_details = []
        invalid_rates = []
        calculated_tax = Decimal("0")
        valid_rates = []

        for line in invoice.lines:
            quantity = _decimal(line.quantity)
            unit_price = _decimal(line.unit_price)
            tax_rate = _decimal(line.tax_rate)
            failure = None

            if tax_rate is None:
                failure = "Tax rate is missing or non-numeric."
            elif tax_rate < 0:
                failure = "Tax rate cannot be negative."
            elif tax_rate > MAX_TAX_RATE:
                failure = "Tax rate exceeds the supported maximum of 100%."

            line_amount = (
                quantity * unit_price
                if quantity is not None and unit_price is not None
                else None
            )
            line_tax = (
                line_amount * tax_rate / Decimal("100")
                if line_amount is not None
                and tax_rate is not None
                and failure is None
                else None
            )
            detail = {
                "line_number": line.line_number,
                "tax_rate": (
                    str(tax_rate) if tax_rate is not None else None
                ),
                "line_amount": _format(line_amount),
                "calculated_line_tax": _format(line_tax),
            }
            if failure:
                detail["failure"] = failure
                invalid_rates.append(detail)
            else:
                valid_rates.append(tax_rate)
                if line_tax is not None:
                    calculated_tax += line_tax
            line_details.append(detail)

        if not invoice.lines:
            invalid_rates.append(
                {
                    "line_number": None,
                    "failure": "Invoice has no lines with tax rates.",
                }
            )

        tax_difference = (
            abs(header_tax - calculated_tax)
            if header_tax is not None
            else Decimal("Infinity")
        )
        po = context.get("po") or {}
        po_vat_rate = _decimal(po.get("vat_percent"))
        rate_mismatches = []
        if po_vat_rate is not None:
            for index, tax_rate in enumerate(valid_rates, start=1):
                if abs(tax_rate - po_vat_rate) > RATE_TOLERANCE:
                    rate_mismatches.append(
                        {
                            "line_position": index,
                            "invoice_tax_rate": str(tax_rate),
                            "po_vat_percent": str(po_vat_rate),
                        }
                    )

        vendor = context.get("vendor") or {}
        present_vendor_tax_fields = [
            field
            for field in _VENDOR_TAX_FIELDS
            if vendor.get(field)
        ]
        missing_vendor_tax = not present_vendor_tax_fields

        tax_001_passed = header_tax is not None and header_tax >= 0
        tax_002_passed = not invalid_rates
        tax_003_passed = (
            tax_001_passed
            and tax_002_passed
            and tax_difference <= self.tolerance
        )
        tax_004_passed = (
            po_vat_rate is None
            or (tax_002_passed and not rate_mismatches)
        )

        return [
            RuleResult(
                rule_code="TAX-001",
                rule_name="Invoice tax amount is present and non-negative",
                passed=tax_001_passed,
                severity="ERROR",
                message=(
                    "Invoice tax amount is present and non-negative."
                    if tax_001_passed
                    else "Invoice tax amount is missing or negative."
                ),
                details={
                    "invoice_tax_amount": _format(header_tax),
                },
            ),
            RuleResult(
                rule_code="TAX-002",
                rule_name="Invoice tax rate is present and valid",
                passed=tax_002_passed,
                severity="ERROR",
                message=(
                    "All invoice line tax rates are valid."
                    if tax_002_passed
                    else "One or more invoice line tax rates are invalid."
                ),
                details={
                    "supported_rate_range": {
                        "minimum": "0",
                        "maximum": str(MAX_TAX_RATE),
                    },
                    "lines": line_details,
                    "invalid_rates": invalid_rates,
                },
            ),
            RuleResult(
                rule_code="TAX-003",
                rule_name=(
                    "Header tax amount equals calculated line-level tax"
                ),
                passed=tax_003_passed,
                severity="ERROR",
                message=(
                    "Header tax matches calculated line-level tax."
                    if tax_003_passed
                    else (
                        "Header tax does not match calculated "
                        "line-level tax."
                    )
                ),
                details={
                    "header_tax_amount": _format(header_tax),
                    "calculated_line_tax": _format(calculated_tax),
                    "difference": _format(tax_difference),
                    "tolerance": str(self.tolerance),
                    "calculation": (
                        "sum(quantity * unit_price * tax_rate / 100)"
                    ),
                },
            ),
            RuleResult(
                rule_code="TAX-004",
                rule_name="Invoice tax rate matches PO or master tax rate",
                passed=tax_004_passed,
                severity="ERROR",
                message=(
                    "Invoice tax rates match the available PO tax rate."
                    if tax_004_passed and po_vat_rate is not None
                    else (
                        "PO or master tax rate was unavailable; "
                        "rate comparison was not performed."
                        if po_vat_rate is None
                        else "Invoice tax rate does not match the PO tax rate."
                    )
                ),
                details={
                    "po_vat_percent": (
                        str(po_vat_rate)
                        if po_vat_rate is not None
                        else None
                    ),
                    "invoice_tax_rates": [
                        str(rate) for rate in valid_rates
                    ],
                    "mismatches": rate_mismatches,
                    "warning": (
                        "PO or master VAT percentage was not available."
                        if po_vat_rate is None
                        else None
                    ),
                    "comparison_performed": po_vat_rate is not None,
                },
            ),
            RuleResult(
                rule_code="TAX-005",
                rule_name="Vendor tax registration details are present",
                passed=True,
                severity="WARNING",
                message=(
                    "Vendor tax registration details are present."
                    if not missing_vendor_tax
                    else (
                        "Vendor tax registration details are missing "
                        "from the current context."
                    )
                ),
                details={
                    "checked_fields": list(_VENDOR_TAX_FIELDS),
                    "present_fields": present_vendor_tax_fields,
                    "missing_tax_registration": missing_vendor_tax,
                    "vendor_source": vendor.get("source"),
                },
            ),
        ]
