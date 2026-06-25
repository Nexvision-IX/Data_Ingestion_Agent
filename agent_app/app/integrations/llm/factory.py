from app.config import settings
from app.integrations.llm.base import LLMClient
from app.integrations.llm.errors import LLMConfigurationError
from app.integrations.llm.groq import GroqLLMClient
from app.integrations.llm.http_clients import AnthropicClient, OpenAICompatibleClient
from app.integrations.llm.mock import MockLLMClient


def get_llm_client(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout_seconds: int | None = None,
    max_retries: int | None = None,
) -> LLMClient:
    """Build an advisory-only LLM client from explicit values or settings."""
    selected_provider = (provider or settings.llm_provider).strip().lower()
    selected_key = settings.llm_api_key if api_key is None else api_key
    selected_model = settings.llm_model if model is None else model
    selected_base_url = settings.llm_base_url if base_url is None else base_url
    selected_timeout = (
        settings.llm_timeout_seconds
        if timeout_seconds is None
        else timeout_seconds
    )
    selected_retries = (
        settings.llm_max_retries if max_retries is None else max_retries
    )

    if selected_provider == "mock":
        return MockLLMClient(model=selected_model or "mock-model")
    if selected_provider == "groq":
        return GroqLLMClient(
            api_key=selected_key,
            model=selected_model,
            base_url=selected_base_url
            or "https://api.groq.com/openai/v1",
            timeout_seconds=selected_timeout,
            max_retries=selected_retries,
        )
    if selected_provider == "openai":
        return OpenAICompatibleClient(
            api_key=selected_key,
            model=selected_model,
            base_url=selected_base_url or "https://api.openai.com/v1",
            timeout_seconds=selected_timeout,
        )
    if selected_provider == "anthropic":
        return AnthropicClient(
            api_key=selected_key,
            model=selected_model,
            base_url=selected_base_url or "https://api.anthropic.com/v1",
            timeout_seconds=selected_timeout,
        )
    raise LLMConfigurationError(
        f"Unsupported LLM_PROVIDER: {selected_provider or '<empty>'}."
    )
