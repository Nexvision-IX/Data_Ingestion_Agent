from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from app.models import Invoice
from app.rules.validation import RuleResult


_SEPARATORS = re.compile(r"[^A-Z0-9]+")
_TERM_DAYS = {
    "NET30": 30,
    "NET45": 45,
    "NET60": 60,
    "DUE_ON_RECEIPT": 0,
}


def normalize_payment_terms(value: Any) -> str:
    raw = str(value or "").strip().upper()
    compact = _SEPARATORS.sub("", raw)
    if compact in {"NET30", "N30"}:
        return "NET30"
    if compact in {"NET45", "N45"}:
        return "NET45"
    if compact in {"NET60", "N60"}:
        return "NET60"
    if compact in {"DUEONRECEIPT", "IMMEDIATE"}:
        return "DUE_ON_RECEIPT"
    return "UNKNOWN"


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


class PaymentTermsControl:
    def evaluate(
        self,
        invoice: Invoice,
        context: dict[str, Any],
    ) -> list[RuleResult]:
        invoice_terms = normalize_payment_terms(invoice.payment_terms)
        po = context.get("po") or {}
        vendor = context.get("vendor") or {}
        po_raw_terms = po.get("payment_terms")
        vendor_raw_terms = vendor.get("payment_terms")
        po_terms = normalize_payment_terms(po_raw_terms)
        vendor_terms = normalize_payment_terms(vendor_raw_terms)

        reference_source = None
        reference_raw_terms = None
        reference_terms = "UNKNOWN"
        if po_raw_terms not in (None, ""):
            reference_source = "PO"
            reference_raw_terms = po_raw_terms
            reference_terms = po_terms
        elif vendor_raw_terms not in (None, ""):
            reference_source = "VENDOR"
            reference_raw_terms = vendor_raw_terms
            reference_terms = vendor_terms

        invoice_date = _date(invoice.invoice_date)
        calculated_due_date = (
            invoice_date + timedelta(days=_TERM_DAYS[invoice_terms])
            if invoice_date is not None
            and invoice_terms in _TERM_DAYS
            else None
        )
        actual_due_date_raw = self._actual_due_date(invoice)
        actual_due_date = _date(actual_due_date_raw)

        terms_present = invoice_terms != "UNKNOWN"
        reference_available = reference_source is not None
        terms_match = (
            not reference_available
            or (
                reference_terms != "UNKNOWN"
                and invoice_terms == reference_terms
            )
        )
        due_date_matches = (
            terms_present
            and invoice_date is not None
            and (
                actual_due_date_raw in (None, "")
                or (
                    actual_due_date is not None
                    and actual_due_date == calculated_due_date
                )
            )
        )
        effective_due_date = actual_due_date or calculated_due_date
        due_not_before_invoice = (
            invoice_date is not None
            and effective_due_date is not None
            and effective_due_date >= invoice_date
        )

        invoice_days = _TERM_DAYS.get(invoice_terms)
        reference_days = _TERM_DAYS.get(reference_terms)
        early_payment_risk = (
            invoice_days is not None
            and reference_days is not None
            and invoice_days < reference_days
        )
        pay_005_passed = not early_payment_risk

        return [
            RuleResult(
                rule_code="PAY-001",
                rule_name="Invoice payment terms are present and recognized",
                passed=terms_present,
                severity="ERROR",
                message=(
                    "Invoice payment terms are present and recognized."
                    if terms_present
                    else "Invoice payment terms are missing or unrecognized."
                ),
                details={
                    "raw_invoice_payment_terms": invoice.payment_terms,
                    "normalized_invoice_payment_terms": invoice_terms,
                    "recognized_terms": sorted(_TERM_DAYS),
                },
            ),
            RuleResult(
                rule_code="PAY-002",
                rule_name="Invoice payment terms match approved terms",
                passed=terms_present and terms_match,
                severity="ERROR",
                message=(
                    "Invoice payment terms match the approved reference."
                    if terms_present and terms_match and reference_available
                    else (
                        "PO and vendor payment terms were unavailable; "
                        "comparison was not performed."
                        if not reference_available
                        else (
                            "Invoice payment terms do not match the "
                            "approved reference."
                        )
                    )
                ),
                details={
                    "invoice_terms": invoice_terms,
                    "reference_source": reference_source,
                    "raw_reference_terms": reference_raw_terms,
                    "reference_terms": reference_terms,
                    "warning": (
                        "PO and vendor payment terms were unavailable."
                        if not reference_available
                        else None
                    ),
                    "comparison_performed": reference_available,
                },
            ),
            RuleResult(
                rule_code="PAY-003",
                rule_name="Due date is calculated correctly",
                passed=due_date_matches,
                severity="ERROR",
                message=(
                    "Due date matches the calculated payment-term date."
                    if due_date_matches and actual_due_date is not None
                    else (
                        "No actual due date was supplied; calculated due "
                        "date is provided for downstream use."
                        if due_date_matches
                        else "Due date is missing, invalid, or mismatched."
                    )
                ),
                details={
                    "invoice_date": self._format(invoice_date),
                    "payment_terms": invoice_terms,
                    "term_days": _TERM_DAYS.get(invoice_terms),
                    "actual_due_date": self._format(actual_due_date),
                    "raw_actual_due_date": actual_due_date_raw,
                    "calculated_due_date": self._format(
                        calculated_due_date
                    ),
                    "actual_due_date_available": (
                        actual_due_date_raw not in (None, "")
                    ),
                },
            ),
            RuleResult(
                rule_code="PAY-004",
                rule_name="Due date is not before invoice date",
                passed=due_not_before_invoice,
                severity="ERROR",
                message=(
                    "Effective due date is not before the invoice date."
                    if due_not_before_invoice
                    else "Effective due date is before the invoice date."
                ),
                details={
                    "invoice_date": self._format(invoice_date),
                    "effective_due_date": self._format(
                        effective_due_date
                    ),
                    "due_date_source": (
                        "ACTUAL"
                        if actual_due_date is not None
                        else "CALCULATED"
                    ),
                },
            ),
            RuleResult(
                rule_code="PAY-005",
                rule_name=(
                    "Payment terms do not create unapproved early-payment risk"
                ),
                passed=pay_005_passed,
                severity="ERROR",
                message=(
                    "Invoice terms do not accelerate payment."
                    if pay_005_passed and reference_available
                    else (
                        "PO and vendor payment terms were unavailable; "
                        "early-payment comparison was not performed."
                        if not reference_available
                        else (
                            "Invoice terms accelerate payment relative "
                            "to the approved reference."
                        )
                    )
                ),
                details={
                    "invoice_terms": invoice_terms,
                    "invoice_term_days": invoice_days,
                    "reference_source": reference_source,
                    "reference_terms": reference_terms,
                    "reference_term_days": reference_days,
                    "early_payment_risk": early_payment_risk,
                    "warning": (
                        "PO and vendor payment terms were unavailable."
                        if not reference_available
                        else None
                    ),
                    "comparison_performed": reference_available,
                },
            ),
        ]

    @staticmethod
    def _actual_due_date(invoice: Invoice) -> Any:
        direct = getattr(invoice, "due_date", None)
        if direct not in (None, ""):
            return direct
        raw = invoice.extraction_raw or {}
        if isinstance(raw, dict):
            due_date = raw.get("due_date")
            if due_date not in (None, ""):
                return due_date
            nested = raw.get("raw_json")
            if isinstance(nested, dict):
                return nested.get("due_date")
        return None

    @staticmethod
    def _format(value: date | None) -> str | None:
        return value.isoformat() if value is not None else None
