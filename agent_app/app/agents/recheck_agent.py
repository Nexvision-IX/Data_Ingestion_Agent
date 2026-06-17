from __future__ import annotations

from app.integrations.llm.base import LLMClient
from app.schemas import RecheckOutput


SYSTEM_PROMPT = """
You are an Accounts Payable recheck and follow-up agent.
Your role is to decide the next workflow action, not to approve or post invoices.
Use the latest source-system snapshot, failed rules, previous exception, reply text,
and retry count. Choose exactly one: REVALIDATE, WAIT, ESCALATE, or CLOSE.
REVALIDATE means deterministic rules must run again.
WAIT means evidence is insufficient.
ESCALATE means retry or SLA limits or risk require a human.
CLOSE is only for an explicitly cancelled or withdrawn invoice.
Do not treat an email claim as proof that financial controls passed.
Return the requested JSON only.
""".strip()


class RecheckAgent:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def decide(self, payload: dict) -> RecheckOutput:
        data = self.llm.generate_json(
            task="recheck",
            system_prompt=SYSTEM_PROMPT,
            payload=payload,
            schema_hint=RecheckOutput.model_json_schema(),
        )
        return RecheckOutput.model_validate(data)
