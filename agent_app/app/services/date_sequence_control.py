from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.config import settings
from app.models import Invoice
from app.rules.validation import RuleResult
from app.services.grn_status_control import (
    VALID_GRN_STATUSES,
    normalize_grn,
)


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


class DateSequenceControl:
    def __init__(
        self,
        max_invoice_age_days: int | None = None,
        today: date | None = None,
    ):
        self.max_invoice_age_days = (
            settings.max_invoice_age_days
            if max_invoice_age_days is None
            else int(max_invoice_age_days)
        )
        if self.max_invoice_age_days < 0:
            raise ValueError(
                "MAX_INVOICE_AGE_DAYS must be zero or greater."
            )
        self.today = today or date.today()

    def evaluate(
        self,
        invoice: Invoice,
        context: dict[str, Any],
    ) -> list[RuleResult]:
        invoice_date = _date(invoice.invoice_date)
        po = context.get("po") or {}
        po_date = _date(po.get("po_date"))
        valid_grns = [
            normalize_grn(grn)
            for grn in context.get("grns", [])
        ]
        valid_grns = [
            grn
            for grn in valid_grns
            if grn["status"] in VALID_GRN_STATUSES
        ]
        dated_grns = [
            {
                "grn_number": grn.get("grn_number"),
                "gr_date": _date(grn.get("gr_date")),
                "raw_gr_date": grn.get("gr_date"),
                "status": grn.get("status"),
            }
            for grn in valid_grns
        ]
        available_grn_dates = [
            item["gr_date"]
            for item in dated_grns
            if item["gr_date"] is not None
        ]

        invoice_present = invoice_date is not None
        before_po = (
            invoice_date is not None
            and po_date is not None
            and invoice_date < po_date
        )
        later_grns = [
            item
            for item in dated_grns
            if (
                invoice_date is not None
                and item["gr_date"] is not None
                and invoice_date < item["gr_date"]
            )
        ]
        future_dated = (
            invoice_date is not None
            and invoice_date > self.today
        )
        age_days = (
            (self.today - invoice_date).days
            if invoice_date is not None
            else None
        )
        too_old = (
            age_days is not None
            and age_days > self.max_invoice_age_days
        )

        return [
            RuleResult(
                rule_code="DATE-001",
                rule_name="Invoice date is present and valid",
                passed=invoice_present,
                severity="ERROR",
                message=(
                    "Invoice date is present and valid."
                    if invoice_present
                    else "Invoice date is missing or invalid."
                ),
                details={
                    "raw_invoice_date": invoice.invoice_date,
                    "invoice_date": self._format(invoice_date),
                },
            ),
            RuleResult(
                rule_code="DATE-002",
                rule_name="Invoice date is not before PO date",
                passed=invoice_present and not before_po,
                severity="ERROR",
                message=(
                    "Invoice date is not before the PO date."
                    if invoice_present and not before_po
                    else "Invoice date is before the PO date."
                ),
                details={
                    "invoice_date": self._format(invoice_date),
                    "po_date": self._format(po_date),
                    "warning": (
                        "PO date was not available; sequence was not checked."
                        if po_date is None
                        else None
                    ),
                    "sequence_checked": po_date is not None,
                },
            ),
            RuleResult(
                rule_code="DATE-003",
                rule_name="Invoice date is not before valid GRN date",
                passed=invoice_present and not later_grns,
                severity="ERROR",
                message=(
                    "Invoice date is not before any dated valid GRN."
                    if invoice_present and not later_grns
                    else "Invoice date is before a valid GRN date."
                ),
                details={
                    "invoice_date": self._format(invoice_date),
                    "valid_grn_count": len(valid_grns),
                    "dated_valid_grn_count": len(available_grn_dates),
                    "valid_grns": [
                        {
                            **item,
                            "gr_date": self._format(item["gr_date"]),
                        }
                        for item in dated_grns
                    ],
                    "grns_after_invoice": [
                        {
                            **item,
                            "gr_date": self._format(item["gr_date"]),
                        }
                        for item in later_grns
                    ],
                    "warning": (
                        "No valid GRN date was available; sequence was not checked."
                        if not available_grn_dates
                        else None
                    ),
                    "sequence_checked": bool(available_grn_dates),
                },
            ),
            RuleResult(
                rule_code="DATE-004",
                rule_name="Invoice date is not future dated",
                passed=invoice_present and not future_dated,
                severity="ERROR",
                message=(
                    "Invoice date is not future dated."
                    if invoice_present and not future_dated
                    else "Invoice date is in the future."
                ),
                details={
                    "invoice_date": self._format(invoice_date),
                    "current_local_date": self.today.isoformat(),
                },
            ),
            RuleResult(
                rule_code="DATE-005",
                rule_name="Invoice is within the allowed age threshold",
                passed=invoice_present and not too_old,
                severity="ERROR",
                message=(
                    "Invoice is within the allowed age threshold."
                    if invoice_present and not too_old
                    else "Invoice is older than the allowed policy threshold."
                ),
                details={
                    "invoice_date": self._format(invoice_date),
                    "current_local_date": self.today.isoformat(),
                    "invoice_age_days": age_days,
                    "max_invoice_age_days": self.max_invoice_age_days,
                },
            ),
        ]

    @staticmethod
    def _format(value: date | None) -> str | None:
        return value.isoformat() if value is not None else None
