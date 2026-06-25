from __future__ import annotations

import copy
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_APP_ROOT = PROJECT_ROOT / "agent_app"
sys.path.insert(0, str(AGENT_APP_ROOT))

# Establish the documented local default before app.config is imported.
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_MODEL"] = ""

from app.integrations.llm.errors import (  # noqa: E402
    LLMConfigurationError,
    LLMResponseError,
)
from app.integrations.llm.factory import get_llm_client  # noqa: E402
from app.integrations.llm.groq import parse_json_object  # noqa: E402
from app.integrations.llm.mock import MockLLMClient  # noqa: E402


def expect_error(error_type, callback, expected_text: str) -> None:
    try:
        callback()
    except error_type as exc:
        assert expected_text in str(exc), str(exc)
    else:
        raise AssertionError(f"Expected {error_type.__name__}")


def test_default_provider_is_mock() -> None:
    client = get_llm_client()
    assert isinstance(client, MockLLMClient)
    assert client.audit_metadata(task="classification") == {
        "provider": "mock",
        "model": "mock-model",
        "task": "classification",
    }


def test_mock_works_without_api_key() -> None:
    client = get_llm_client(provider="mock", api_key="")
    result = client.generate_json(
        task="classification",
        system_prompt="Classify only; do not approve.",
        payload={
            "invoice": {"invoice_number": "INV-CP15"},
            "failed_validations": [
                {
                    "rule_code": "FIN-004",
                    "message": "Document total mismatch",
                }
            ],
        },
        schema_hint={"type": "object"},
    )
    assert result["category"] == "FINANCIAL_MISMATCH"


def test_real_provider_requires_api_key() -> None:
    expect_error(
        LLMConfigurationError,
        lambda: get_llm_client(
            provider="groq",
            api_key="",
            model="llama-test",
        ),
        "LLM_API_KEY is required",
    )


def test_openai_requires_api_key() -> None:
    expect_error(
        LLMConfigurationError,
        lambda: get_llm_client(
            provider="openai",
            api_key="",
            model="openai-test-model",
        ),
        "LLM_API_KEY is required when LLM_PROVIDER=openai",
    )


def test_anthropic_requires_api_key() -> None:
    expect_error(
        LLMConfigurationError,
        lambda: get_llm_client(
            provider="anthropic",
            api_key="",
            model="anthropic-test-model",
        ),
        "LLM_API_KEY is required when LLM_PROVIDER=anthropic",
    )


def test_unsupported_provider_is_controlled() -> None:
    expect_error(
        LLMConfigurationError,
        lambda: get_llm_client(provider="not-a-provider"),
        "Unsupported LLM_PROVIDER",
    )


def test_strict_json_handling_is_controlled() -> None:
    assert parse_json_object('{"decision": "WAIT"}') == {
        "decision": "WAIT"
    }
    expect_error(
        LLMResponseError,
        lambda: parse_json_object("```json\n{\"decision\":\"WAIT\"}\n```"),
        "invalid JSON",
    )
    expect_error(
        LLMResponseError,
        lambda: parse_json_object('["WAIT"]'),
        "must be a JSON object",
    )


def test_llm_cannot_bypass_deterministic_controls() -> None:
    failed_result = {
        "rule_code": "TAX-003",
        "passed": False,
        "severity": "ERROR",
        "message": "Header tax does not match calculated tax",
    }
    original = copy.deepcopy(failed_result)
    client = get_llm_client(provider="mock")
    advisory = client.generate_json(
        task="classification",
        system_prompt="Advisory classification only.",
        payload={
            "invoice": {"invoice_number": "INV-GUARD"},
            "failed_validations": [failed_result],
        },
        schema_hint={"type": "object"},
    )

    assert failed_result == original
    assert failed_result["passed"] is False
    assert advisory["category"] == "TAX_MISMATCH"
    assert not hasattr(client, "approve_invoice")
    assert not hasattr(client, "post_invoice")


def main() -> None:
    tests = [
        test_default_provider_is_mock,
        test_mock_works_without_api_key,
        test_real_provider_requires_api_key,
        test_openai_requires_api_key,
        test_anthropic_requires_api_key,
        test_unsupported_provider_is_controlled,
        test_strict_json_handling_is_controlled,
        test_llm_cannot_bypass_deterministic_controls,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("All CP-15 LLM provider factory tests passed.")


if __name__ == "__main__":
    main()
