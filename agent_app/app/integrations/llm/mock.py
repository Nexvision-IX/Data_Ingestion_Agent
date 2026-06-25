from __future__ import annotations

import copy
from typing import Any

from app.integrations.llm.base import LLMClient


RULE_TO_CATEGORY = {
    **{
        f"OCR-{index:03d}": (
            "EXTRACTION_QUALITY_ISSUE",
            "HIGH",
            "AP_OCR_REVIEW",
        )
        for index in range(1, 11)
    },
    "AP-001": ("PO_MISSING", "HIGH", "PROCUREMENT"),
    "PO-001": ("PO_STATUS_INVALID", "HIGH", "PROCUREMENT"),
    "AP-002": ("VENDOR_NOT_FOUND", "HIGH", "VENDOR_MASTER"),
    "AP-003": ("BLOCKED_VENDOR", "CRITICAL", "VENDOR_MASTER"),
    "AP-004": ("VENDOR_MISMATCH", "HIGH", "PROCUREMENT"),
    "VND-001": ("VENDOR_NOT_FOUND", "HIGH", "VENDOR_MASTER"),
    "VND-002": ("BLOCKED_VENDOR", "CRITICAL", "VENDOR_MASTER"),
    "VND-003": ("VENDOR_MISMATCH", "HIGH", "PROCUREMENT"),
    "VND-004": (
        "VENDOR_MASTER_DATA_INCOMPLETE",
        "MEDIUM",
        "VENDOR_MASTER",
    ),
    "AP-005": ("CURRENCY_MISMATCH", "HIGH", "AP"),
    "AP-006": ("GRN_MISSING", "HIGH", "REQUESTER"),
    "AP-007": ("QUANTITY_MISMATCH", "HIGH", "REQUESTER"),
    "GRN-001": ("GRN_STATUS_INVALID", "HIGH", "REQUESTER"),
    "AP-008": ("PRICE_MISMATCH", "HIGH", "PROCUREMENT"),
    "AP-009": ("DUPLICATE_INVOICE", "CRITICAL", "AP"),
    "DUP-001": ("DUPLICATE_INVOICE", "CRITICAL", "AP"),
    "DUP-002": ("DUPLICATE_INVOICE", "CRITICAL", "AP"),
    "DUP-003": ("DUPLICATE_INVOICE", "HIGH", "AP"),
    "DUP-004": ("DUPLICATE_INVOICE", "CRITICAL", "AP"),
    "FIN-001": ("FINANCIAL_MISMATCH", "HIGH", "AP"),
    "FIN-002": ("FINANCIAL_MISMATCH", "HIGH", "AP"),
    "FIN-003": ("FINANCIAL_MISMATCH", "HIGH", "AP"),
    "FIN-004": ("FINANCIAL_MISMATCH", "HIGH", "AP"),
    "FIN-005": ("FINANCIAL_MISMATCH", "HIGH", "AP"),
    "CONS-001": ("PO_GRN_CONSUMPTION_EXCEEDED", "HIGH", "AP"),
    "CONS-002": ("PO_GRN_CONSUMPTION_EXCEEDED", "HIGH", "AP"),
    "CONS-003": ("PO_GRN_CONSUMPTION_EXCEEDED", "HIGH", "AP"),
    "CONS-004": ("PO_GRN_CONSUMPTION_EXCEEDED", "HIGH", "AP"),
    "DATE-001": ("DATE_POLICY_EXCEPTION", "HIGH", "AP"),
    "DATE-002": ("DATE_POLICY_EXCEPTION", "HIGH", "AP"),
    "DATE-003": ("DATE_POLICY_EXCEPTION", "HIGH", "AP"),
    "DATE-004": ("DATE_POLICY_EXCEPTION", "HIGH", "AP"),
    "DATE-005": ("DATE_POLICY_EXCEPTION", "HIGH", "AP"),
    "TAX-001": ("TAX_MISMATCH", "HIGH", "AP"),
    "TAX-002": ("TAX_MISMATCH", "HIGH", "AP"),
    "TAX-003": ("TAX_MISMATCH", "HIGH", "AP"),
    "TAX-004": ("TAX_MISMATCH", "HIGH", "AP"),
    "TAX-005": (
        "VENDOR_TAX_DATA_INCOMPLETE",
        "MEDIUM",
        "VENDOR_MASTER",
    ),
    "PAY-001": ("PAYMENT_TERMS_MISMATCH", "HIGH", "AP"),
    "PAY-002": ("PAYMENT_TERMS_MISMATCH", "HIGH", "AP"),
    "PAY-003": ("PAYMENT_TERMS_MISMATCH", "HIGH", "AP"),
    "PAY-004": ("PAYMENT_TERMS_MISMATCH", "HIGH", "AP"),
    "PAY-005": ("PAYMENT_TERMS_MISMATCH", "HIGH", "AP"),
    "AP-010": ("PAYMENT_TERMS_MISMATCH", "MEDIUM", "AP"),
}


class MockLLMClient(LLMClient):
    """Deterministic LLM substitute so the complete project runs without a key."""

    provider_name = "mock"

    def __init__(self, model: str = "mock-model"):
        self.model_name = model or "mock-model"

    def generate_json(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        if task == "extraction_repair":
            original = copy.deepcopy(
                payload.get("original_extracted_json") or {}
            )
            evidence = payload.get("raw_evidence") or {}
            corrections = (
                evidence.get("mock_corrections", {})
                if isinstance(evidence, dict)
                else {}
            )
            if isinstance(corrections, dict):
                original.update(copy.deepcopy(corrections))
            return original

        if task == "classification":
            failures = payload.get("failed_validations", [])
            first = failures[0] if failures else {
                "rule_code": "UNKNOWN",
                "message": "Unknown issue",
            }
            category, priority, owner = RULE_TO_CATEGORY.get(
                first.get("rule_code"),
                ("OTHER_EXCEPTION", "MEDIUM", "AP"),
            )
            return {
                "category": category,
                "confidence": 0.93,
                "rationale": (
                    f"Primary failed rule is {first.get('rule_code')}: "
                    f"{first.get('message')}"
                ),
                "priority": priority,
                "owner_team": owner,
            }

        if task == "communication":
            invoice = payload["invoice"]
            exception = payload["exception"]
            summary = payload.get("exception_summary") or {}
            category = exception["category"]
            recipient_role = summary.get(
                "owner_team",
                exception.get("owner_team", "AP"),
            )
            failed_messages = summary.get("failed_rule_messages", [])
            failed_controls = "\n".join(
                (
                    f"- {item.get('rule_code')}: "
                    f"{item.get('message')}"
                )
                for item in failed_messages
            ) or "- See the AP exception record for failed controls."
            recheck_note = (
                "This exception is eligible for recheck after the requested "
                "external data update is confirmed."
                if summary.get("recheck_eligible")
                else (
                    "This exception is not eligible for automatic recheck; "
                    "corrected invoice data or manual review is required."
                )
            )
            return {
                "recipient_role": recipient_role,
                "subject": (
                    f"AP exception action required: invoice "
                    f"{invoice['invoice_number']}"
                ),
                "body": (
                    f"Hello {recipient_role},\n\n"
                    "Reason for exception\n"
                    f"{category.replace('_', ' ').title()} prevents the "
                    "invoice from proceeding.\n\n"
                    "Invoice details\n"
                    f"Invoice: {invoice['invoice_number']}\n"
                    f"Vendor: {invoice['vendor_name']}\n"
                    f"PO: {invoice.get('po_number') or 'Not provided'}\n"
                    f"Invoice amount: {invoice['currency']} "
                    f"{invoice['total_amount']:.2f}\n\n"
                    "Failed controls\n"
                    f"{failed_controls}\n\n"
                    "Action needed\n"
                    f"{summary.get('recommended_resolution') or exception.get('resolution_strategy', 'Please review and correct the issue.')}\n\n"
                    "Recheck note\n"
                    f"{recheck_note}\n\n"
                    "Please confirm when the requested action is complete.\n\n"
                    "Regards,\nAccounts Payable Agent"
                ),
                "requested_action": summary.get(
                    "next_action",
                    exception.get(
                        "resolution_strategy",
                        "Review and correct the exception.",
                    ),
                ),
            }

        if task == "recheck":
            eligibility = payload.get("recheck_eligibility") or {}
            if not eligibility.get("eligible", False):
                return {
                    "decision": "ESCALATE",
                    "confidence": 1.0,
                    "rationale": eligibility.get(
                        "reason",
                        "Automatic recheck is not allowed for this category.",
                    ),
                    "next_action": eligibility.get(
                        "next_action",
                        "Use manual review or controlled reprocessing.",
                    ),
                }
            message = (payload.get("latest_message") or "").lower()
            count = int(payload.get("recheck_count", 0))
            positive_terms = [
                "resolved",
                "posted",
                "updated",
                "corrected",
                "completed",
                "done",
                "available",
                "unblocked",
                "approved",
            ]
            if any(term in message for term in positive_terms):
                decision = "REVALIDATE"
                rationale = (
                    "The latest response indicates that the underlying issue "
                    "may be resolved."
                )
                next_action = (
                    "Fetch fresh source data and rerun deterministic validation."
                )
            elif count >= int(payload.get("max_attempts", 3)):
                decision = "ESCALATE"
                rationale = (
                    "The retry limit has been reached without confirmation "
                    "of resolution."
                )
                next_action = (
                    "Escalate to the configured owner and AP team lead."
                )
            else:
                decision = "WAIT"
                rationale = (
                    "There is not enough evidence that the underlying "
                    "master or transaction data changed."
                )
                next_action = (
                    "Keep the case open and schedule another follow-up."
                )
            return {
                "decision": decision,
                "confidence": 0.90,
                "rationale": rationale,
                "next_action": next_action,
            }

        raise ValueError(f"Unsupported mock LLM task: {task}")
