"""Provider-agnostic LLM contracts (spec section 6).

The agent loop must never contain provider-specific logic; it talks to the
`LLMProvider` protocol and receives a normalized `ModelResponse`.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class TransientLLMFailure(Exception):
    """A known transient LLM-call failure.

    Retry policy (initial attempt + configured retries) is owned by the
    runtime, not by providers. Non-transient failures propagate as-is and
    fail closed.
    """


class ToolCallRequest(BaseModel):
    """A model-requested tool call with structured arguments."""

    id: str | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    #: Present only when the model emitted arguments that could not be
    #: parsed as a JSON object, so the runtime can handle it honestly.
    raw_arguments: str | None = None


class ModelResponse(BaseModel):
    """Normalized result of one model call."""

    content: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: float | None = None
    #: Raw provider usage metadata, kept only for debugging.
    raw_usage: dict[str, Any] | None = None


def ensure_unique_tool_call_ids(response: ModelResponse) -> ModelResponse:
    """Guarantee a stable non-empty unique id on every requested tool call."""
    seen: set[str] = set()
    normalized: list[ToolCallRequest] = []
    changed = False
    for call in response.tool_calls:
        if call.id and call.id not in seen:
            seen.add(call.id)
            normalized.append(call)
            continue
        new_id = _fresh_tool_call_id(seen)
        seen.add(new_id)
        normalized.append(call.model_copy(update={"id": new_id}))
        changed = True
    if not changed:
        return response
    return response.model_copy(update={"tool_calls": normalized})


def _fresh_tool_call_id(seen: set[str]) -> str:
    candidate = f"call_{uuid.uuid4().hex}"
    while candidate in seen:
        candidate = f"call_{uuid.uuid4().hex}"
    return candidate


@runtime_checkable
class LLMProvider(Protocol):
    """Provider-neutral model interface used by the runtime.

    `messages` are canonical conversation records and `tools` are canonical
    starter-kit schemas. `response_schema`, when supplied, is provider-neutral
    JSON Schema for a no-tools structured response; the adapter owns translation
    to the provider wire format.
    """

    #: Adapter can request schema-constrained output when tools are disabled.
    supports_response_schema: bool

    def generate(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> ModelResponse: ...
