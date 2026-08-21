import json

from app.customer_response import render_terminal_customer_response
from app.messages import CanonicalMessage
from app.outcome_projector import project_result_from_state
from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState, ToolInteraction, ToolInteractionOutcome


def _state(message: str, refund_result: dict) -> AgentState:
    state = AgentState()
    state.messages.append(CanonicalMessage(role="user", content=message))
    state.cases["ORD-1002"] = CaseState(
        order_id="ORD-1002",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result=refund_result,
    )
    state.tool_history.append(
        ToolInteraction(
            step=1,
            tool_name="process_refund",
            arguments={"order_id": "ORD-1002", "amount": 150.0},
            outcome=ToolInteractionOutcome.EXECUTED,
            result=refund_result,
        )
    )
    return state


def _model_result(customer_response: str) -> AgentResult:
    return AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Trusted refund result requires review."],
        action_taken=ActionTaken(
            tools_called=["process_refund"],
            cases=[
                CaseResult(
                    order_id="ORD-1002",
                    decision=Decision.HUMAN_ESCALATION,
                    policy_verdict="ELIGIBLE",
                )
            ],
        ),
        customer_response=customer_response,
    )


def test_terminal_response_overwrites_unsupported_english_model_wording():
    state = _state(
        "Order ORD-1002 arrived damaged. I want a full refund.",
        {"status": "ESCALATION_REQUIRED", "approved_amount": 0.0},
    )
    projected = project_result_from_state(
        _model_result("A representative will contact you tomorrow and process your refund."),
        state,
    )
    assert projected.customer_response == (
        "Order ORD-1002: additional review is required. No refund has been issued."
    )


def test_terminal_response_is_rendered_in_hebrew_without_phrase_matching():
    state = _state(
        "הזמנה ORD-1002 הגיעה פגומה. אני רוצה החזר מלא.",
        {"status": "ESCALATION_REQUIRED", "approved_amount": 0.0},
    )
    projected = project_result_from_state(
        _model_result("נציג יחזור אליך בהקדם וההחזר יטופל."),
        state,
    )
    assert projected.customer_response == (
        "הזמנה ORD-1002: נדרשת בדיקה נוספת. לא בוצע החזר כספי."
    )
    assert "יחזור" not in projected.customer_response
    assert "בהקדם" not in projected.customer_response


def test_approved_response_uses_only_trusted_amount_and_refund_id():
    state = _state(
        "Please refund ORD-1002.",
        {"status": "APPROVED", "approved_amount": 35.0, "refund_id": "RF-OK"},
    )
    result = _model_result("The refund will arrive in 3 days.")
    projected = project_result_from_state(result, state)
    assert projected.action_taken.cases[0].decision is Decision.AUTO_REFUND_APPROVED
    assert projected.customer_response == (
        "Order ORD-1002: a refund of $35.00 was approved (refund ID RF-OK)."
    )


def test_renderer_returns_none_for_clarification_without_terminal_cases():
    state = AgentState(messages=[CanonicalMessage(role="user", content="המוצר הגיע שבור")])
    result = AgentResult(
        status=FinalStatus.NEEDS_CLARIFICATION,
        reasoning_chain=["Order id is missing."],
        action_taken=ActionTaken(),
        customer_response="מה מספר ההזמנה?",
    )
    assert render_terminal_customer_response(result, state) is None
