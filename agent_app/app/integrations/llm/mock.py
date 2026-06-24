from __future__ import annotations

from typing import Any

from app.integrations.llm.base import LLMClient


RULE_TO_CATEGORY = {
    "AP-001": ("PO_MISSING", "HIGH", "PROCUREMENT"),
    "AP-002": ("VENDOR_NOT_FOUND", "HIGH", "VENDOR_MASTER"),
    "AP-003": ("BLOCKED_VENDOR", "CRITICAL", "VENDOR_MASTER"),
    "AP-004": ("VENDOR_MISMATCH", "HIGH", "PROCUREMENT"),
    "AP-005": ("CURRENCY_MISMATCH", "HIGH", "AP"),
    "AP-006": ("GRN_MISSING", "HIGH", "REQUESTER"),
    "AP-007": ("QUANTITY_MISMATCH", "HIGH", "REQUESTER"),
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
    "AP-010": ("PAYMENT_TERMS_MISMATCH", "MEDIUM", "AP"),
}


class MockLLMClient(LLMClient):
    """Deterministic LLM substitute so the complete project runs without a key."""

    def generate_json(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
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
            category = exception["category"]
            recipient_role = {
                "GRN_MISSING": "Requester / Goods Receiver",
                "PRICE_MISMATCH": "Procurement",
                "BLOCKED_VENDOR": "Vendor Master Team",
                "DUPLICATE_INVOICE": "AP Reviewer",
            }.get(category, exception.get("owner_team", "AP"))
            return {
                "recipient_role": recipient_role,
                "subject": (
                    f"Action required: {category} for invoice "
                    f"{invoice['invoice_number']}"
                ),
                "body": (
                    "Hello,\n\n"
                    f"Invoice {invoice['invoice_number']} from "
                    f"{invoice['vendor_name']} cannot proceed because of "
                    f"{category.replace('_', ' ').lower()}.\n\n"
                    "Required action: "
                    f"{exception.get('resolution_strategy', 'Please review the issue.')}.\n\n"
                    f"PO: {invoice.get('po_number') or 'Not provided'}\n"
                    f"Invoice amount: {invoice['currency']} "
                    f"{invoice['total_amount']:.2f}\n\n"
                    "Please reply after the required update is completed.\n\n"
                    "Regards,\nAccounts Payable Agent"
                ),
                "requested_action": exception.get(
                    "resolution_strategy",
                    "Review and correct the exception.",
                ),
            }

        if task == "recheck":
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
