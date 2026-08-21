"""Evidence-consistency tests for the deterministic validator."""

from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState, ToolInteraction, ToolInteractionOutcome
from app.validator import validate_result


def _result(cases, *, tools=None, status=FinalStatus.COMPLETED, response="customer text"):
    return AgentResult(
        status=status,
        reasoning_chain=["Checked trusted evidence."],
        action_taken=ActionTaken(tools_called=tools or [], cases=cases),
        customer_response=response,
    )


def _approved_state() -> AgentState:
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(
        order_id="ORD-1001",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={
            "status": "APPROVED",
            "approved_amount": 35.0,
            "refund_id": "RF-1001-3500",
        },
    )
    return state


def _approved_case() -> CaseResult:
    return CaseResult(
        order_id="ORD-1001",
        decision=Decision.AUTO_REFUND_APPROVED,
        refund_amount=35.0,
        refund_id="RF-1001-3500",
        policy_verdict="ELIGIBLE",
    )


def test_consistent_approved_case_has_no_issues():
    assert validate_result(_result([_approved_case()]), _approved_state()) == []


def test_refund_amount_and_id_must_match_trusted_approval():
    state = _approved_state()
    result = _result([
        CaseResult(
            order_id="ORD-1001",
            decision=Decision.AUTO_REFUND_APPROVED,
            refund_amount=99.0,
            refund_id="RF-FAKE",
            policy_verdict="ELIGIBLE",
        )
    ])
    issues = validate_result(result, state)
    assert any("refund_amount" in issue for issue in issues)
    assert any("refund_id" in issue for issue in issues)


def test_auto_refund_requires_trusted_approved_result():
    state = AgentState(cases={"ORD-1001": CaseState(order_id="ORD-1001")})
    issues = validate_result(
        _result([CaseResult(order_id="ORD-1001", decision=Decision.AUTO_REFUND_APPROVED)]),
        state,
    )
    assert any("AUTO_REFUND_APPROVED without" in issue for issue in issues)


def test_escalation_required_maps_to_human_escalation():
    state = AgentState()
    state.cases["ORD-1002"] = CaseState(
        order_id="ORD-1002",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "ESCALATION_REQUIRED", "approved_amount": 0.0},
    )
    issues = validate_result(
        _result([CaseResult(order_id="ORD-1002", decision=Decision.REJECTED)]),
        state,
    )
    assert any("ESCALATION_REQUIRED" in issue for issue in issues)


def test_ineligible_policy_maps_to_rejected():
    state = AgentState()
    state.cases["ORD-1003"] = CaseState(
        order_id="ORD-1003",
        policy_result={"eligible": False, "verdict": "OUTSIDE_RETURN_WINDOW"},
    )
    consistent = _result([
        CaseResult(
            order_id="ORD-1003",
            decision=Decision.REJECTED,
            policy_verdict="OUTSIDE_RETURN_WINDOW",
        )
    ])
    assert validate_result(consistent, state) == []


def test_terminal_error_requires_no_action_and_matching_error_code():
    state = AgentState()
    state.cases["ORD-2222"] = CaseState(
        order_id="ORD-2222", terminal_error="ORDER_NOT_FOUND"
    )
    wrong = _result([CaseResult(order_id="ORD-2222", decision=Decision.REJECTED)])
    issues = validate_result(wrong, state)
    assert any("NO_ACTION" in issue for issue in issues)
    assert any("error_code" in issue for issue in issues)

    correct = _result([
        CaseResult(
            order_id="ORD-2222",
            decision=Decision.NO_ACTION,
            error_code="ORDER_NOT_FOUND",
        )
    ])
    assert validate_result(correct, state) == []


def test_unknown_order_is_rejected():
    issues = validate_result(
        _result([CaseResult(order_id="ORD-UNKNOWN", decision=Decision.NO_ACTION)]),
        AgentState(),
    )
    assert any("never grounded" in issue for issue in issues)


def test_completed_cannot_contain_unresolved_case():
    state = AgentState(cases={"ORD-X": CaseState(order_id="ORD-X")})
    issues = validate_result(
        _result([CaseResult(order_id="ORD-X", decision=Decision.NO_ACTION)]),
        state,
    )
    assert any("unresolved" in issue for issue in issues)


def test_unresolved_clarification_case_must_be_no_action_without_terminal_fields():
    state = AgentState(
        cases={
            "ORD-X": CaseState(
                order_id="ORD-X",
                verified_order={"order_id": "ORD-X", "total_amount": 20.0},
            )
        }
    )
    wrong = _result(
        [
            CaseResult(
                order_id="ORD-X",
                decision=Decision.HUMAN_ESCALATION,
                policy_verdict="ELIGIBLE",
                escalation_reasons=["INVENTED"],
            )
        ],
        status=FinalStatus.NEEDS_CLARIFICATION,
    )
    issues = validate_result(wrong, state)
    assert any("must use NO_ACTION" in issue for issue in issues)
    assert any("cannot report terminal" in issue for issue in issues)

    correct = _result(
        [CaseResult(order_id="ORD-X", decision=Decision.NO_ACTION)],
        status=FinalStatus.NEEDS_CLARIFICATION,
    )
    assert validate_result(correct, state) == []


def test_resolved_touched_case_may_not_disappear():
    state = _approved_state()
    state.tool_history.append(
        ToolInteraction(
            step=1,
            tool_name="process_refund",
            arguments={"order_id": "ORD-1001", "amount": 35.0},
            outcome=ToolInteractionOutcome.EXECUTED,
            result={"status": "APPROVED"},
        )
    )
    issues = validate_result(_result([]), state)
    assert any("missing from" in issue for issue in issues)


def test_tools_called_matches_factual_tool_set_only():
    state = _approved_state()
    state.tool_history.extend([
        ToolInteraction(
            step=1,
            tool_name="get_order_details",
            arguments={"order_id": "ORD-1001"},
            outcome=ToolInteractionOutcome.EXECUTED,
            result={"order_id": "ORD-1001"},
        ),
        ToolInteraction(
            step=2,
            tool_name="process_refund",
            arguments={"order_id": "ORD-1001", "amount": 35.0},
            outcome=ToolInteractionOutcome.EXECUTED,
            result={"status": "APPROVED"},
        ),
    ])
    assert validate_result(
        _result([_approved_case()], tools=["get_order_details", "process_refund"]),
        state,
    ) == []

    issues = validate_result(
        _result([_approved_case()], tools=["get_order_details", "get_user_profile"]),
        state,
    )
    assert any("invents" in issue for issue in issues)
    assert any("omits" in issue for issue in issues)


def test_validator_does_not_depend_on_natural_language_phrase_lists():
    state = _approved_state()
    # Natural-language business claims are not interpreted here. In production
    # customer text is overwritten by deterministic rendering before validation.
    result = _result(
        [_approved_case()],
        response="נציג יחזור אליך מחר; refund will settle in 3 days.",
    )
    assert validate_result(result, state) == []
