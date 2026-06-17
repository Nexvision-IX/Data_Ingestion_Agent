from __future__ import annotations

from app.integrations.llm.base import LLMClient
from app.schemas import ClassificationOutput


SYSTEM_PROMPT = """
You are an Accounts Payable exception classification agent.
Use only the supplied invoice and deterministic validation results.
Choose the most important root-cause category. Do not invent SAP facts.
Return confidence, concise rationale, business priority, and owner team.
The output must follow the supplied JSON schema exactly.
""".strip()


class ClassificationAgent:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def classify(
        self,
        invoice_payload: dict,
        failed_validations: list[dict],
    ) -> ClassificationOutput:
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
