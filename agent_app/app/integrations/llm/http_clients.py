from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import settings
from app.integrations.llm.base import LLMClient


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("LLM did not return a JSON object")
        return json.loads(match.group(0))


class OpenAICompatibleClient(LLMClient):
    def generate_json(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY is not configured")

        base_url = settings.llm_base_url or "https://api.openai.com/v1"
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.llm_model,
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
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return _extract_json(content)


class AnthropicClient(LLMClient):
    def generate_json(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY is not configured")

        base_url = settings.llm_base_url or "https://api.anthropic.com/v1"
        response = httpx.post(
            f"{base_url.rstrip('/')}/messages",
            headers={
                "x-api-key": settings.llm_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.llm_model,
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
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        blocks = response.json()["content"]
        text = "".join(
            block.get("text", "")
            for block in blocks
            if block.get("type") == "text"
        )
        return _extract_json(text)
