"""Regression and invariant tests for timeline-claim semantics."""

from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState
from app.validator import validate_result


def _rejected_state() -> AgentState:
    state = AgentState()
    state.cases["ORD-OLD"] = CaseState(
        order_id="ORD-OLD",
        policy_result={
            "eligible": False,
            "verdict": "OUTSIDE_RETURN_WINDOW",
            "policy_id": "POL-RET-01",
        },
    )
    return state


def _rejected_result(response: str) -> AgentResult:
    return AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Trusted policy result is OUTSIDE_RETURN_WINDOW."],
        action_taken=ActionTaken(
            tools_called=[],
            cases=[
                CaseResult(
                    order_id="ORD-OLD",
                    decision=Decision.REJECTED,
                    policy_verdict="OUTSIDE_RETURN_WINDOW",
                )
            ],
        ),
        customer_response=response,
    )


def test_policy_return_window_duration_is_not_an_operational_timeline():
    response = (
        "This request is outside the return window. Refund requests under this "
        "policy are allowed within 30 days of delivery."
    )
    assert validate_result(_rejected_result(response), _rejected_state()) == []


def test_future_refund_arrival_duration_is_still_rejected():
    state = AgentState()
    state.cases["ORD-A"] = CaseState(
        order_id="ORD-A",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={
            "status": "APPROVED",
            "approved_amount": 25.0,
            "refund_id": "RF-A",
        },
    )
    result = AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Refund APPROVED."],
        action_taken=ActionTaken(
            tools_called=[],
            cases=[
                CaseResult(
                    order_id="ORD-A",
                    decision=Decision.AUTO_REFUND_APPROVED,
                    refund_amount=25.0,
                    refund_id="RF-A",
                    policy_verdict="ELIGIBLE",
                )
            ],
        ),
        customer_response="Your refund will appear in your account within 30 days.",
    )

    issues = validate_result(result, state)
    assert any("UNSUPPORTED_TIMELINE_CLAIM" in issue for issue in issues), issues


def test_non_operational_policy_sentence_with_refund_word_is_allowed():
    response = "The refund policy permits eligible return requests within 45 days."
    assert validate_result(_rejected_result(response), _rejected_state()) == []
