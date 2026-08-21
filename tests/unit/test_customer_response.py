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


def test_missing_order_clarification_is_runtime_rendered_in_hebrew():
    state = AgentState(messages=[CanonicalMessage(role="user", content="המוצר הגיע שבור")])
    result = AgentResult(
        status=FinalStatus.NEEDS_CLARIFICATION,
        reasoning_chain=["Order id is missing."],
        action_taken=ActionTaken(),
        customer_response="ההחזר אושר; נציג יחזור אליך מחר.",
    )
    projected = project_result_from_state(result, state)
    assert projected.customer_response == "מה מספר ההזמנה שברצונך שנבדוק?"
    assert "אושר" not in projected.customer_response
    assert "יחזור" not in projected.customer_response


def test_known_order_clarification_asks_only_for_return_reason():
    state = AgentState(messages=[CanonicalMessage(role="user", content="Please help with ORD-1001")])
    result = AgentResult(
        status=FinalStatus.NEEDS_CLARIFICATION,
        reasoning_chain=["Return reason is missing."],
        action_taken=ActionTaken(),
        customer_response="Your refund is already approved; tell us more later.",
    )
    projected = project_result_from_state(result, state)
    assert projected.customer_response == (
        "What is the reason for the refund or return request for order ORD-1001?"
    )
    assert "approved" not in projected.customer_response.lower()


def test_mixed_resolved_and_unresolved_clarification_renders_only_trusted_fact_plus_question():
    state = AgentState(
        messages=[
            CanonicalMessage(
                role="user",
                content="ORD-1001 arrived damaged. Also help me with ORD-1010.",
            )
        ]
    )
    state.cases["ORD-1001"] = CaseState(
        order_id="ORD-1001",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={
            "status": "APPROVED",
            "approved_amount": 35.0,
            "refund_id": "RF-1001-3500",
        },
    )
    state.cases["ORD-1010"] = CaseState(
        order_id="ORD-1010",
        verified_order={"order_id": "ORD-1010", "total_amount": 48.0},
    )
    state.tool_history.extend(
        [
            ToolInteraction(
                step=1,
                tool_name="process_refund",
                arguments={"order_id": "ORD-1001", "amount": 35.0},
                outcome=ToolInteractionOutcome.EXECUTED,
                result=state.cases["ORD-1001"].refund_result,
            ),
            ToolInteraction(
                step=2,
                tool_name="get_order_details",
                arguments={"order_id": "ORD-1010"},
                outcome=ToolInteractionOutcome.EXECUTED,
                result=state.cases["ORD-1010"].verified_order,
            ),
        ]
    )
    model_result = AgentResult(
        status=FinalStatus.NEEDS_CLARIFICATION,
        reasoning_chain=["ORD-1001 resolved; ORD-1010 needs a reason."],
        action_taken=ActionTaken(
            tools_called=["process_refund", "get_order_details"],
            cases=[
                CaseResult(
                    order_id="ORD-1001",
                    decision=Decision.AUTO_REFUND_APPROVED,
                    refund_amount=999.0,
                    refund_id="RF-INVENTED",
                ),
                CaseResult(
                    order_id="ORD-1010",
                    decision=Decision.HUMAN_ESCALATION,
                ),
            ],
        ),
        customer_response="Both refunds are approved and support will call tomorrow.",
    )

    projected = project_result_from_state(model_result, state)

    assert projected.status is FinalStatus.NEEDS_CLARIFICATION
    resolved, unresolved = projected.action_taken.cases
    assert resolved.decision is Decision.AUTO_REFUND_APPROVED
    assert resolved.refund_amount == 35.0
    assert resolved.refund_id == "RF-1001-3500"
    assert unresolved.decision is Decision.NO_ACTION
    assert unresolved.refund_amount is None
    assert unresolved.refund_id is None
    assert projected.customer_response == (
        "Order ORD-1001: a refund of $35.00 was approved (refund ID RF-1001-3500).\n"
        "What is the reason for the refund or return request for order ORD-1010?"
    )
    assert "call" not in projected.customer_response.lower()
