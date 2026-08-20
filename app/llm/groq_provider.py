"""Groq provider over the OpenAI-compatible chat completions API (spec section 6).

Knows only how to call one provider and normalize the result into
`ModelResponse`, including converting canonical tool/message schemas and the
provider-neutral final-response schema into Groq's wire format.
"""

from __future__ import annotations

import copy
import json
import time
from typing import Any

import openai

from app.config import Settings
from app.llm.base import (
    ModelResponse,
    ToolCallRequest,
    TransientLLMFailure,
    ensure_unique_tool_call_ids,
)
from app.messages import (
    AssistantToolCallMessage,
    CanonicalMessage,
    Message,
    ToolObservationMessage,
)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_TRANSIENT_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)


def to_openai_function_tools(
    schemas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert canonical Anthropic-shaped tool schemas to OpenAI function format."""
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


def to_groq_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert provider-neutral JSON Schema to Groq strict-mode requirements.

    Groq strict Structured Outputs require every object property to be listed
    in `required` and every object to be closed with `additionalProperties:
    false`. Pydantic represents optional fields as nullable schemas that may be
    omitted, so at the provider boundary we require those nullable fields to be
    present (possibly as null) without changing the runtime/Pydantic contract.
    Unsupported/default annotations are removed from the wire schema.
    """

    def normalize(node: Any) -> Any:
        if isinstance(node, list):
            return [normalize(item) for item in node]
        if not isinstance(node, dict):
            return node

        normalized = {
            key: normalize(value)
            for key, value in node.items()
            if key != "default"
        }
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["additionalProperties"] = False
            normalized["required"] = list(properties.keys())
        return normalized

    return normalize(copy.deepcopy(schema))


def to_wire_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert canonical conversation messages to OpenAI-compatible wire dicts."""
    wire: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, CanonicalMessage):
            wire.append({"role": message.role, "content": message.content})
        elif isinstance(message, AssistantToolCallMessage):
            missing = [tc.name for tc in message.tool_calls if not tc.id]
            if missing:
                raise ValueError(
                    "AssistantToolCallMessage contains tool calls without "
                    f"ids ({', '.join(missing)}); ids are guaranteed at the "
                    "provider boundary and must be preserved end-to-end."
                )
            wire.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in message.tool_calls
                    ],
                }
            )
        elif isinstance(message, ToolObservationMessage):
            if not message.tool_call_id:
                raise ValueError(
                    "ToolObservationMessage is missing tool_call_id; observation "
                    "correlation must never fall back to an ambiguous placeholder."
                )
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content,
                }
            )
        else:
            raise TypeError(f"Unsupported canonical message type: {type(message)!r}")
    return wire


def normalize_response(
    raw: Any, *, provider: str, fallback_model: str, latency_ms: float
) -> ModelResponse:
    """Convert a raw chat-completion object into the normalized contract."""
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

    response = ModelResponse(
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
    return ensure_unique_tool_call_ids(response)


class GroqProvider:
    """Thin OpenAI-compatible client pointed at Groq."""

    supports_response_schema = True

    def __init__(self, settings: Settings) -> None:
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is not configured.")
        if not settings.llm_model:
            raise ValueError("LLM_MODEL is not configured.")
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._reasoning_effort = settings.llm_reasoning_effort
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
        """Call Groq using provider-owned wire-format translation.

        Groq documents Structured Outputs as incompatible with tool use, so a
        response schema is accepted only on a no-tools finalization/repair call.
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
                    "schema": to_groq_strict_schema(response_schema),
                },
            }
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
