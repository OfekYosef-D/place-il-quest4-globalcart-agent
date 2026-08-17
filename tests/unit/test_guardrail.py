"""Tests for the deterministic process_refund precondition guardrail."""

import json

from app.guardrail import (
    GUARDRAIL_REASON,
    blocked_tool_observation,
    check_refund_precondition,
)
from app.state import AgentState, CaseState


def _state_with_policy(policy_result) -> AgentState:
    state = AgentState(step_count=3)
    state.cases["ORD-1001"] = CaseState(order_id="ORD-1001", policy_result=policy_result)
    return state


def test_blocked_when_no_case_exists():
    event = check_refund_precondition(
        AgentState(), {"order_id": "ORD-1001", "amount": 35.0}
    )
    assert event is not None
    assert event.reason == GUARDRAIL_REASON
    assert event.tool_name == "process_refund"


def test_blocked_when_policy_result_missing():
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(order_id="ORD-1001")
    event = check_refund_precondition(state, {"order_id": "ORD-1001", "amount": 35.0})
    assert event is not None


def test_blocked_when_policy_result_is_a_business_error():
    state = _state_with_policy({"error": "ORDER_NOT_FOUND", "message": "..."})
    assert check_refund_precondition(state, {"order_id": "ORD-1001"}) is not None


def test_blocked_when_policy_says_ineligible():
    state = _state_with_policy(
        {"order_id": "ORD-1003", "eligible": False, "verdict": "OUTSIDE_RETURN_WINDOW"}
    )
    event = check_refund_precondition(state, {"order_id": "ORD-1001"})
    assert event is not None
    assert event.reason == GUARDRAIL_REASON


def test_blocked_when_order_id_argument_missing_or_not_a_string():
    state = _state_with_policy({"eligible": True, "verdict": "ELIGIBLE"})
    assert check_refund_precondition(state, {}) is not None
    assert check_refund_precondition(state, {"order_id": 1001}) is not None


def test_allowed_when_trusted_eligible_policy_exists():
    # Covers escalation-prone orders too (ORD-1002/1011/1005): eligible=true
    # passes the precondition; escalation is enforced inside process_refund.
    state = _state_with_policy({"eligible": True, "verdict": "ELIGIBLE"})
    assert check_refund_precondition(state, {"order_id": "ORD-1001"}) is None


def test_observation_is_structured_and_model_safe():
    state = AgentState()
    event = check_refund_precondition(state, {"order_id": "ORD-1001"})
    payload = json.loads(blocked_tool_observation(event))

    assert payload["blocked"] is True
    assert payload["tool"] == "process_refund"
    assert payload["reason_code"] == GUARDRAIL_REASON
    assert "eligible=true" in payload["message"]
    # No internal risk signals leak into the observation.
    text = json.dumps(payload).lower()
    for forbidden in ("fraud", "risk score", "ltv"):
        assert forbidden not in text
