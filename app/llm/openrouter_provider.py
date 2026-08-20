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


class OpenRouterProvider:
    """Thin OpenRouter client for autonomous tool use and strict final JSON."""

    # OpenRouter exposes JSON-schema structured outputs and tool calling for
    # compatible models. The request also sets require_parameters so routing is
    # limited to endpoints that advertise support for every parameter we send.
    supports_response_schema = True
    supports_response_schema_with_tools = True

    def __init__(self, settings: Settings) -> None:
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is not configured.")
        if not settings.llm_model:
            raise ValueError("LLM_MODEL is not configured.")
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
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

        Tool-enabled calls remain autonomous: the model may request a tool or
        finish with content. When a response schema is supplied, final content
        is constrained to the project contract while tools remain available.
        """
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
            kwargs["extra_body"] = {
                "provider": {"require_parameters": True}
            }

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
