from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    """Advisory LLM interface for explanation and workflow assistance.

    Implementations may classify exceptions, draft communications, and
    summarize recheck evidence. They must not replace deterministic AP
    validation or make posting-approval decisions.
    """

    provider_name = "unknown"
    model_name = ""

    def audit_metadata(self, *, task: str) -> dict[str, str]:
        """Return non-sensitive metadata suitable for workflow logging."""
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "task": task,
        }

    @abstractmethod
    def generate_json(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError
