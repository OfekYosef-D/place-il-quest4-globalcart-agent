"""Small provider wrapper that binds the resolver's final response schema.

The agent loop stays provider-agnostic and keeps its existing `generate`
contract. Providers that can combine tool calling with structured outputs get
the schema automatically; providers that cannot combine them continue their
normal tool loop and can still use schema-constrained no-tools repair calls.
"""

from __future__ import annotations

from typing import Any

from app.llm.base import LLMProvider, ModelResponse


class SchemaBoundProvider:
    """Inject a default final-response schema when the wrapped adapter supports it."""

    def __init__(self, provider: LLMProvider, response_schema: dict[str, Any]) -> None:
        self._provider = provider
        self._response_schema = response_schema
        self.supports_response_schema = getattr(
            provider, "supports_response_schema", False
        )
        self.supports_response_schema_with_tools = getattr(
            provider, "supports_response_schema_with_tools", False
        )

    def generate(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> ModelResponse:
        effective_schema = response_schema
        if (
            effective_schema is None
            and tools
            and self.supports_response_schema_with_tools
        ):
            effective_schema = self._response_schema
        return self._provider.generate(
            messages,
            tools,
            response_schema=effective_schema,
        )
