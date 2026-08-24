"""Shared deterministic fakes for runtime tests (no network, no real LLM)."""

from __future__ import annotations

import json

from app.llm.base import ModelResponse, ToolCallRequest


class FakeProvider:
    """Scripted provider: replays `ModelResponse` items or raises exceptions.

    Existing tests inspect `calls` as `(messages, tools)` pairs. Structured
    response contracts are recorded separately in `response_schemas` so the
    older call-shape contract stays stable.
    """

    supports_response_schema = False
    supports_response_schema_with_tools = False

    def __init__(
        self,
        scripted: list,
        *,
        supports_response_schema: bool = False,
        supports_response_schema_with_tools: bool = False,
    ) -> None:
        self._scripted = list(scripted)
        self.supports_response_schema = supports_response_schema
        self.supports_response_schema_with_tools = supports_response_schema_with_tools
        self.calls: list[tuple[list, object]] = []
        self.response_schemas: list[object] = []

    def generate(self, messages, tools=None, *, response_schema=None) -> ModelResponse:
        self.calls.append((list(messages), tools))
        self.response_schemas.append(response_schema)
        if not self._scripted:
            raise AssertionError("FakeProvider script exhausted")
        item = self._scripted.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def tool_call_response(*calls: tuple[str, str, dict], content: str | None = None) -> ModelResponse:
    """Build a ModelResponse requesting tool calls."""
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


def intake_response(
    *,
    intent: str = "SUPPORT_CASE",
    support_goal: str = "REFUND",
    case_reason: str = "damaged_on_arrival",
    reason_evidence: str | None = "damaged",
    issue_summary: str = "Customer reports an order issue.",
) -> ModelResponse:
    """Build the no-tools structured semantic intake response used by Stage 2 tests."""
    if case_reason == "unknown":
        reason_evidence = None
    return final_response(
        json.dumps(
            {
                "intent": intent,
                "support_goal": support_goal,
                "case_reason": case_reason,
                "reason_evidence": reason_evidence,
                "issue_summary": issue_summary,
            }
        )
    )
