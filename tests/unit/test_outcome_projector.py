"""Invariant tests for deterministic projection of trusted business outcomes."""

from app.outcome_projector import project_case_result, project_result_from_state
from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState, ToolInteraction, ToolInteractionOutcome


def _model_result(*cases: CaseResult, status: FinalStatus = FinalStatus.COMPLETED) -> AgentResult:
    return AgentResult(
        status=status,
        reasoning_chain=["Model draft."],
        action_taken=ActionTaken(tools_called=["invented_tool"], cases=list(cases)),
        customer_response="Thank you for your patience.",
    )


def _touch(state: AgentState, order_id: str, *, tool_name: str = "process_refund") -> None:
    state.tool_history.append(
        ToolInteraction(
            step=1,
            tool_name=tool_name,
            arguments={"order_id": order_id},
            outcome=ToolInteractionOutcome.EXECUTED,
            result={"order_id": order_id},
        )
    )


def test_escalation_invariant_never_projects_refund_fields_for_varied_cases():
    """The rule is about ESCALATION_REQUIRED, not scenario ids or cap values."""
    samples = [
        ("ORD-X51", 51.0),
        ("ORD-X52", 52.0),
        ("ORD-X73", 73.0),
        ("ORD-X150", 150.0),
        ("ORD-X9000", 9000.0),
    ]
    for order_id, requested_amount in samples:
        case = CaseState(
            order_id=order_id,
            policy_result={"eligible": True, "verdict": "ELIGIBLE"},
            refund_result={
                "status": "ESCALATION_REQUIRED",
                "requested_amount": requested_amount,
                "approved_amount": 0.0,
                "reasons": ["AUTHORITY_LIMIT"],
            },
        )

        projected = project_case_result(case)

        assert projected is not None
        assert projected.decision is Decision.HUMAN_ESCALATION
        assert projected.refund_amount is None
        assert projected.refund_id is None
        assert projected.policy_verdict == "ELIGIBLE"
        assert projected.escalation_reasons == ["AUTHORITY_LIMIT"]


def test_approved_invariant_projects_only_trusted_amount_and_id():
    case = CaseState(
        order_id="ORD-ARBITRARY",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={
            "status": "APPROVED",
            "requested_amount": 123.45,
            "approved_amount": 123.45,
            "refund_id": "RF-TRUSTED",
        },
    )

    projected = project_case_result(case)

    assert projected is not None
    assert projected.decision is Decision.AUTO_REFUND_APPROVED
    assert projected.refund_amount == 123.45
    assert projected.refund_id == "RF-TRUSTED"


def test_rejected_refund_invariant_has_no_refund_fields():
    case = CaseState(
        order_id="ORD-R",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "REJECTED", "reasons": ["TOOL_REJECTED"]},
    )

    projected = project_case_result(case)

    assert projected is not None
    assert projected.decision is Decision.REJECTED
    assert projected.refund_amount is None
    assert projected.refund_id is None


def test_ineligible_policy_projects_rejected_without_needing_refund():
    case = CaseState(
        order_id="ORD-OLD",
        policy_result={"eligible": False, "verdict": "OUTSIDE_RETURN_WINDOW"},
    )

    projected = project_case_result(case)

    assert projected is not None
    assert projected.decision is Decision.REJECTED
    assert projected.policy_verdict == "OUTSIDE_RETURN_WINDOW"
    assert projected.refund_amount is None
    assert projected.refund_id is None


def test_terminal_order_error_projects_no_action():
    case = CaseState(order_id="ORD-NOT-REAL", terminal_error="ORDER_NOT_FOUND")

    projected = project_case_result(case)

    assert projected is not None
    assert projected.decision is Decision.NO_ACTION
    assert projected.error_code == "ORDER_NOT_FOUND"
    assert projected.refund_amount is None
    assert projected.refund_id is None


def test_unknown_refund_status_is_not_guessed():
    case = CaseState(order_id="ORD-UNKNOWN", refund_result={"status": "SOMETHING_NEW"})
    assert project_case_result(case) is None


def test_projection_overwrites_model_business_fields_with_trusted_escalation():
    state = AgentState()
    state.cases["ORD-X"] = CaseState(
        order_id="ORD-X",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "ESCALATION_REQUIRED", "approved_amount": 0.0},
    )
    _touch(state, "ORD-X")

    # Intentionally wrong model draft: exactly the class of live failure the
    # runtime must neutralize without knowing any scenario-specific values.
    draft = _model_result(
        CaseResult(
            order_id="ORD-X",
            decision=Decision.AUTO_REFUND_APPROVED,
            refund_amount=999.0,
            refund_id="RF-INVENTED",
        )
    )

    projected = project_result_from_state(draft, state)
    case = projected.action_taken.cases[0]

    assert projected.status is FinalStatus.COMPLETED
    assert case.decision is Decision.HUMAN_ESCALATION
    assert case.refund_amount is None
    assert case.refund_id is None
    assert projected.action_taken.tools_called == ["process_refund"]


def test_terminal_error_makes_resolved_turn_completed_even_if_model_requests_clarification():
    state = AgentState()
    state.cases["ORD-2222"] = CaseState(
        order_id="ORD-2222", terminal_error="ORDER_NOT_FOUND"
    )
    _touch(state, "ORD-2222", tool_name="get_order_details")

    draft = _model_result(
        CaseResult(order_id="ORD-2222", decision=Decision.NO_ACTION),
        status=FinalStatus.NEEDS_CLARIFICATION,
    )

    projected = project_result_from_state(draft, state)

    assert projected.status is FinalStatus.COMPLETED
    assert projected.action_taken.cases[0].decision is Decision.NO_ACTION
    assert projected.action_taken.cases[0].error_code == "ORDER_NOT_FOUND"


def test_unresolved_touched_case_does_not_force_completed_status():
    state = AgentState()
    state.cases["ORD-PENDING"] = CaseState(order_id="ORD-PENDING")
    _touch(state, "ORD-PENDING", tool_name="get_order_details")

    draft = _model_result(
        CaseResult(order_id="ORD-PENDING", decision=Decision.HUMAN_ESCALATION),
        status=FinalStatus.FAILED_SAFE,
    )

    projected = project_result_from_state(draft, state)
    assert projected.status is FinalStatus.FAILED_SAFE


def test_untouched_hallucinated_case_is_preserved_for_downstream_validation():
    state = AgentState()
    state.cases["ORD-REAL"] = CaseState(
        order_id="ORD-REAL",
        policy_result={"eligible": False, "verdict": "NON_RETURNABLE_CATEGORY"},
    )
    _touch(state, "ORD-REAL", tool_name="check_return_policy")

    draft = _model_result(
        CaseResult(order_id="ORD-REAL", decision=Decision.REJECTED),
        CaseResult(order_id="ORD-HALLUCINATED", decision=Decision.NO_ACTION),
    )

    projected = project_result_from_state(draft, state)
    ids = [case.order_id for case in projected.action_taken.cases]

    assert ids == ["ORD-REAL", "ORD-HALLUCINATED"]
