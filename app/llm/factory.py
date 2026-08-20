"""Provider construction kept outside the autonomous agent loop."""

from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.groq_provider import GroqProvider
from app.llm.openrouter_provider import OpenRouterProvider

_PROVIDER_TYPES = {
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
}


def supported_provider_names() -> frozenset[str]:
    return frozenset(_PROVIDER_TYPES)


def required_api_key_name(provider_name: str) -> str:
    normalized = provider_name.strip().lower()
    if normalized == "groq":
        return "GROQ_API_KEY"
    if normalized == "openrouter":
        return "OPENROUTER_API_KEY"
    raise ValueError(
        f"Unsupported LLM_PROVIDER {provider_name!r}; supported providers: "
        + ", ".join(sorted(_PROVIDER_TYPES))
    )


def has_provider_api_key(settings: Settings) -> bool:
    normalized = settings.llm_provider.strip().lower()
    if normalized == "groq":
        return bool(settings.groq_api_key)
    if normalized == "openrouter":
        return bool(settings.openrouter_api_key)
    return False


def build_provider(settings: Settings) -> LLMProvider:
    normalized = settings.llm_provider.strip().lower()
    provider_type = _PROVIDER_TYPES.get(normalized)
    if provider_type is None:
        raise ValueError(
            f"Unsupported LLM_PROVIDER {settings.llm_provider!r}; supported providers: "
            + ", ".join(sorted(_PROVIDER_TYPES))
        )
    return provider_type(settings)
