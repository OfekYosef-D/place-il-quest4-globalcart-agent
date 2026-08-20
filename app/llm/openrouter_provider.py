"""OpenRouter adapter over the OpenAI-compatible chat-completions API.

OpenRouter is used as a gateway to hosted models while the agent runtime stays
provider-neutral. The adapter owns authentication, routing hints, wire-format
conversion, structured-output requests, and response normalization.
"""

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
    to_wire_messages,
)
from app.messages import Message

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_TRANSIENT_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)

# Capability is explicit rather than assumed for every model OpenRouter hosts.
# The selected Qwen route advertises `response_format`; future model IDs should
# be added only after checking the current OpenRouter model metadata/docs.
_STRUCTURED_OUTPUT_MODELS = frozenset({"qwen/qwen3.5-397b-a17b"})


class OpenRouterProvider:
    """Thin OpenRouter client for tool use and strict no-tools final JSON."""

    supports_response_schema: bool

    def __init__(self, settings: Settings) -> None:
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is not configured.")
        if not settings.llm_model:
            raise ValueError("LLM_MODEL is not configured.")
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self.supports_response_schema = self._model in _STRUCTURED_OUTPUT_MODELS
        self._client = openai.OpenAI(
            api_key=settings.openrouter_api_key,
            base_url=OPENROUTER_BASE_URL,
            timeout=settings.llm_timeout_seconds,
        )

    def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> ModelResponse:
        """Call the configured OpenRouter model.

        Normal agentic calls expose tools and let the model choose the next
        action. Schema-constrained calls are intentionally no-tools calls so
        the project does not depend on an unverified combination of provider
        features. The parser and deterministic validator remain authoritative.
        """
        if response_schema is not None and tools:
            raise ValueError("OpenRouter response_schema requires tools=None")
        if response_schema is not None and not self.supports_response_schema:
            raise ValueError(
                f"OpenRouter model {self._model!r} is not verified for strict response schemas"
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

        if tools or response_schema is not None:
            # Restrict routing to inference endpoints that advertise support
            # for every request parameter instead of silently degrading it.
            kwargs["extra_body"] = {"provider": {"require_parameters": True}}

        start = time.perf_counter()
        try:
            raw = self._client.chat.completions.create(**kwargs)
        except _TRANSIENT_ERRORS as exc:
            raise TransientLLMFailure(str(exc)) from exc
        latency_ms = (time.perf_counter() - start) * 1000

        return normalize_response(
            raw,
            provider="openrouter",
            fallback_model=self._model,
            latency_ms=latency_ms,
        )
