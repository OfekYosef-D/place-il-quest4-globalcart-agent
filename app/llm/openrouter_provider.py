"""OpenRouter adapter over the OpenAI-compatible chat-completions API.

OpenRouter is used as a gateway to hosted models while the agent runtime stays
provider-neutral. The adapter owns authentication, request routing hints,
OpenAI wire conversion, structured-output requests, and response normalization.
"""

from __future__ import annotations

import time
from typing import Any

import openai

from app.config import Settings
from app.llm.base import ModelResponse, TransientLLMFailure
from app.llm.openai_compat import (
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
    """Thin OpenRouter client for tool use and schema-constrained final output."""

    # The gateway API can request JSON Schema output. `require_parameters`
    # below restricts routing to inference endpoints that support the request
    # parameters we actually send.
    supports_response_schema = True

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

        Normal agentic calls expose tools and let the model choose the next
        action. No-tools finalization/repair calls may additionally request a
        JSON-Schema-constrained response. The runtime never sees OpenRouter
        request fields.
        """
        if response_schema is not None and tools:
            raise ValueError("response_schema requires tools=None")

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": to_wire_messages(messages),
            "temperature": self._temperature,
        }
        if tools:
            kwargs["tools"] = to_openai_function_tools(tools)
        if response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "globalcart_agent_result",
                    "strict": True,
                    "schema": response_schema,
                },
            }

        # OpenRouter can route one model through multiple inference providers.
        # When tools/structured output are requested, keep only endpoints that
        # advertise support for the exact parameters instead of silently
        # degrading the protocol.
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
