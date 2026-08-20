"""Groq adapter over its OpenAI-compatible chat-completions API."""

from __future__ import annotations

import time
from typing import Any

import openai

from app.config import Settings
from app.llm.base import ModelResponse, TransientLLMFailure
from app.llm.openai_compat import (
    json_schema_response_format,
    normalize_response,
    to_openai_function_tools,
    to_strict_json_schema,
    to_wire_messages,
)
from app.messages import Message

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_TRANSIENT_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)

# Groq's current strict Structured Outputs documentation lists these model IDs.
_GROQ_STRICT_SCHEMA_MODELS = frozenset(
    {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}
)


def to_groq_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible alias for existing Groq strict-schema tests."""
    return to_strict_json_schema(schema)


class GroqProvider:
    """Thin Groq client preserving the provider-neutral runtime contract."""

    supports_response_schema: bool
    supports_response_schema_with_tools = False

    def __init__(self, settings: Settings) -> None:
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is not configured.")
        if not settings.llm_model:
            raise ValueError("LLM_MODEL is not configured.")
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._reasoning_effort = settings.llm_reasoning_effort
        self.supports_response_schema = self._model in _GROQ_STRICT_SCHEMA_MODELS
        self._client = openai.OpenAI(
            api_key=settings.groq_api_key,
            base_url=GROQ_BASE_URL,
            timeout=settings.llm_timeout_seconds,
        )

    def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> ModelResponse:
        """Call Groq using provider-owned request features.

        Groq documents Structured Outputs as incompatible with tool use, so a
        schema-constrained call is accepted only when no tools are exposed.
        """
        if response_schema is not None and tools:
            raise ValueError("Groq response_schema requires tools=None")
        if response_schema is not None and not self.supports_response_schema:
            raise ValueError(
                f"Groq model {self._model!r} does not support configured strict response schemas"
            )

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": to_wire_messages(messages),
            "temperature": self._temperature,
        }
        if tools:
            kwargs["tools"] = to_openai_function_tools(tools)
        if response_schema is not None:
            kwargs["response_format"] = json_schema_response_format(response_schema)
        if self._reasoning_effort is not None:
            kwargs["extra_body"] = {"reasoning_effort": self._reasoning_effort}

        start = time.perf_counter()
        try:
            raw = self._client.chat.completions.create(**kwargs)
        except _TRANSIENT_ERRORS as exc:
            raise TransientLLMFailure(str(exc)) from exc
        latency_ms = (time.perf_counter() - start) * 1000

        return normalize_response(
            raw, provider="groq", fallback_model=self._model, latency_ms=latency_ms
        )
