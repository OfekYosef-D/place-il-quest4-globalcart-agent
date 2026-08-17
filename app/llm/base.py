"""Provider-agnostic LLM contracts (spec section 6).

The agent loop must never contain provider-specific logic; it talks to the
`LLMProvider` protocol and receives a normalized `ModelResponse`.
"""

from __future__ import annotations

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


@runtime_checkable
class LLMProvider(Protocol):
    """Conceptual interface: LLMProvider.generate(messages, tools) -> ModelResponse."""

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse: ...
