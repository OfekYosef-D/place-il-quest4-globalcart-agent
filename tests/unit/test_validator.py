"""Tests for the deterministic consistency validator.

The validator checks final output against trusted tool evidence only. It must
never recompute business rules, and FAILED_SAFE must never disable evidence
consistency checks.
"""

from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState
from app.validator import validate_result


def _result(cases, response="Thank you for your patience.", status=FinalStatus.COMPLETED):
    return AgentResult(
        status=status,
        reasoning_chain=["Checked policy"],
        action_taken=ActionTaken(tools_called=[], cases=cases),
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
