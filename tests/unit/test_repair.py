"""Tests for the single ephemeral no-new-tools repair pass."""

import json

import pytest

from app.llm.base import TransientLLMFailure
from app.messages import CanonicalMessage
from app.output_parser import ModelAssessment
from app.repair import RepairFailed, repair_final_output

from tests.unit.fakes import FakeProvider, final_response, tool_call_response

VALID_RESULT = {
    "status": "COMPLETED",
    "reasoning_chain": ["Verified ORD-1001", "Policy ELIGIBLE", "Refund APPROVED"],
    "action_taken": {
        "tools_called": ["get_order_details", "check_return_policy", "process_refund"],
        "cases": [
            {
                "order_id": "ORD-1001",
                "decision": "AUTO_REFUND_APPROVED",
                "refund_amount": 35.0,
                "refund_id": "RF-1001-3500",
            }
        ],
    },
    "customer_response": "Your refund was approved.",
}

RUN_MESSAGES = [
    CanonicalMessage(role="system", content="You are the operations resolver."),
    CanonicalMessage(role="user", content="Refund ORD-1001, it arrived damaged."),
]


def _repair(provider, messages=None):
    return repair_final_output(
        provider,
        RUN_MESSAGES if messages is None else messages,
        "not json at all",
        ["Final output does not contain a JSON object."],
        max_retries=2,
        backoff_seconds=0.0,
        sleep=lambda _seconds: None,
    )


def test_malformed_first_valid_correction_accepted_without_tools():
    provider = FakeProvider([final_response(json.dumps(VALID_RESULT))])
    result, assessment = _repair(provider)

    assert result.status.value == "COMPLETED"
    assert result.action_taken.cases[0].refund_id == "RF-1001-3500"
    assert assessment == ModelAssessment()
    assert len(provider.calls) == 1
    assert provider.calls[0][1] is None, "repair must call the provider with tools=None"


def test_caller_messages_are_never_contaminated():
    messages = list(RUN_MESSAGES)
    provider = FakeProvider([final_response(json.dumps(VALID_RESULT))])
    _repair(provider, messages=messages)
    assert messages == RUN_MESSAGES

    # The ephemeral repair exchange carried the correction instruction.
    repair_messages = provider.calls[0][0]
    assert len(repair_messages) == len(RUN_MESSAGES) + 1
    correction = repair_messages[-1]
    assert "Problems found" in correction.content
    assert "Final output does not contain a JSON object." in correction.content
    assert "not json at all" in correction.content


def test_still_invalid_correction_raises_repair_failed():
    provider = FakeProvider([final_response("I cannot fix it, sorry.")])
    with pytest.raises(RepairFailed, match="still invalid"):
        _repair(provider)


def test_repair_response_requesting_tool_calls_raises():
    provider = FakeProvider([tool_call_response(("c1", "get_order_details", {"order_id": "ORD-1001"}))])
    with pytest.raises(RepairFailed, match="tool calls"):
        _repair(provider)


def test_transient_failure_retried_within_single_repair_attempt():
    provider = FakeProvider([TransientLLMFailure("503"), final_response(json.dumps(VALID_RESULT))])
    result, _ = _repair(provider)
    assert result.status.value == "COMPLETED"
    assert len(provider.calls) == 2


def test_exhausted_transient_retries_raise_repair_failed():
    provider = FakeProvider([TransientLLMFailure("503")] * 5)
    with pytest.raises(RepairFailed, match="failed"):
        _repair(provider)
    assert len(provider.calls) == 3, "1 initial attempt + 2 configured retries"


def test_non_transient_failure_raises_repair_failed_without_retry():
    provider = FakeProvider([ValueError("bad api key")])
    with pytest.raises(RepairFailed, match="bad api key"):
        _repair(provider)
    assert len(provider.calls) == 1


def test_assessment_is_preserved_through_repair():
    payload = dict(VALID_RESULT, sentiment="frustrated", urgency="high")
    provider = FakeProvider([final_response(json.dumps(payload))])
    result, assessment = _repair(provider)
    assert assessment.sentiment == "frustrated"
    assert assessment.urgency == "high"
    assert set(result.model_dump()) == {
        "status",
        "reasoning_chain",
        "action_taken",
        "customer_response",
    }
