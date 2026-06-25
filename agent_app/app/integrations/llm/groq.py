from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.integrations.llm.base import LLMClient
from app.integrations.llm.errors import LLMConfigurationError, LLMResponseError


logger = logging.getLogger(__name__)


def parse_json_object(content: str) -> dict[str, Any]:
    """Strictly parse one JSON object without extracting markdown or prose."""
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise LLMResponseError(
            "The advisory LLM returned invalid JSON."
        ) from exc
    if not isinstance(parsed, dict):
        raise LLMResponseError(
            "The advisory LLM response must be a JSON object."
        )
    return parsed


class GroqLLMClient(LLMClient):
    """Groq-backed advisory client using its OpenAI-compatible API."""

    provider_name = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: int = 60,
        max_retries: int = 2,
    ):
        if not api_key.strip():
            raise LLMConfigurationError(
                "LLM_API_KEY is required when LLM_PROVIDER=groq."
            )
        if not model.strip():
            raise LLMConfigurationError(
                "LLM_MODEL is required when LLM_PROVIDER=groq."
            )
        self._api_key = api_key
        self.model_name = model.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)

    def generate_json(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = self.audit_metadata(task=task)
        logger.info(
            "Advisory LLM request provider=%s model=%s task=%s",
            metadata["provider"],
            metadata["model"],
            metadata["task"],
        )
        request_body = {
            "model": self.model_name,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": task,
                            "input": payload,
                            "required_output_schema": schema_hint,
                            "instruction": (
                                "Return exactly one valid JSON object. "
                                "Do not include markdown or surrounding prose. "
                                "This task is advisory and cannot approve, "
                                "reject, or post an invoice."
                            ),
                        },
                        default=str,
                    ),
                },
            ],
        }

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                return parse_json_object(content)
            except LLMResponseError:
                raise
            except (
                httpx.TimeoutException,
                httpx.TransportError,
                httpx.HTTPStatusError,
                KeyError,
                IndexError,
                TypeError,
                ValueError,
            ) as exc:
                last_error = exc
                retryable = (
                    isinstance(exc, (httpx.TimeoutException, httpx.TransportError))
                    or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and (
                            exc.response.status_code == 429
                            or exc.response.status_code >= 500
                        )
                    )
                )
                if not retryable or attempt >= self.max_retries:
                    break
                time.sleep(min(0.25 * (2**attempt), 1.0))

        logger.warning(
            "Advisory LLM request failed provider=%s model=%s task=%s",
            metadata["provider"],
            metadata["model"],
            metadata["task"],
        )
        raise LLMResponseError(
            "The advisory Groq LLM request failed; deterministic AP controls "
            "were not changed or bypassed."
        ) from last_error
