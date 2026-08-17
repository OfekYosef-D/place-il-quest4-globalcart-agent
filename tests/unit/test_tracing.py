"""Tests for tracing/metrics primitives."""

import dataclasses

import pytest

from app.tracing import (
    BlockedToolEvent,
    ModelCallRecord,
    RunSummary,
    ToolCallRecord,
    estimate_cost,
)


def test_model_call_record_defaults():
    record = ModelCallRecord(step=1, provider="groq", model="m", kind="tool_call")
    assert record.attempt == 1
    assert record.retries == 0
    assert record.input_tokens is None


def test_tool_call_record_fields():
    record = ToolCallRecord(
        step=2,
        tool_name="get_order_details",
        arguments={"order_id": "ORD-1001"},
        duration_ms=0.4,
        cache_hit=True,
    )
    assert record.cache_hit is True
    assert record.result_summary is None


def test_blocked_tool_event_is_a_plain_trace_record():
    event = BlockedToolEvent(
        step=3,
        tool_name="process_refund",
        arguments={"order_id": "ORD-1001", "amount": 35.0},
        reason="MISSING_ELIGIBLE_POLICY_PRECONDITION",
    )
    payload = dataclasses.asdict(event)
    assert payload == {
        "step": 3,
        "tool_name": "process_refund",
        "arguments": {"order_id": "ORD-1001", "amount": 35.0},
        "reason": "MISSING_ELIGIBLE_POLICY_PRECONDITION",
    }


def test_run_summary_defaults():
    summary = RunSummary()
    assert summary.llm_calls == 0
    assert summary.tool_calls == 0
    assert summary.total_tokens == 0
    assert summary.total_duration_ms == 0.0
    assert summary.estimated_cost_usd is None


def test_estimate_cost_requires_both_prices():
    assert estimate_cost(100, 50, None, None) is None
    assert estimate_cost(100, 50, 0.1, None) is None
    assert estimate_cost(100, 50, None, 0.5) is None


def test_estimate_cost_math():
    cost = estimate_cost(1_000_000, 500_000, 0.5, 1.0)
    assert cost == pytest.approx(0.5 * 1.0 + 1.0 * 0.5)
