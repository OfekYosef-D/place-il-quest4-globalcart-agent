from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState
from app.validator import validate_result


def _result(cases, response):
    return AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Checked trusted evidence"],
        action_taken=ActionTaken(tools_called=[], cases=cases),
        customer_response=response,
    )


def _approved_case(order_id="ORD-1001", refund_id="RF-1001-3500"):
    return CaseState(
        order_id=order_id,
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "APPROVED", "approved_amount": 35.0, "refund_id": refund_id},
    )


def test_historical_approval_does_not_authorize_generic_success_claim_for_current_rejection():
    state = AgentState()
    state.cases["ORD-1001"] = _approved_case()
    state.cases["ORD-1003"] = CaseState(
        order_id="ORD-1003",
        policy_result={"eligible": False, "verdict": "OUTSIDE_RETURN_WINDOW"},
    )

    result = _result(
        [
            CaseResult(
                order_id="ORD-1003",
                decision=Decision.REJECTED,
                policy_verdict="OUTSIDE_RETURN_WINDOW",
            )
        ],
        "Your refund was approved.",
    )

    issues = validate_result(result, state)
    assert any("current AgentResult" in issue for issue in issues)


def test_explicitly_reported_prior_approved_order_may_use_its_trusted_evidence():
    state = AgentState()
    state.cases["ORD-1001"] = _approved_case()
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
        "Your refund was approved for order ORD-1001.",
    )
    assert validate_result(result, state) == []


def test_blanket_claim_fails_for_approved_plus_policy_rejected():
    state = AgentState()
    state.cases["ORD-1001"] = _approved_case()
    state.cases["ORD-1003"] = CaseState(
        order_id="ORD-1003",
        policy_result={"eligible": False, "verdict": "OUTSIDE_RETURN_WINDOW"},
    )
    cases = [
        CaseResult(
            order_id="ORD-1001",
            decision=Decision.AUTO_REFUND_APPROVED,
            refund_amount=35.0,
            refund_id="RF-1001-3500",
        ),
        CaseResult(order_id="ORD-1003", decision=Decision.REJECTED),
    ]
    issues = validate_result(_result(cases, "All refunds were approved."), state)
    assert any("blanket refund claim" in issue for issue in issues)


def test_blanket_claim_fails_for_approved_plus_no_action():
    state = AgentState()
    state.cases["ORD-1001"] = _approved_case()
    state.cases["ORD-2222"] = CaseState(
        order_id="ORD-2222", terminal_error="ORDER_NOT_FOUND"
    )
    cases = [
        CaseResult(
            order_id="ORD-1001",
            decision=Decision.AUTO_REFUND_APPROVED,
            refund_amount=35.0,
            refund_id="RF-1001-3500",
        ),
        CaseResult(order_id="ORD-2222", decision=Decision.NO_ACTION),
    ]
    issues = validate_result(_result(cases, "All refunds were approved."), state)
    assert any("blanket refund claim" in issue for issue in issues)


def test_blanket_claim_fails_for_approved_plus_human_escalation():
    state = AgentState()
    state.cases["ORD-1001"] = _approved_case()
    state.cases["ORD-1002"] = CaseState(
        order_id="ORD-1002",
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "ESCALATION_REQUIRED", "approved_amount": 0.0},
    )
    cases = [
        CaseResult(
            order_id="ORD-1001",
            decision=Decision.AUTO_REFUND_APPROVED,
            refund_amount=35.0,
            refund_id="RF-1001-3500",
        ),
        CaseResult(order_id="ORD-1002", decision=Decision.HUMAN_ESCALATION),
    ]
    issues = validate_result(_result(cases, "Both refunds were approved."), state)
    assert any("blanket refund claim" in issue for issue in issues)


def test_blanket_claim_passes_for_two_trusted_approved_cases():
    state = AgentState()
    state.cases["ORD-1001"] = _approved_case("ORD-1001", "RF-1001-3500")
    state.cases["ORD-1010"] = _approved_case("ORD-1010", "RF-1010-4800")
    cases = [
        CaseResult(
            order_id="ORD-1001",
            decision=Decision.AUTO_REFUND_APPROVED,
            refund_amount=35.0,
            refund_id="RF-1001-3500",
        ),
        CaseResult(
            order_id="ORD-1010",
            decision=Decision.AUTO_REFUND_APPROVED,
            refund_amount=35.0,
            refund_id="RF-1010-4800",
        ),
    ]
    assert validate_result(_result(cases, "Both refunds were approved."), state) == []
