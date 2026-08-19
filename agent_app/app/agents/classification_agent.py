from __future__ import annotations

from app.integrations.llm.base import LLMClient
from app.schemas import ClassificationOutput


SYSTEM_PROMPT = """
You are an Accounts Payable exception classification agent.
Use only the supplied invoice and deterministic validation results.
Choose the most important root-cause category. Do not invent SAP facts.
Return confidence, concise rationale, business priority, and owner team.
Deterministic validation results remain the source of truth for pass/fail.
You classify and explain only; you do not approve the invoice.
The output must follow the supplied JSON schema exactly.
""".strip()


RULE_PRIORITY = {
    "OCR-008": ("OCR_LOW_CONFIDENCE", "HIGH", "AP_OCR_REVIEW"),
    "AP-001": ("PO_NOT_FOUND", "HIGH", "PROCUREMENT"),
    "AP-009": ("DUPLICATE_INVOICE", "CRITICAL", "AP"),
    "DUP-001": ("DUPLICATE_INVOICE", "CRITICAL", "AP"),
    "DUP-002": ("DUPLICATE_INVOICE", "CRITICAL", "AP"),
    "DUP-003": ("DUPLICATE_INVOICE", "HIGH", "AP"),
    "DUP-004": ("DUPLICATE_INVOICE", "CRITICAL", "AP"),
    "PO-001": ("PO_STATUS_INVALID", "HIGH", "PROCUREMENT"),
    "AP-004": ("VENDOR_MISMATCH", "HIGH", "PROCUREMENT"),
    "VND-003": ("VENDOR_MISMATCH", "HIGH", "PROCUREMENT"),
    "AP-006": ("GRN_MISSING", "HIGH", "REQUESTER"),
    "GRN-001": ("GRN_STATUS_INVALID", "HIGH", "REQUESTER"),
    "AP-007": ("QUANTITY_NOT_COVERED_BY_GRN", "HIGH", "REQUESTER"),
    "CONS-001": ("PO_GRN_CONSUMPTION_EXCEEDED", "HIGH", "AP"),
    "CONS-004": ("PO_GRN_CONSUMPTION_EXCEEDED", "HIGH", "AP"),
    "AP-008": ("PRICE_AMOUNT_MISMATCH", "HIGH", "PROCUREMENT"),
    "FIN-001": ("FINANCIAL_MISMATCH", "HIGH", "AP"),
    "FIN-002": ("FINANCIAL_MISMATCH", "HIGH", "AP"),
    "FIN-003": ("FINANCIAL_MISMATCH", "HIGH", "AP"),
    "FIN-004": ("FINANCIAL_MISMATCH", "HIGH", "AP"),
    "FIN-005": ("FINANCIAL_MISMATCH", "HIGH", "AP"),
    "TAX-001": ("TAX_MISMATCH", "HIGH", "AP"),
    "TAX-002": ("TAX_MISMATCH", "HIGH", "AP"),
    "TAX-003": ("TAX_MISMATCH", "HIGH", "AP"),
    "TAX-004": ("TAX_MISMATCH", "HIGH", "AP"),
    "PAY-001": ("PAYMENT_TERMS_MISMATCH", "HIGH", "AP"),
    "PAY-002": ("PAYMENT_TERMS_MISMATCH", "HIGH", "AP"),
    "PAY-003": ("PAYMENT_TERMS_MISMATCH", "HIGH", "AP"),
    "PAY-004": ("PAYMENT_TERMS_MISMATCH", "HIGH", "AP"),
    "PAY-005": ("PAYMENT_TERMS_MISMATCH", "HIGH", "AP"),
    "AP-010": ("PAYMENT_TERMS_MISMATCH", "MEDIUM", "AP"),
    "DATE-001": ("DATE_POLICY_EXCEPTION", "HIGH", "AP"),
    "DATE-002": ("DATE_SEQUENCE_ERROR", "HIGH", "AP"),
    "DATE-003": ("DATE_SEQUENCE_ERROR", "HIGH", "AP"),
    "DATE-004": ("DATE_POLICY_EXCEPTION", "HIGH", "AP"),
    "DATE-005": ("DATE_POLICY_EXCEPTION", "HIGH", "AP"),
}

PRIMARY_RULE_ORDER = [
    "OCR-008",
    "AP-001",
    "AP-009",
    "DUP-001",
    "DUP-002",
    "DUP-003",
    "DUP-004",
    "PO-001",
    "AP-004",
    "VND-003",
    "AP-005",
    "AP-006",
    "GRN-001",
    "AP-007",
    "CONS-001",
    "CONS-004",
    "AP-008",
    "FIN-001",
    "FIN-002",
    "FIN-003",
    "FIN-004",
    "FIN-005",
    "TAX-001",
    "TAX-002",
    "TAX-003",
    "TAX-004",
    "PAY-001",
    "PAY-002",
    "PAY-003",
    "PAY-004",
    "PAY-005",
    "AP-010",
    "DATE-001",
    "DATE-002",
    "DATE-003",
    "DATE-004",
    "DATE-005",
]


def select_primary_failed_validation(
    failed_validations: list[dict],
) -> dict | None:
    by_code = {
        item.get("rule_code"): item
        for item in failed_validations
    }
    for rule_code in PRIMARY_RULE_ORDER:
        if rule_code in by_code:
            return by_code[rule_code]
    return failed_validations[0] if failed_validations else None


class ClassificationAgent:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def classify(
        self,
        invoice_payload: dict,
        failed_validations: list[dict],
    ) -> ClassificationOutput:
        primary = select_primary_failed_validation(failed_validations)
        if primary and primary.get("rule_code") == "AP-005":
            category = (
                (primary.get("details") or {}).get("category")
                or "CURRENCY_MISMATCH"
            )
            return ClassificationOutput(
                category=category,
                confidence=1.0,
                rationale=primary.get("message") or category,
                priority="HIGH",
                owner_team="AP",
            )
        if (
            primary
            and primary.get("rule_code") in {"AP-004", "VND-003"}
            and (primary.get("details") or {}).get("vendor_match_status")
            == "MANUAL_REVIEW"
        ):
            return ClassificationOutput(
                category="MANUAL_REVIEW",
                confidence=1.0,
                rationale=primary.get("message") or "Controlled vendor review.",
                priority="HIGH",
                owner_team="AP",
            )
        if primary and primary.get("rule_code") in RULE_PRIORITY:
            category, priority, owner = RULE_PRIORITY[primary["rule_code"]]
            return ClassificationOutput(
                category=category,
                confidence=1.0,
                rationale=(
                    "Primary failed rule selected by deterministic "
                    f"priority is {primary.get('rule_code')}: "
                    f"{primary.get('message')}"
                ),
                priority=priority,
                owner_team=owner,
            )

        data = self.llm.generate_json(
            task="classification",
            system_prompt=SYSTEM_PROMPT,
            payload={
                "invoice": invoice_payload,
                "failed_validations": failed_validations,
            },
            schema_hint=ClassificationOutput.model_json_schema(),
        )
        return ClassificationOutput.model_validate(data)
