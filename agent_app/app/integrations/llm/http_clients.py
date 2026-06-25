from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import settings
from app.integrations.llm.base import LLMClient
from app.integrations.llm.errors import LLMConfigurationError, LLMResponseError


def _extract_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise LLMResponseError(
            "The advisory LLM returned invalid JSON."
        ) from exc
    if not isinstance(parsed, dict):
        raise LLMResponseError(
            "The advisory LLM response must be a JSON object."
        )
    return parsed


class OpenAICompatibleClient(LLMClient):
    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
    ):
        self._api_key = settings.llm_api_key if api_key is None else api_key
        self.model_name = settings.llm_model if model is None else model
        self.base_url = (
            settings.llm_base_url if base_url is None else base_url
        ) or "https://api.openai.com/v1"
        self.timeout_seconds = (
            settings.llm_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if not self._api_key.strip():
            raise LLMConfigurationError(
                "LLM_API_KEY is required when LLM_PROVIDER=openai."
            )
        if not self.model_name.strip():
            raise LLMConfigurationError(
                "LLM_MODEL is required when LLM_PROVIDER=openai."
            )

    def generate_json(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
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
                                    "Return only one valid JSON object. "
                                    "Do not include markdown."
                                ),
                            },
                            default=str,
                        ),
                    },
                ],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return _extract_json(content)


class AnthropicClient(LLMClient):
    provider_name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
    ):
        self._api_key = settings.llm_api_key if api_key is None else api_key
        self.model_name = settings.llm_model if model is None else model
        self.base_url = (
            settings.llm_base_url if base_url is None else base_url
        ) or "https://api.anthropic.com/v1"
        self.timeout_seconds = (
            settings.llm_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if not self._api_key.strip():
            raise LLMConfigurationError(
                "LLM_API_KEY is required when LLM_PROVIDER=anthropic."
            )
        if not self.model_name.strip():
            raise LLMConfigurationError(
                "LLM_MODEL is required when LLM_PROVIDER=anthropic."
            )

    def generate_json(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_name,
                "max_tokens": 1200,
                "temperature": 0,
                "system": system_prompt,
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": task,
                                "input": payload,
                                "required_output_schema": schema_hint,
                                "instruction": (
                                    "Return only one valid JSON object. "
                                    "Do not include markdown."
                                ),
                            },
                            default=str,
                        ),
                    }
                ],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        blocks = response.json()["content"]
        text = "".join(
            block.get("text", "")
            for block in blocks
            if block.get("type") == "text"
        )
        return _extract_json(text)
