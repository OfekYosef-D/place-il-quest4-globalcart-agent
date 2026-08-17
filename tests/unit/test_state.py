"""Tests for runtime state models."""

from app.schemas import Decision
from app.state import AgentState, CaseState, RuntimeStatus


def test_agent_state_defaults():
    state = AgentState()
    assert state.messages == []
    assert state.cases == {}
    assert state.tool_history == []
    assert state.step_count == 0
    assert state.status is RuntimeStatus.RUNNING
    assert state.final_result is None
    assert state.failure_reason is None


def test_case_state_stores_results_not_policy_logic():
    case = CaseState(order_id="ORD-1001")
    assert case.reason is None
    assert case.verified_order is None
    assert case.verified_user is None
    assert case.policy_result is None
    assert case.refund_result is None
    assert case.decision is None

    case.policy_result = {"eligible": True, "verdict": "ELIGIBLE"}
    case.decision = Decision.AUTO_REFUND_APPROVED
    assert case.policy_result["eligible"] is True


def test_runtime_status_covers_required_values():
    assert {s.value for s in RuntimeStatus} == {
        "RUNNING",
        "NEEDS_CLARIFICATION",
        "COMPLETED",
        "FAILED_SAFE",
    }


def test_state_instances_do_not_share_mutable_defaults():
    first, second = AgentState(), AgentState()
    first.cases["ORD-1001"] = CaseState(order_id="ORD-1001")
    assert second.cases == {}
