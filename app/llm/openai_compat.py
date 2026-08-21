"""Shared helpers for OpenAI-compatible chat-completions providers.

Canonical messages and supplied tool schemas stay provider-neutral in the
runtime. Adapters that speak the OpenAI chat-completions wire format reuse
these conversions, strict JSON-schema shaping, and normalized response parsing.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from app.llm.base import ModelResponse, ToolCallRequest, ensure_unique_tool_call_ids
from app.messages import (
    AssistantToolCallMessage,
    CanonicalMessage,
    Message,
    ToolObservationMessage,
)


def to_openai_function_tools(
    schemas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert canonical starter-kit schemas without mutating them."""
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


def to_wire_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert canonical conversation messages to OpenAI chat format."""
    wire: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, CanonicalMessage):
            wire.append({"role": message.role, "content": message.content})
        elif isinstance(message, AssistantToolCallMessage):
            missing = [tc.name for tc in message.tool_calls if not tc.id]
            if missing:
                raise ValueError(
                    "AssistantToolCallMessage contains tool calls without "
                    f"ids ({', '.join(missing)}); ids must be preserved end-to-end."
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
                    "correlation must never use an ambiguous placeholder."
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


def to_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize Pydantic JSON Schema for strict constrained decoding.

    Strict structured-output implementations commonly require closed objects
    and every property listed as required. Optional Pydantic fields remain
    nullable, but are required to be present as either their value or ``null``.
    Defaults are removed because they are not useful at the model boundary.
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


def json_schema_response_format(
    schema: dict[str, Any], *, name: str = "globalcart_agent_result"
) -> dict[str, Any]:
    """Build an OpenAI-compatible strict ``response_format`` payload."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": to_strict_json_schema(schema),
        },
    }


def normalize_response(
    raw: Any, *, provider: str, fallback_model: str, latency_ms: float
) -> ModelResponse:
    """Normalize an OpenAI-shaped chat completion into the runtime contract."""
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
    raw_usage = (
        usage.model_dump()
        if usage is not None and hasattr(usage, "model_dump")
        else None
    )
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
