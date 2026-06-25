from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import ExceptionCase, Invoice, WorkflowEvent


OWNER_BY_CATEGORY = {
    "VENDOR_NOT_FOUND": "Vendor Master / AP",
    "BLOCKED_VENDOR": "Vendor Master / AP",
    "VENDOR_MISMATCH": "Vendor Master / AP",
    "VENDOR_MASTER_DATA_INCOMPLETE": "Vendor Master / AP",
    "VENDOR_TAX_DATA_INCOMPLETE": "Vendor Master / AP Tax",
    "GRN_MISSING": "Receiving / Requester",
    "GRN_STATUS_INVALID": "Receiving / Requester",
    "QUANTITY_MISMATCH": "Receiving / Requester",
    "PO_GRN_CONSUMPTION_EXCEEDED": (
        "Receiving / Requester / Procurement"
    ),
    "PO_MISSING": "Procurement / AP",
    "PO_STATUS_INVALID": "Procurement / AP",
    "PAYMENT_TERMS_MISMATCH": "Procurement / AP",
    "FINANCIAL_MISMATCH": "AP / Finance",
    "TAX_MISMATCH": "AP / Tax",
    "DATE_POLICY_EXCEPTION": "AP / Finance",
    "DUPLICATE_INVOICE": "AP",
    "OTHER_EXCEPTION": "AP Reviewer",
}

RECHECK_ELIGIBLE_CATEGORIES = frozenset(
    {
        "VENDOR_NOT_FOUND",
        "BLOCKED_VENDOR",
        "VENDOR_MISMATCH",
        "VENDOR_MASTER_DATA_INCOMPLETE",
        "VENDOR_TAX_DATA_INCOMPLETE",
        "GRN_MISSING",
        "GRN_STATUS_INVALID",
        "QUANTITY_MISMATCH",
        "PO_GRN_CONSUMPTION_EXCEEDED",
        "PO_MISSING",
        "PO_STATUS_INVALID",
        "PAYMENT_TERMS_MISMATCH",
        "PRICE_MISMATCH",
        "CURRENCY_MISMATCH",
    }
)

NON_RECHECK_REASONS = {
    "FINANCIAL_MISMATCH": (
        "Invoice financial data must be corrected and reprocessed before "
        "deterministic validation can pass."
    ),
    "TAX_MISMATCH": (
        "Invoice tax data must be corrected and reprocessed before "
        "deterministic validation can pass."
    ),
    "DUPLICATE_INVOICE": (
        "A possible duplicate requires AP review or corrected invoice data; "
        "automatic source-data recheck is not appropriate."
    ),
    "DATE_POLICY_EXCEPTION": (
        "Date-policy failures require corrected invoice data or accounting-"
        "period review before reprocessing."
    ),
}


def owner_for_category(category: str) -> str:
    return OWNER_BY_CATEGORY.get(category, "AP Reviewer")


def recheck_eligibility(category: str) -> dict[str, Any]:
    eligible = category in RECHECK_ELIGIBLE_CATEGORIES
    if eligible:
        reason = (
            "The exception depends on PO, GRN, vendor, payment-term, or "
            "other external master/transaction data that may change."
        )
        next_action = (
            "Confirm the external update, fetch fresh source data, and rerun "
            "all deterministic validation controls."
        )
    else:
        reason = NON_RECHECK_REASONS.get(
            category,
            (
                "This category is not configured for automatic recheck; "
                "manual AP review or controlled reprocessing is required."
            ),
        )
        next_action = (
            "Correct the invoice or complete manual review, then use the "
            "controlled reprocess workflow if appropriate."
        )
    return {
        "eligible": eligible,
        "reason": reason,
        "next_action": next_action,
    }


def non_eligible_recheck_response(
    category: str,
) -> dict[str, Any]:
    eligibility = recheck_eligibility(category)
    return {
        "decision": "NOT_ELIGIBLE",
        "confidence": 1.0,
        "rationale": eligibility["reason"],
        "next_action": eligibility["next_action"],
        "recheck_eligible": False,
        "category": category,
    }


class ExceptionSummaryService:
    def __init__(self, db: Session | None = None):
        self.db = db

    def build(
        self,
        *,
        invoice: Invoice,
        category: str,
        severity: str,
        validation_results: Iterable[Any],
        recommended_resolution: str,
    ) -> dict[str, Any]:
        results = [self._result_dict(item) for item in validation_results]
        failed_blocking = [
            item
            for item in results
            if not item["passed"] and item["severity"] == "ERROR"
        ]
        warnings = [
            item for item in results if item["severity"] == "WARNING"
        ]
        eligibility = recheck_eligibility(category)
        owner = owner_for_category(category)
        return {
            "invoice_number": invoice.invoice_number,
            "vendor_name": invoice.vendor_name,
            "po_number": invoice.po_number,
            "exception_category": category,
            "severity": severity,
            "owner_team": owner,
            "failed_blocking_rules": [
                item["rule_code"] for item in failed_blocking
            ],
            "warning_rules": [item["rule_code"] for item in warnings],
            "failed_rule_messages": [
                {
                    "rule_code": item["rule_code"],
                    "rule_name": item["rule_name"],
                    "message": item["message"],
                }
                for item in failed_blocking
            ],
            "key_rule_details": {
                item["rule_code"]: item["details"]
                for item in failed_blocking
            },
            "recommended_resolution": recommended_resolution,
            "recheck_eligible": eligibility["eligible"],
            "recheck_eligibility_reason": eligibility["reason"],
            "next_action": eligibility["next_action"],
        }

    def record_events(
        self,
        invoice: Invoice,
        summary: dict[str, Any],
    ) -> None:
        if self.db is None:
            return
        self._event(
            invoice,
            "EXCEPTION_SUMMARY_CREATED",
            "Structured exception summary created.",
            summary,
        )
        self._event(
            invoice,
            "EXCEPTION_OWNER_ASSIGNED",
            f"Exception assigned to {summary['owner_team']}.",
            {
                "category": summary["exception_category"],
                "owner_team": summary["owner_team"],
            },
        )
        self._event(
            invoice,
            "RECHECK_ELIGIBILITY_EVALUATED",
            summary["recheck_eligibility_reason"],
            {
                "category": summary["exception_category"],
                "eligible": summary["recheck_eligible"],
                "reason": summary["recheck_eligibility_reason"],
                "next_action": summary["next_action"],
            },
        )

    def record_recheck_evaluation(
        self,
        invoice: Invoice,
        category: str,
    ) -> dict[str, Any]:
        eligibility = recheck_eligibility(category)
        if self.db is not None:
            self._event(
                invoice,
                "RECHECK_ELIGIBILITY_EVALUATED",
                eligibility["reason"],
                {
                    "category": category,
                    **eligibility,
                },
            )
        return eligibility

    def evaluate_recheck_request(
        self,
        invoice: Invoice,
        exception: ExceptionCase,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        eligibility = self.record_recheck_evaluation(
            invoice,
            exception.category,
        )
        if eligibility["eligible"]:
            return eligibility, None

        exception.last_recheck_decision = "NOT_ELIGIBLE"
        return (
            eligibility,
            non_eligible_recheck_response(exception.category),
        )

    def record_communication_drafted(
        self,
        invoice: Invoice,
        *,
        exception_id: str,
        recipient_role: str,
        subject: str,
        recheck_eligible: bool,
    ) -> None:
        if self.db is None:
            return
        self._event(
            invoice,
            "EXCEPTION_COMMUNICATION_DRAFTED",
            "Business-readable exception communication was drafted.",
            {
                "exception_id": exception_id,
                "recipient_role": recipient_role,
                "subject": subject,
                "recheck_eligible": recheck_eligible,
            },
        )

    def _event(
        self,
        invoice: Invoice,
        event_type: str,
        message: str,
        metadata: dict[str, Any],
    ) -> None:
        self.db.add(
            WorkflowEvent(
                invoice_id=invoice.id,
                event_type=event_type,
                agent_name="ExceptionSummaryService",
                message=message,
                metadata_json=metadata,
            )
        )

    @staticmethod
    def _result_dict(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return {
                "rule_code": item.get("rule_code"),
                "rule_name": item.get("rule_name"),
                "passed": bool(item.get("passed")),
                "severity": item.get("severity", "ERROR"),
                "message": item.get("message", ""),
                "details": item.get("details", {}),
            }
        return {
            "rule_code": item.rule_code,
            "rule_name": item.rule_name,
            "passed": item.passed,
            "severity": item.severity,
            "message": item.message,
            "details": item.details,
        }
