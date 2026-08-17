"""Canonical conversation messages shared by the runtime and providers.

The runtime builds only these provider-agnostic shapes. Conversion to a
provider wire format (e.g. OpenAI-compatible chat completions) is owned by
the provider layer (`app.llm.groq_provider`), never by the agent loop
(docs/IMPLEMENTATION_SPEC.md sections 4 and 6).

Deliberately the smallest practical set: text messages, assistant
tool-call requests, and tool observations. `tool_call_id` correlation is
preserved end-to-end.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field

from app.llm.base import ToolCallRequest


class CanonicalMessage(BaseModel):
    """Plain text message: system prompt, customer turn, or assistant text."""

    role: Literal["system", "user", "assistant"]
    content: str


class AssistantToolCallMessage(BaseModel):
    """Assistant turn that requests one or more tool calls.

    `tool_calls` reuses `ToolCallRequest` so the model response and the
    conversation record share one typed shape.
    """

    content: str | None = None
    tool_calls: list[ToolCallRequest] = Field(min_length=1)


class ToolObservationMessage(BaseModel):
    """Trusted tool result handed back to the model.

    `tool_call_id` must equal the id of the assistant tool-call request this
    observation answers, so providers can correlate wire messages.
    """

    tool_call_id: str | None = None
    tool_name: str
    content: str  # JSON-encoded observation


#: Any message the runtime may put on the conversation.
Message = Union[CanonicalMessage, AssistantToolCallMessage, ToolObservationMessage]
