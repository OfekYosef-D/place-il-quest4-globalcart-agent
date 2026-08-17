"""Tests for the deterministic consistency validator.

The validator checks final output against trusted tool evidence only. It must
never recompute business rules, and FAILED_SAFE must never disable evidence
consistency checks.
"""

from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState, ToolInteraction, ToolInteractionOutcome
from app.validator import validate_result


def _result(
    cases, response="Thank you for your patience.", status=FinalStatus.COMPLETED, tools_called=None
):
    return AgentResult(
        status=status,
        reasoning_chain=["Checked policy"],
        action_taken=ActionTaken(tools_called=tools_called or [], cases=cases),
        customer_response=response,
    )


def _approved_state():
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(
        order_id="ORD-1001",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "APPROVED", "approved_amount": 35.0, "refund_id": "RF-1001-3500"},
    )
    return state


def test_consistent_approved_run_has_no_issues():
    state = _approved_state()
    result = _result(
        [
            CaseResult(
                order_id="ORD-1001",
                decision=Decision.AUTO_REFUND_APPROVED,
                refund_amount=35.0,
                refund_id="RF-1001-3500",
                policy_verdict="ELIGIBLE",
            )
        ],
        response="Your refund was approved.",
    )
    assert validate_result(result, state) == []


def test_approval_claim_without_trusted_approval_fails():
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(
        order_id="ORD-1001",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
    )
    result = _result(
        [CaseResult(order_id="ORD-1001", decision=Decision.NO_ACTION)],
        response="Your refund has been issued.",
    )
    issues = validate_result(result, state)
    assert any("without a trusted APPROVED" in issue for issue in issues)


def test_refund_amount_and_id_mismatch_fail():
    state = _approved_state()
    result = _result(
        [
            CaseResult(
                order_id="ORD-1001",
                decision=Decision.AUTO_REFUND_APPROVED,
                refund_amount=99.0,
                refund_id="RF-FAKE",
            )
        ]
    )
    issues = validate_result(result, state)
    assert any("refund_amount" in issue for issue in issues)
    assert any("refund_id" in issue for issue in issues)


def test_auto_refund_decision_without_approved_refund_fails():
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(order_id="ORD-1001")
    result = _result(
        [CaseResult(order_id="ORD-1001", decision=Decision.AUTO_REFUND_APPROVED)]
    )
    issues = validate_result(result, state)
    assert any("AUTO_REFUND_APPROVED without" in issue for issue in issues)


def test_refund_fields_without_approval_fail():
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(order_id="ORD-1001")
    result = _result(
        [
            CaseResult(
                order_id="ORD-1001",
                decision=Decision.REJECTED,
                refund_amount=35.0,
            )
        ]
    )
    issues = validate_result(result, state)
    assert any("refund fields" in issue for issue in issues)


def test_escalation_required_requires_human_escalation():
    state = AgentState()
    state.cases["ORD-1002"] = CaseState(
        order_id="ORD-1002",
        refund_result={"status": "ESCALATION_REQUIRED", "approved_amount": 0.0},
    )
    result = _result([CaseResult(order_id="ORD-1002", decision=Decision.REJECTED)])
    issues = validate_result(result, state)
    assert any("ESCALATION_REQUIRED" in issue for issue in issues)


def test_ineligible_policy_requires_rejection():
    state = AgentState()
    state.cases["ORD-1003"] = CaseState(
        order_id="ORD-1003",
        policy_result={"eligible": False, "verdict": "OUTSIDE_RETURN_WINDOW"},
    )
    result = _result([CaseResult(order_id="ORD-1003", decision=Decision.HUMAN_ESCALATION)])
    issues = validate_result(result, state)
    assert any("ineligible" in issue for issue in issues)

    consistent = _result([CaseResult(order_id="ORD-1003", decision=Decision.REJECTED)])
    assert validate_result(consistent, state) == []


def test_terminal_error_requires_no_action_without_verdict():
    state = AgentState()
    state.cases["ORD-2222"] = CaseState(order_id="ORD-2222", terminal_error="ORDER_NOT_FOUND")

    wrong_decision = _result([CaseResult(order_id="ORD-2222", decision=Decision.REJECTED)])
    issues = validate_result(wrong_decision, state)
    assert any("NO_ACTION" in issue for issue in issues)

    with_verdict = _result(
        [
            CaseResult(
                order_id="ORD-2222",
                decision=Decision.NO_ACTION,
                policy_verdict="ELIGIBLE",
            )
        ]
    )
    issues = validate_result(with_verdict, state)
    assert any("terminal error" in issue for issue in issues)

    consistent = _result([CaseResult(order_id="ORD-2222", decision=Decision.NO_ACTION)])
    assert validate_result(consistent, state) == []


def test_policy_verdict_must_match_trusted_verdict():
    state = AgentState()
    state.cases["ORD-1003"] = CaseState(
        order_id="ORD-1003",
        policy_result={"eligible": False, "verdict": "OUTSIDE_RETURN_WINDOW"},
    )
    result = _result(
        [
            CaseResult(
                order_id="ORD-1003",
                decision=Decision.REJECTED,
                policy_verdict="ELIGIBLE",
            )
        ]
    )
    issues = validate_result(result, state)
    assert any("policy_verdict" in issue for issue in issues)


def test_unknown_order_id_fails():
    state = AgentState()
    result = _result([CaseResult(order_id="ORD-9999", decision=Decision.NO_ACTION)])
    issues = validate_result(result, state)
    assert any("ORD-9999" in issue for issue in issues)


def test_internal_risk_disclosure_is_flagged():
    state = _approved_state()
    cases = [
        CaseResult(
            order_id="ORD-1001",
            decision=Decision.AUTO_REFUND_APPROVED,
            refund_amount=35.0,
            refund_id="RF-1001-3500",
        )
    ]
    for phrase in ("Your fraud score was high.", "Due to your LTV we approved.",
                   "prior fraud flags were found", "risk score exceeded threshold"):
        issues = validate_result(_result(cases, response=phrase), state)
        assert any("internal risk" in issue for issue in issues), phrase


def test_failed_safe_still_enforces_evidence_consistency():
    """FAILED_SAFE may relax completeness, never evidence consistency."""
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(order_id="ORD-1001")
    result = _result(
        [CaseResult(order_id="ORD-1001", decision=Decision.AUTO_REFUND_APPROVED)],
        response="Your refund was issued.",
        status=FinalStatus.FAILED_SAFE,
    )
    issues = validate_result(result, state)
    assert issues, "FAILED_SAFE must not disable consistency checks"


def test_failed_safe_preserves_resolved_case_and_escapes_unresolved():
    """Multi-order failure: resolved outcomes survive, unresolved cases fail safe."""
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(
        order_id="ORD-1001",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "APPROVED", "approved_amount": 35.0, "refund_id": "RF-1001-3500"},
    )
    # ORD-1005 was mid-flight when the run failed safely: no decision evidence.
    state.cases["ORD-1005"] = CaseState(order_id="ORD-1005")
    result = _result(
        [
            CaseResult(
                order_id="ORD-1001",
                decision=Decision.AUTO_REFUND_APPROVED,
                refund_amount=35.0,
                refund_id="RF-1001-3500",
                policy_verdict="ELIGIBLE",
            ),
            CaseResult(order_id="ORD-1005", decision=Decision.HUMAN_ESCALATION),
        ],
        response="ORD-1001 refund was approved. ORD-1005 needs additional review.",
        status=FinalStatus.FAILED_SAFE,
    )
    assert validate_result(result, state) == []


def test_failed_safe_contradicting_escalation_evidence_fails():
    state = AgentState()
    state.cases["ORD-1002"] = CaseState(
        order_id="ORD-1002",
        refund_result={"status": "ESCALATION_REQUIRED", "approved_amount": 0.0},
    )
    result = _result(
        [CaseResult(order_id="ORD-1002", decision=Decision.AUTO_REFUND_APPROVED)],
        response="Your refund has been completed.",
        status=FinalStatus.FAILED_SAFE,
    )
    issues = validate_result(result, state)
    assert issues, "FAILED_SAFE must never permit contradicting trusted evidence"


# ----------------------------------------------------------------------
# Multi-order completeness (fix 3)
# ----------------------------------------------------------------------


def test_resolved_case_missing_from_output_fails():
    # Completeness is driven by the turn's factual interactions (here: the
    # whole history, i.e. a single turn with turn_start_history=0).
    state = _history_state()
    issues = validate_result(_result([]), state)
    assert any("missing from" in issue for issue in issues)


def test_failed_safe_may_not_drop_resolved_cases():
    state = _history_state()
    issues = validate_result(_result([], status=FinalStatus.FAILED_SAFE), state)
    assert any("missing from" in issue for issue in issues), (
        "FAILED_SAFE may omit genuinely unresolved cases only, never resolved ones"
    )


def test_unresolved_case_may_be_absent_from_failed_safe_output():
    state = AgentState()
    state.cases["ORD-1005"] = CaseState(order_id="ORD-1005")
    assert validate_result(_result([], status=FinalStatus.FAILED_SAFE), state) == []


def test_duplicate_case_in_output_fails():
    state = _approved_state()
    case = CaseResult(
        order_id="ORD-1001",
        decision=Decision.AUTO_REFUND_APPROVED,
        refund_amount=35.0,
        refund_id="RF-1001-3500",
    )
    issues = validate_result(_result([case, case]), state)
    assert any("more than once" in issue for issue in issues)


# ----------------------------------------------------------------------
# Deterministic outcome/action consistency (fix 4)
# ----------------------------------------------------------------------


def test_rejected_process_refund_requires_rejected_decision():
    state = AgentState()
    state.cases["ORD-1004"] = CaseState(
        order_id="ORD-1004",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "REJECTED", "reasons": ["REFUND_CAP_EXCEEDED"]},
    )
    wrong = _result([CaseResult(order_id="ORD-1004", decision=Decision.HUMAN_ESCALATION)])
    issues = validate_result(wrong, state)
    assert any("status is REJECTED" in issue for issue in issues)

    consistent = _result([CaseResult(order_id="ORD-1004", decision=Decision.REJECTED)])
    assert validate_result(consistent, state) == []


def _history_state():
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(
        order_id="ORD-1001",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "APPROVED", "approved_amount": 35.0, "refund_id": "RF-1001-3500"},
    )
    state.tool_history.extend(
        [
            ToolInteraction(
                step=1,
                tool_name="get_order_details",
                arguments={"order_id": "ORD-1001"},
                outcome=ToolInteractionOutcome.EXECUTED,
                result={"order_id": "ORD-1001"},
            ),
            ToolInteraction(
                step=2,
                tool_name="check_return_policy",
                arguments={"order_id": "ORD-1001"},
                outcome=ToolInteractionOutcome.CACHED,
                result={"eligible": True},
            ),
            ToolInteraction(
                step=3,
                tool_name="process_refund",
                arguments={"order_id": "ORD-1001", "amount": 35.0},
                outcome=ToolInteractionOutcome.BLOCKED,
                reason_code="MISSING_ELIGIBLE_POLICY_PRECONDITION",
            ),
            ToolInteraction(
                step=4,
                tool_name="process_refund",
                arguments={"order_id": "ORD-1001", "amount": 35.0},
                outcome=ToolInteractionOutcome.EXECUTED,
                result={"status": "APPROVED"},
            ),
        ]
    )
    return state


def _approved_case_result():
    return CaseResult(
        order_id="ORD-1001",
        decision=Decision.AUTO_REFUND_APPROVED,
        refund_amount=35.0,
        refund_id="RF-1001-3500",
    )


def test_tools_called_must_match_executed_tools_exactly():
    """Factual executed/cache-served set; blocked calls never count; no
    exact workflow or call order is required."""
    state = _history_state()
    factual = ["check_return_policy", "get_order_details", "process_refund"]
    base_cases = [_approved_case_result()]

    assert validate_result(_result(base_cases, tools_called=factual), state) == []

    invented = _result(base_cases, tools_called=factual + ["get_user_profile"])
    assert any("invents" in issue for issue in validate_result(invented, state))

    omitted = _result(base_cases, tools_called=["get_order_details"])
    assert any("omits" in issue for issue in validate_result(omitted, state))

    duplicated = _result(base_cases, tools_called=factual + ["process_refund"])
    assert any("duplicates" in issue for issue in validate_result(duplicated, state))


# ----------------------------------------------------------------------
# Case-aware customer-response refund claims (fix 5)
# ----------------------------------------------------------------------


def _mixed_outcome_state():
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(
        order_id="ORD-1001",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "APPROVED", "approved_amount": 35.0, "refund_id": "RF-1001-3500"},
    )
    state.cases["ORD-1003"] = CaseState(
        order_id="ORD-1003",
        policy_result={"eligible": False, "verdict": "OUTSIDE_RETURN_WINDOW"},
    )
    return state


def _mixed_cases():
    return [
        CaseResult(
            order_id="ORD-1001",
            decision=Decision.AUTO_REFUND_APPROVED,
            refund_amount=35.0,
            refund_id="RF-1001-3500",
        ),
        CaseResult(
            order_id="ORD-1003",
            decision=Decision.REJECTED,
            policy_verdict="OUTSIDE_RETURN_WINDOW",
        ),
    ]


def test_success_claim_tied_to_unapproved_order_fails():
    state = _mixed_outcome_state()
    result = _result(_mixed_cases(), response="Your refund was approved for order ORD-1003.")
    issues = validate_result(result, state)
    assert any("ORD-1003" in issue for issue in issues)

    grounded = _result(_mixed_cases(), response="Your refund was approved for order ORD-1001.")
    assert validate_result(grounded, state) == []


def test_blanket_claim_with_mixed_outcomes_fails():
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(
        order_id="ORD-1001",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "APPROVED", "approved_amount": 35.0, "refund_id": "RF-1001-3500"},
    )
    state.cases["ORD-1004"] = CaseState(
        order_id="ORD-1004",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "REJECTED", "reasons": ["REFUND_CAP_EXCEEDED"]},
    )
    cases = [
        CaseResult(
            order_id="ORD-1001",
            decision=Decision.AUTO_REFUND_APPROVED,
            refund_amount=35.0,
            refund_id="RF-1001-3500",
        ),
        CaseResult(order_id="ORD-1004", decision=Decision.REJECTED),
    ]
    result = _result(cases, response="Both refunds were approved.")
    issues = validate_result(result, state)
    assert any("blanket" in issue for issue in issues)


def test_blanket_claim_allowed_when_every_refund_is_approved():
    state = AgentState()
    refunds = (("ORD-1001", "RF-1001-3500"), ("ORD-1006", "RF-1006-2000"))
    for order_id, refund_id in refunds:
        state.cases[order_id] = CaseState(
            order_id=order_id,
            policy_result={"eligible": True, "verdict": "ELIGIBLE"},
            refund_result={"status": "APPROVED", "approved_amount": 20.0, "refund_id": refund_id},
        )
    cases = [
        CaseResult(
            order_id=order_id,
            decision=Decision.AUTO_REFUND_APPROVED,
            refund_amount=20.0,
            refund_id=refund_id,
        )
        for order_id, refund_id in refunds
    ]
    result = _result(cases, response="Both refunds were approved.")
    assert validate_result(result, state) == []


def test_blanket_claim_fails_with_policy_rejected_case():
    """Pre-Milestone-3 fix 2: ORD-1001 APPROVED plus ORD-1003 trusted
    eligible=false (process_refund never executed) makes "all refunds were
    approved" a false blanket claim."""
    state = _mixed_outcome_state()
    result = _result(_mixed_cases(), response="All refunds were approved.")
    issues = validate_result(result, state)
    assert any("blanket" in issue for issue in issues)


# ----------------------------------------------------------------------
# Turn-scoped action validation (pre-Milestone-3 fix 1)
# ----------------------------------------------------------------------


def _two_turn_state():
    """Session state after turn 1 resolved ORD-1001 and turn 2 resolved ORD-1010.

    Returns (state, turn_start_history) where turn_start_history indexes the
    first interaction of turn 2 in the cumulative tool_history.
    """
    state = AgentState()
    state.cases["ORD-1001"] = CaseState(
        order_id="ORD-1001",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "APPROVED", "approved_amount": 35.0, "refund_id": "RF-1001-3500"},
        decision=Decision.AUTO_REFUND_APPROVED,
    )
    state.cases["ORD-1010"] = CaseState(
        order_id="ORD-1010",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "APPROVED", "approved_amount": 48.0, "refund_id": "RF-1010-4800"},
    )
    turn1 = [
        ToolInteraction(
            step=1,
            tool_name="get_order_details",
            arguments={"order_id": "ORD-1001"},
            outcome=ToolInteractionOutcome.EXECUTED,
            result={"order_id": "ORD-1001"},
        ),
        ToolInteraction(
            step=2,
            tool_name="get_user_profile",
            arguments={"user_id": "USR-101"},
            outcome=ToolInteractionOutcome.EXECUTED,
            result={"user_id": "USR-101"},
        ),
        ToolInteraction(
            step=3,
            tool_name="check_return_policy",
            arguments={"order_id": "ORD-1001"},
            outcome=ToolInteractionOutcome.EXECUTED,
            result={"eligible": True, "verdict": "ELIGIBLE"},
        ),
        ToolInteraction(
            step=4,
            tool_name="process_refund",
            arguments={"order_id": "ORD-1001", "amount": 35.0},
            outcome=ToolInteractionOutcome.EXECUTED,
            result={"status": "APPROVED"},
        ),
    ]
    turn2 = [
        ToolInteraction(
            step=1,
            tool_name="get_order_details",
            arguments={"order_id": "ORD-1010"},
            outcome=ToolInteractionOutcome.EXECUTED,
            result={"order_id": "ORD-1010"},
        ),
        ToolInteraction(
            step=2,
            tool_name="check_return_policy",
            arguments={"order_id": "ORD-1010"},
            outcome=ToolInteractionOutcome.CACHED,
            result={"eligible": True, "verdict": "ELIGIBLE"},
        ),
        ToolInteraction(
            step=3,
            tool_name="process_refund",
            arguments={"order_id": "ORD-1010", "amount": 48.0},
            outcome=ToolInteractionOutcome.EXECUTED,
            result={"status": "APPROVED"},
        ),
    ]
    state.tool_history.extend(turn1 + turn2)
    return state, len(turn1)


def _turn2_result():
    return _result(
        [
            CaseResult(
                order_id="ORD-1010",
                decision=Decision.AUTO_REFUND_APPROVED,
                refund_amount=48.0,
                refund_id="RF-1010-4800",
                policy_verdict="ELIGIBLE",
            )
        ],
        response="Your refund was approved for order ORD-1010.",
        tools_called=["get_order_details", "check_return_policy", "process_refund"],
    )


def test_turn_scope_validates_only_current_turn_tools_and_cases():
    """action_taken describes the current turn; prior trusted state stays
    available in session memory but is not re-demanded."""
    state, turn_start = _two_turn_state()

    assert validate_result(_turn2_result(), state, turn_start_history=turn_start) == []

    # The same output fails whole-session validation: session scope would
    # wrongly demand the prior turn's resolved case and its turn-1-only tool.
    session_issues = validate_result(_turn2_result(), state)
    assert any("ORD-1001" in issue and "missing from" in issue for issue in session_issues)
    assert any("get_user_profile" in issue and "omits" in issue for issue in session_issues)


def test_current_turn_resolved_case_cannot_be_silently_omitted():
    state, turn_start = _two_turn_state()
    incomplete = _result(
        [],
        response="Thank you for your patience.",
        tools_called=["get_order_details", "check_return_policy", "process_refund"],
    )
    issues = validate_result(incomplete, state, turn_start_history=turn_start)
    assert any("ORD-1010" in issue and "missing from" in issue for issue in issues)
    assert not any("ORD-1001" in issue for issue in issues), (
        "prior-turn cases must not be re-demanded by the current turn"
    )
