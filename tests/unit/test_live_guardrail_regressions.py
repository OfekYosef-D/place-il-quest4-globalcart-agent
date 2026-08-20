"""Regression tests for customer-facing claim gaps found by live OpenRouter evals."""

from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState
from app.validator import validate_result


def _approved_state(order_id: str = "ORD-X900") -> AgentState:
    state = AgentState()
    state.cases[order_id] = CaseState(
        order_id=order_id,
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={
            "status": "APPROVED",
            "approved_amount": 42.0,
            "refund_id": "RF-X900-4200",
        },
    )
    return state


def _approved_result(response: str, order_id: str = "ORD-X900") -> AgentResult:
    return AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Trusted refund result is APPROVED."],
        action_taken=ActionTaken(
            tools_called=[],
            cases=[
                CaseResult(
                    order_id=order_id,
                    decision=Decision.AUTO_REFUND_APPROVED,
                    refund_amount=42.0,
                    refund_id="RF-X900-4200",
                    policy_verdict="ELIGIBLE",
                )
            ],
        ),
        customer_response=response,
    )


def _escalation_state(order_id: str = "ORD-X901") -> AgentState:
    state = AgentState()
    state.cases[order_id] = CaseState(
        order_id=order_id,
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "ESCALATION_REQUIRED", "reasons": ["AUTHORITY_LIMIT"]},
    )
    return state


def _escalation_result(response: str, order_id: str = "ORD-X901") -> AgentResult:
    return AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Trusted refund result requires escalation."],
        action_taken=ActionTaken(
            tools_called=[],
            cases=[
                CaseResult(
                    order_id=order_id,
                    decision=Decision.HUMAN_ESCALATION,
                    policy_verdict="ELIGIBLE",
                    escalation_reasons=["AUTHORITY_LIMIT"],
                )
            ],
        ),
        customer_response=response,
    )


def test_untrusted_explicit_refund_timeline_is_rejected():
    state = _approved_state()
    result = _approved_result(
        "Your refund was approved. Please allow 3-5 business days for the funds to appear in your account."
    )
    issues = validate_result(result, state)
    assert any("UNSUPPORTED_TIMELINE_CLAIM" in issue for issue in issues)


def test_untrusted_vague_processing_timeline_is_rejected():
    state = _approved_state()
    result = _approved_result(
        "Your refund was approved and should appear according to your payment method's standard processing time."
    )
    issues = validate_result(result, state)
    assert any("UNSUPPORTED_TIMELINE_CLAIM" in issue for issue in issues)


def test_passive_future_contact_promise_is_rejected():
    state = _escalation_state()
    result = _escalation_result(
        "Your case requires additional review. You will be contacted regarding the outcome."
    )
    issues = validate_result(result, state)
    assert any("UNSUPPORTED_FOLLOWUP_PROMISE" in issue for issue in issues)


def test_review_without_contact_promise_remains_allowed():
    state = _escalation_state()
    result = _escalation_result(
        "Your case requires additional review by our support team. No refund has been issued."
    )
    assert validate_result(result, state) == []


def test_policy_window_is_not_misclassified_as_operational_timeline():
    state = AgentState()
    state.cases["ORD-X902"] = CaseState(
        order_id="ORD-X902",
        policy_result={"eligible": False, "verdict": "OUTSIDE_RETURN_WINDOW"},
    )
    result = AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Trusted policy result is outside the return window."],
        action_taken=ActionTaken(
            tools_called=[],
            cases=[
                CaseResult(
                    order_id="ORD-X902",
                    decision=Decision.REJECTED,
                    policy_verdict="OUTSIDE_RETURN_WINDOW",
                )
            ],
        ),
        customer_response=(
            "Our return policy allows change-of-mind returns within 30 days of delivery, "
            "and this order is outside that window."
        ),
    )
    assert validate_result(result, state) == []
