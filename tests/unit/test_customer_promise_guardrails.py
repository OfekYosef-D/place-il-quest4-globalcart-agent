"""Regression and invariant tests for customer-facing future commitments."""

from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState
from app.validator import validate_result


def _escalation_state(order_id: str = "ORD-X900") -> AgentState:
    state = AgentState()
    state.cases[order_id] = CaseState(
        order_id=order_id,
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={
            "status": "ESCALATION_REQUIRED",
            "approved_amount": 0.0,
            "reasons": ["ADDITIONAL_REVIEW_REQUIRED"],
        },
    )
    return state


def _escalation_result(response: str, order_id: str = "ORD-X900") -> AgentResult:
    return AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Trusted refund result requires additional review."],
        action_taken=ActionTaken(
            tools_called=[],
            cases=[
                CaseResult(
                    order_id=order_id,
                    decision=Decision.HUMAN_ESCALATION,
                    policy_verdict="ELIGIBLE",
                    escalation_reasons=["ADDITIONAL_REVIEW_REQUIRED"],
                )
            ],
        ),
        customer_response=response,
    )


def test_escalation_may_state_review_without_promising_future_contact():
    state = _escalation_state()
    result = _escalation_result(
        "Your request requires additional review by our support team. "
        "No refund has been issued."
    )
    assert validate_result(result, state) == []


def test_future_contact_promise_is_rejected_for_any_escalation_case():
    for wording in (
        "A representative will contact you shortly.",
        "Our support team will reach out to you.",
        "A human agent will follow up with you.",
    ):
        issues = validate_result(_escalation_result(wording), _escalation_state())
        assert any("UNSUPPORTED_FOLLOWUP_PROMISE" in issue for issue in issues), wording


def test_future_refund_completion_is_rejected_without_approved_evidence():
    for wording in (
        "A representative will contact you to complete the refund process.",
        "The team will process your refund after manual review.",
        "Your refund will be issued after the review.",
    ):
        issues = validate_result(_escalation_result(wording), _escalation_state())
        assert any("UNAPPROVED_FUTURE_REFUND_PROMISE" in issue for issue in issues), wording


def test_rule_is_not_tied_to_known_eval_order_ids():
    order_id = "ORD-UNSEEN-73"
    state = _escalation_state(order_id)
    result = _escalation_result(
        "Your case has been escalated for manual review. No refund has been issued.",
        order_id,
    )
    assert validate_result(result, state) == []
