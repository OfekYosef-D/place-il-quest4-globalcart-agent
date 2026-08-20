"""Provider construction kept outside the autonomous agent loop."""

from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.groq_provider import GroqProvider
from app.llm.openrouter_provider import OpenRouterProvider
from app.llm.schema_bound import SchemaBoundProvider
from app.output_parser import final_output_json_schema

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
    """Build one adapter and bind the resolver's structured final contract.

    The wrapper is inert for providers that cannot combine tools with response
    schemas. For capable providers (currently OpenRouter), the same autonomous
    tool call can either request another tool or finish with schema-constrained
    final JSON.
    """
    normalized = settings.llm_provider.strip().lower()
    provider_type = _PROVIDER_TYPES.get(normalized)
    if provider_type is None:
        raise ValueError(
            f"Unsupported LLM_PROVIDER {settings.llm_provider!r}; supported providers: "
            + ", ".join(sorted(_PROVIDER_TYPES))
        )
    provider = provider_type(settings)
    return SchemaBoundProvider(provider, final_output_json_schema())
