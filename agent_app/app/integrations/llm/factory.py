from app.config import settings
from app.integrations.llm.base import LLMClient
from app.integrations.llm.http_clients import AnthropicClient, OpenAICompatibleClient
from app.integrations.llm.mock import MockLLMClient


def get_llm_client() -> LLMClient:
    if settings.llm_provider == "mock":
        return MockLLMClient()
    if settings.llm_provider == "openai":
        return OpenAICompatibleClient()
    if settings.llm_provider == "anthropic":
        return AnthropicClient()
    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
