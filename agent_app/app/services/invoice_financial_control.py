from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from app.config import settings
from app.models import Invoice
from app.rules.validation import RuleResult


CENT = Decimal("0.01")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


class InvoiceFinancialControl:
    """Deterministic arithmetic controls for invoice financial integrity."""

    def __init__(self, tolerance: Decimal | float | str | None = None):
        configured = (
            settings.financial_tolerance_amount
            if tolerance is None
            else tolerance
        )
        parsed = _decimal(configured)
        if parsed is None or parsed < 0:
            raise ValueError(
                "Financial validation tolerance must be a non-negative number."
            )
        self.tolerance = parsed

    def evaluate(self, invoice: Invoice) -> list[RuleResult]:
        line_details = []
        line_failures = []
        line_subtotal = Decimal("0")
        expected_tax = Decimal("0")

        for line in invoice.lines:
            quantity = _decimal(line.quantity)
            unit_price = _decimal(line.unit_price)
            tax_rate = _decimal(line.tax_rate)
            failure = None

            if quantity is None or unit_price is None or tax_rate is None:
                failure = "Line contains a missing or non-numeric value."
            elif quantity <= 0:
                failure = "Line quantity must be greater than zero."
            elif unit_price < 0:
                failure = "Line unit price cannot be negative."
            elif tax_rate < 0:
                failure = "Line tax rate cannot be negative."

            line_amount = (
                quantity * unit_price
                if quantity is not None and unit_price is not None
                else None
            )
            if (
                failure is None
                and line_amount is not None
                and line_amount <= 0
            ):
                failure = "Calculated line amount must be greater than zero."

            detail = {
                "line_number": line.line_number,
                "quantity": (
                    str(quantity) if quantity is not None else None
                ),
                "unit_price": (
                    str(unit_price) if unit_price is not None else None
                ),
                "tax_rate": (
                    str(tax_rate) if tax_rate is not None else None
                ),
                "calculated_line_amount": (
                    _money(line_amount)
                    if line_amount is not None
                    else None
                ),
            }
            if failure:
                detail["failure"] = failure
                line_failures.append(detail)
            else:
                line_subtotal += line_amount
                expected_tax += (
                    line_amount * tax_rate / Decimal("100")
                )
            line_details.append(detail)

        if not invoice.lines:
            line_failures.append(
                {
                    "line_number": None,
                    "failure": "Invoice has no lines to validate.",
                }
            )

        subtotal = _decimal(invoice.subtotal)
        tax_amount = _decimal(invoice.tax_amount)
        total_amount = _decimal(invoice.total_amount)

        subtotal_difference = self._difference(subtotal, line_subtotal)
        tax_difference = self._difference(tax_amount, expected_tax)
        declared_total_expected = (
            subtotal + tax_amount
            if subtotal is not None and tax_amount is not None
            else None
        )
        total_difference = self._difference(
            total_amount,
            declared_total_expected,
        )

        rounded_subtotal = line_subtotal.quantize(
            CENT,
            rounding=ROUND_HALF_UP,
        )
        rounded_tax = expected_tax.quantize(
            CENT,
            rounding=ROUND_HALF_UP,
        )
        rounded_total = rounded_subtotal + rounded_tax
        rounding_subtotal_difference = self._difference(
            subtotal,
            rounded_subtotal,
        )
        rounding_total_difference = self._difference(
            total_amount,
            rounded_total,
        )

        fin_001_passed = not line_failures
        fin_002_passed = (
            fin_001_passed
            and subtotal is not None
            and subtotal_difference <= self.tolerance
            and not (line_subtotal > self.tolerance and subtotal <= 0)
        )
        fin_003_passed = (
            fin_001_passed
            and tax_amount is not None
            and tax_difference <= self.tolerance
            and not (expected_tax > self.tolerance and tax_amount <= 0)
        )
        fin_004_passed = (
            subtotal is not None
            and tax_amount is not None
            and total_amount is not None
            and total_difference <= self.tolerance
            and not (
                declared_total_expected is not None
                and declared_total_expected > self.tolerance
                and total_amount <= 0
            )
        )
        fin_005_passed = (
            fin_001_passed
            and subtotal is not None
            and total_amount is not None
            and rounding_subtotal_difference <= self.tolerance
            and rounding_total_difference <= self.tolerance
            and not (
                rounded_total > self.tolerance
                and total_amount <= 0
            )
        )

        return [
            RuleResult(
                rule_code="FIN-001",
                rule_name="Line amount equals quantity × unit price",
                passed=fin_001_passed,
                severity="ERROR",
                message=(
                    "All invoice line amounts were calculated successfully."
                    if fin_001_passed
                    else "One or more invoice lines have invalid arithmetic."
                ),
                details={
                    "calculation": "quantity * unit_price",
                    "lines": line_details,
                    "failures": line_failures,
                },
            ),
            self._amount_result(
                "FIN-002",
                "Invoice subtotal equals sum of invoice line amounts",
                fin_002_passed,
                subtotal,
                line_subtotal,
                subtotal_difference,
                "Invoice subtotal matches the calculated line subtotal.",
                "Invoice subtotal does not match the calculated line subtotal.",
            ),
            self._amount_result(
                "FIN-003",
                "Tax amount equals subtotal × VAT percentage",
                fin_003_passed,
                tax_amount,
                expected_tax,
                tax_difference,
                "Invoice tax matches the tax calculated from line VAT rates.",
                "Invoice tax does not match the calculated VAT amount.",
                extra={
                    "calculation": (
                        "sum(quantity * unit_price * tax_rate / 100)"
                    ),
                },
            ),
            self._amount_result(
                "FIN-004",
                "Document total equals subtotal + tax amount",
                fin_004_passed,
                total_amount,
                declared_total_expected,
                total_difference,
                "Document total matches subtotal plus tax.",
                "Document total does not match subtotal plus tax.",
            ),
            RuleResult(
                rule_code="FIN-005",
                rule_name=(
                    "Invoice subtotal and total are within rounding tolerance"
                ),
                passed=fin_005_passed,
                severity="ERROR",
                message=(
                    "Invoice subtotal and total are within rounding tolerance."
                    if fin_005_passed
                    else (
                        "Invoice subtotal or total exceeds the allowed "
                        "rounding tolerance."
                    )
                ),
                details={
                    "declared_subtotal": self._format(subtotal),
                    "rounded_expected_subtotal": _money(
                        rounded_subtotal
                    ),
                    "subtotal_difference": self._format(
                        rounding_subtotal_difference
                    ),
                    "declared_total": self._format(total_amount),
                    "rounded_expected_total": _money(rounded_total),
                    "total_difference": self._format(
                        rounding_total_difference
                    ),
                    "tolerance": str(self.tolerance),
                },
            ),
        ]

    def _amount_result(
        self,
        rule_code: str,
        rule_name: str,
        passed: bool,
        actual: Decimal | None,
        expected: Decimal | None,
        difference: Decimal,
        pass_message: str,
        fail_message: str,
        extra: dict[str, Any] | None = None,
    ) -> RuleResult:
        details = {
            "actual": self._format(actual),
            "expected": self._format(expected),
            "difference": self._format(difference),
            "tolerance": str(self.tolerance),
        }
        if extra:
            details.update(extra)
        return RuleResult(
            rule_code=rule_code,
            rule_name=rule_name,
            passed=passed,
            severity="ERROR",
            message=pass_message if passed else fail_message,
            details=details,
        )

    @staticmethod
    def _difference(
        actual: Decimal | None,
        expected: Decimal | None,
    ) -> Decimal:
        if actual is None or expected is None:
            return Decimal("Infinity")
        return abs(actual - expected)

    @staticmethod
    def _format(value: Decimal | None) -> str | None:
        if value is None or not value.is_finite():
            return None
        return _money(value)
