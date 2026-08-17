"""Tests for the structured final-output contracts."""

import pytest
from pydantic import ValidationError

from app.schemas import (
    ActionTaken,
    AgentResult,
    CaseResult,
    Decision,
    FinalStatus,
)


def _sample_result() -> AgentResult:
    return AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Order ORD-1001 verified; policy ELIGIBLE; refund approved."],
        action_taken=ActionTaken(
            tools_called=["get_order_details", "check_return_policy", "process_refund"],
            cases=[
                CaseResult(
                    order_id="ORD-1001",
                    decision=Decision.AUTO_REFUND_APPROVED,
                    refund_amount=35.0,
                    refund_id="RF-1001-3500",
                    policy_verdict="ELIGIBLE",
                )
            ],
        ),
        customer_response="Your refund was approved.",
    )


def test_quest_required_top_level_fields_present():
    payload = _sample_result().model_dump(mode="json")
    assert set(payload) == {
        "status",
        "reasoning_chain",
        "action_taken",
        "customer_response",
    }


def test_cases_is_always_a_list():
    result = AgentResult(
        status=FinalStatus.NEEDS_CLARIFICATION,
        reasoning_chain=["Order id missing; asked the customer to confirm it."],
        action_taken=ActionTaken(),
        customer_response="Please confirm your order number.",
    )
    payload = result.model_dump(mode="json")
    assert payload["action_taken"]["cases"] == []
    assert payload["action_taken"]["tools_called"] == []


def test_round_trip_through_json():
    original = _sample_result()
    restored = AgentResult.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.action_taken.cases[0].decision is Decision.AUTO_REFUND_APPROVED


def test_optional_case_fields_default_to_none():
    case = CaseResult(order_id="ORD-2222", decision=Decision.NO_ACTION)
    assert case.refund_amount is None
    assert case.refund_id is None
    assert case.policy_verdict is None
    assert case.error_code is None
    assert case.escalation_reasons == []


def _kwargs(**overrides):
    base = {
        "status": FinalStatus.COMPLETED,
        "reasoning_chain": ["Verified order, checked policy, approved refund."],
        "action_taken": ActionTaken(),
        "customer_response": "Your refund was approved.",
    }
    base.update(overrides)
    return base


def test_missing_reasoning_chain_fails_validation():
    kwargs = _kwargs()
    del kwargs["reasoning_chain"]
    with pytest.raises(ValidationError):
        AgentResult(**kwargs)


def test_empty_reasoning_chain_fails_validation():
    with pytest.raises(ValidationError):
        AgentResult(**_kwargs(reasoning_chain=[]))


def test_missing_customer_response_fails_validation():
    kwargs = _kwargs()
    del kwargs["customer_response"]
    with pytest.raises(ValidationError):
        AgentResult(**kwargs)


def test_empty_customer_response_fails_validation():
    with pytest.raises(ValidationError):
        AgentResult(**_kwargs(customer_response=""))


@pytest.mark.parametrize("status", [FinalStatus.NEEDS_CLARIFICATION, FinalStatus.FAILED_SAFE])
def test_non_completed_outputs_stay_valid(status):
    result = AgentResult(
        status=status,
        reasoning_chain=["Trusted evidence was insufficient; stopped safely."],
        action_taken=ActionTaken(tools_called=["get_order_details"]),
        customer_response="We need a bit more information to continue.",
    )
    assert result.status is status
    assert len(result.reasoning_chain) == 1


def test_unexpected_extra_fields_are_forbidden():
    """Fix 9: the frozen external contract rejects arbitrary extra fields."""
    with pytest.raises(ValidationError):
        AgentResult(**_kwargs(confidence=0.9))
    with pytest.raises(ValidationError):
        AgentResult(**_kwargs(recommendation="approve"))
    with pytest.raises(ValidationError):
        ActionTaken(tools_called=[], cases=[], extra_field="x")
    with pytest.raises(ValidationError):
        CaseResult(order_id="ORD-1001", decision=Decision.REJECTED, confidence=0.5)
