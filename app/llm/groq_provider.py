"""Groq provider over the OpenAI-compatible chat completions API (spec section 6).

Knows only how to call one provider and normalize the result into
`ModelResponse`, including converting canonical (supplied, Anthropic-shaped)
tool schemas into this provider's wire format. Retry policy belongs to the
runtime (Milestone 2).
"""

from __future__ import annotations

import copy
import json
import time
from typing import Any

import openai

from app.config import Settings
from app.llm.base import ModelResponse, ToolCallRequest, TransientLLMFailure

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

#: Known transient failure classes worth retrying (runtime decides the policy).
_TRANSIENT_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)


def to_openai_function_tools(
    schemas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert canonical Anthropic-shaped tool schemas to OpenAI function format.

    The runtime passes the supplied `TOOL_SCHEMAS` unchanged; only this
    provider layer knows the wire format. Input objects are never mutated.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema.get("description", ""),
                "parameters": copy.deepcopy(schema["input_schema"]),
            },
        }
        for schema in schemas
    ]


def normalize_response(
    raw: Any, *, provider: str, fallback_model: str, latency_ms: float
) -> ModelResponse:
    """Convert a raw chat-completion object into the normalized contract.

    Accepts any object with the OpenAI completion shape, so it can be unit
    tested without network access.
    """
    message = raw.choices[0].message

    tool_calls: list[ToolCallRequest] = []
    for tc in getattr(message, "tool_calls", None) or []:
        raw_args = tc.function.arguments or ""
        parsed: Any = None
        try:
            parsed = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict):
            parsed = None
        tool_calls.append(
            ToolCallRequest(
                id=getattr(tc, "id", None),
                name=tc.function.name,
                arguments=parsed if parsed is not None else {},
                raw_arguments=raw_args if parsed is None and raw_args else None,
            )
        )

    usage = getattr(raw, "usage", None)
    raw_usage = usage.model_dump() if usage is not None and hasattr(usage, "model_dump") else None

    return ModelResponse(
        content=getattr(message, "content", None),
        tool_calls=tool_calls,
        provider=provider,
        model=getattr(raw, "model", None) or fallback_model,
        input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        total_tokens=getattr(usage, "total_tokens", None) if usage else None,
        latency_ms=latency_ms,
        raw_usage=raw_usage,
    )


class GroqProvider:
    """Thin OpenAI-compatible client pointed at Groq."""

    def __init__(self, settings: Settings) -> None:
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is not configured.")
        if not settings.llm_model:
            raise ValueError("LLM_MODEL is not configured.")
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._client = openai.OpenAI(
            api_key=settings.groq_api_key,
            base_url=GROQ_BASE_URL,
            timeout=settings.llm_timeout_seconds,
        )

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Call the provider with canonical tool schemas.

        `tools` are the supplied Anthropic-shaped schemas; conversion to the
        OpenAI function wire format happens here, never in the runtime.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if tools:
            kwargs["tools"] = to_openai_function_tools(tools)

        start = time.perf_counter()
        try:
            raw = self._client.chat.completions.create(**kwargs)
        except _TRANSIENT_ERRORS as exc:
            raise TransientLLMFailure(str(exc)) from exc
        latency_ms = (time.perf_counter() - start) * 1000

        return normalize_response(
            raw, provider="groq", fallback_model=self._model, latency_ms=latency_ms
        )
