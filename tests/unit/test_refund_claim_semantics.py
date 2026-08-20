"""Customer-response semantics around refund success and explicit denial."""

from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState
from app.validator import validate_result


def _escalation_state() -> AgentState:
    state = AgentState()
    state.cases["ORD-E"] = CaseState(
        order_id="ORD-E",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "ESCALATION_REQUIRED", "approved_amount": 0.0},
    )
    return state


def _escalation_result(response: str) -> AgentResult:
    return AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Trusted refund outcome is ESCALATION_REQUIRED."],
        action_taken=ActionTaken(
            tools_called=[],
            cases=[
                CaseResult(
                    order_id="ORD-E",
                    decision=Decision.HUMAN_ESCALATION,
                    policy_verdict="ELIGIBLE",
                )
            ],
        ),
        customer_response=response,
    )


def test_explicit_no_refund_issued_statement_is_not_success_claim():
    result = _escalation_result(
        "Your request requires additional review. No refund has been issued."
    )
    assert validate_result(result, _escalation_state()) == []


def test_affirmative_refund_issued_statement_is_still_rejected():
    result = _escalation_result(
        "Your request requires additional review. Your refund has been issued."
    )
    issues = validate_result(result, _escalation_state())
    assert any("without a trusted APPROVED" in issue for issue in issues), issues


def test_not_all_refunds_approved_is_not_blanket_success_claim():
    state = _escalation_state()
    result = _escalation_result("Not all refunds were approved; this one needs review.")
    assert validate_result(result, state) == []
