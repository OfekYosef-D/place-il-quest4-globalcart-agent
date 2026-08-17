"""Shared deterministic fakes for runtime tests (no network, no real LLM)."""

from __future__ import annotations

from app.llm.base import ModelResponse, ToolCallRequest


class FakeProvider:
    """Scripted provider: replays `ModelResponse` items or raises exceptions.

    Each `generate` call pops the next scripted item. Records every call's
    messages snapshot and tools so tests can assert on what the runtime sent.
    """

    def __init__(self, scripted: list) -> None:
        self._scripted = list(scripted)
        self.calls: list[tuple[list, object]] = []

    def generate(self, messages, tools=None) -> ModelResponse:
        self.calls.append((list(messages), tools))
        if not self._scripted:
            raise AssertionError("FakeProvider script exhausted")
        item = self._scripted.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def tool_call_response(*calls: tuple[str, str, dict], content: str | None = None) -> ModelResponse:
    """Build a ModelResponse requesting tool calls.

    Each positional item is `(call_id, tool_name, arguments)`.
    """
    return ModelResponse(
        content=content,
        tool_calls=[
            ToolCallRequest(id=call_id, name=name, arguments=dict(arguments))
            for call_id, name, arguments in calls
        ],
        provider="fake",
        model="fake-model",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        latency_ms=1.0,
    )


def final_response(content: str) -> ModelResponse:
    """Build a ModelResponse carrying final (non-tool) content."""
    return ModelResponse(
        content=content,
        provider="fake",
        model="fake-model",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        latency_ms=1.0,
    )
