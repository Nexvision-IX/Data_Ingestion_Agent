class LLMProviderError(RuntimeError):
    """Controlled business error raised by advisory LLM integrations."""


class LLMConfigurationError(LLMProviderError):
    """The selected advisory LLM provider is not configured safely."""


class LLMResponseError(LLMProviderError):
    """The advisory LLM provider returned an unusable response."""
