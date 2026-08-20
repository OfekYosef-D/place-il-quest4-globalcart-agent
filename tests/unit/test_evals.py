"""Unit coverage for Milestone 3 eval scoring/reporting without network calls."""

import pytest

from app.agent import AgentRun
from app.evals.models import CandidateModel
from app.evals.reporting import build_report, summarize_candidate
from app.evals.scenarios import AGENT_SCENARIOS, TOOL_PROBES
from app.evals.scoring import EvalClassification, score_agent_run, score_tool_probe
from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState, ToolInteraction, ToolInteractionOutcome
from app.tracing import RunSummary
from run_evals import _missing_api_keys, _validate_candidates
from app.config import Settings


def _approved_run(order_id="ORD-1001"):
    state = AgentState(step_count=3)
    state.cases[order_id] = CaseState(
        order_id=order_id,
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "APPROVED", "approved_amount": 35.0, "refund_id": "RF-1"},
    )
    state.tool_history.extend([
        ToolInteraction(step=1, tool_name="get_order_details", arguments={"order_id": order_id}, outcome=ToolInteractionOutcome.EXECUTED, result={"order_id": order_id}),
        ToolInteraction(step=2, tool_name="check_return_policy", arguments={"order_id": order_id, "reason": "damaged_on_arrival"}, outcome=ToolInteractionOutcome.EXECUTED, result={"eligible": True, "verdict": "ELIGIBLE"}),
        ToolInteraction(step=3, tool_name="process_refund", arguments={"order_id": order_id, "amount": 35.0}, outcome=ToolInteractionOutcome.EXECUTED, result={"status": "APPROVED", "approved_amount": 35.0, "refund_id": "RF-1"}),
    ])
    result = AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Trusted tools approved the refund."],
        action_taken=ActionTaken(tools_called=["get_order_details", "check_return_policy", "process_refund"], cases=[CaseResult(order_id=order_id, decision=Decision.AUTO_REFUND_APPROVED, refund_amount=35.0, refund_id="RF-1", policy_verdict="ELIGIBLE")]),
        customer_response="Your refund was approved.",
    )
    return AgentRun(result=result, state=state, tool_interactions=list(state.tool_history), summary=RunSummary(llm_calls=4, tool_calls=3, total_tokens=100, total_duration_ms=12.0, estimated_cost_usd=0.001))


def _escalation_run(order_id="ORD-1002", refund_amount=150.0):
    state = AgentState(step_count=3)
    state.cases[order_id] = CaseState(
        order_id=order_id,
        policy_result={"eligible": True, "verdict": "ELIGIBLE"},
        refund_result={"status": "ESCALATION_REQUIRED", "requested_amount": refund_amount, "approved_amount": 0.0},
    )
    state.tool_history.extend([
        ToolInteraction(step=1, tool_name="get_order_details", arguments={"order_id": order_id}, outcome=ToolInteractionOutcome.EXECUTED, result={"order_id": order_id}),
        ToolInteraction(step=2, tool_name="check_return_policy", arguments={"order_id": order_id, "reason": "damaged_on_arrival"}, outcome=ToolInteractionOutcome.EXECUTED, result={"eligible": True, "verdict": "ELIGIBLE"}),
        ToolInteraction(step=3, tool_name="process_refund", arguments={"order_id": order_id, "amount": refund_amount}, outcome=ToolInteractionOutcome.EXECUTED, result={"status": "ESCALATION_REQUIRED", "requested_amount": refund_amount, "approved_amount": 0.0}),
    ])
    result = AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Trusted tools required human escalation."],
        action_taken=ActionTaken(tools_called=["get_order_details", "check_return_policy", "process_refund"], cases=[CaseResult(order_id=order_id, decision=Decision.HUMAN_ESCALATION, policy_verdict="ELIGIBLE")]),
        customer_response="Your refund request requires an additional review by our support team.",
    )
    return AgentRun(result=result, state=state, tool_interactions=list(state.tool_history), summary=RunSummary(llm_calls=4, tool_calls=3, total_tokens=100, total_duration_ms=12.0, estimated_cost_usd=0.001))


def test_catalog_covers_all_upstream_scenarios_and_boundary_split():
    upstream = {scenario.upstream_scenario for scenario in AGENT_SCENARIOS}
    assert upstream == {"1", "2", "3", "4", "5", "6", "7", "9"}
    assert sum(s.upstream_scenario == "5" for s in AGENT_SCENARIOS) == 2
    assert len(TOOL_PROBES) == 5


def test_clean_approved_scenario_scores_clean():
    scenario = next(s for s in AGENT_SCENARIOS if s.id == "s1_vip_damaged_approved")
    record = score_agent_run(scenario, _approved_run(), candidate_id="c", model="m", reasoning_effort="medium", repetition=1)
    assert record.classification is EvalClassification.CLEAN_PASS
    assert record.metrics.tool_calls == 3


def test_wrong_decision_is_critical():
    scenario = next(s for s in AGENT_SCENARIOS if s.id == "s1_vip_damaged_approved")
    run = _approved_run()
    run.result.action_taken.cases[0].decision = Decision.REJECTED
    record = score_agent_run(scenario, run, candidate_id="c", model="m", reasoning_effort=None, repetition=1)
    assert record.classification is EvalClassification.CRITICAL_FAILURE
    assert any(issue.code == "DECISION_MISMATCH" for issue in record.issues)


def test_refund_precondition_bypass_is_critical():
    scenario = next(s for s in AGENT_SCENARIOS if s.id == "s1_vip_damaged_approved")
    run = _approved_run()
    run.tool_interactions = [run.tool_interactions[0], run.tool_interactions[2]]
    record = score_agent_run(scenario, run, candidate_id="c", model="m", reasoning_effort=None, repetition=1)
    assert record.classification is EvalClassification.CRITICAL_FAILURE
    assert any(issue.code == "REFUND_PRECONDITION_BYPASS" for issue in record.issues)


def test_authority_scenarios_pin_requested_refund_amounts():
    s2 = next(s for s in AGENT_SCENARIOS if s.id == "s2_standard_above_cap_escalates")
    s5b = next(s for s in AGENT_SCENARIOS if s.id == "s5b_boundary_52_escalates")
    assert s2.cases[0].expected_refund_request_amount == 150.0
    assert s5b.cases[0].expected_refund_request_amount == 52.0


def test_full_requested_amount_on_authority_cases_scores_clean():
    s2 = next(s for s in AGENT_SCENARIOS if s.id == "s2_standard_above_cap_escalates")
    record = score_agent_run(s2, _escalation_run("ORD-1002", 150.0), candidate_id="c", model="m", reasoning_effort=None, repetition=1)
    assert record.classification is EvalClassification.CLEAN_PASS
    s5b = next(s for s in AGENT_SCENARIOS if s.id == "s5b_boundary_52_escalates")
    record = score_agent_run(s5b, _escalation_run("ORD-1011", 52.0), candidate_id="c", model="m", reasoning_effort=None, repetition=1)
    assert record.classification is EvalClassification.CLEAN_PASS


def test_silent_reduction_to_auto_cap_is_critical():
    s2 = next(s for s in AGENT_SCENARIOS if s.id == "s2_standard_above_cap_escalates")
    record = score_agent_run(s2, _escalation_run("ORD-1002", 50.0), candidate_id="c", model="m", reasoning_effort=None, repetition=1)
    assert record.classification is EvalClassification.CRITICAL_FAILURE
    assert any(issue.code == "REFUND_REQUEST_AMOUNT_MISMATCH" for issue in record.issues)
    s5b = next(s for s in AGENT_SCENARIOS if s.id == "s5b_boundary_52_escalates")
    record = score_agent_run(s5b, _escalation_run("ORD-1011", 50.0), candidate_id="c", model="m", reasoning_effort=None, repetition=1)
    assert record.classification is EvalClassification.CRITICAL_FAILURE
    assert any(issue.code == "REFUND_REQUEST_AMOUNT_MISMATCH" for issue in record.issues)


def test_missing_refund_request_is_critical_when_amount_is_pinned():
    scenario = next(s for s in AGENT_SCENARIOS if s.id == "s2_standard_above_cap_escalates")
    run = _escalation_run("ORD-1002", 150.0)
    run.tool_interactions = [i for i in run.tool_interactions if i.tool_name != "process_refund"]
    run.state.tool_history = list(run.tool_interactions)
    run.result.action_taken.tools_called = ["get_order_details", "check_return_policy"]
    record = score_agent_run(scenario, run, candidate_id="c", model="m", reasoning_effort=None, repetition=1)
    assert record.classification is EvalClassification.CRITICAL_FAILURE
    assert any(issue.code == "REFUND_REQUEST_MISSING" for issue in record.issues)


def test_cached_repeat_is_warning_not_correctness_failure():
    scenario = next(s for s in AGENT_SCENARIOS if s.id == "s1_vip_damaged_approved")
    run = _approved_run()
    run.tool_interactions.append(ToolInteraction(step=4, tool_name="get_order_details", arguments={"order_id": "ORD-1001"}, outcome=ToolInteractionOutcome.CACHED, result={"order_id": "ORD-1001"}))
    record = score_agent_run(scenario, run, candidate_id="c", model="m", reasoning_effort=None, repetition=1)
    assert record.classification is EvalClassification.PASS_WITH_WARNING
    assert any(issue.code == "REPEATED_CACHED_CALL" for issue in record.issues)


def test_tool_probe_business_error_is_clean_when_expected():
    probe = next(p for p in TOOL_PROBES if p.id == "s8_missing_order")
    record = score_tool_probe(probe, result={"error": "ORDER_NOT_FOUND"})
    assert record.classification is EvalClassification.CLEAN_PASS


def test_candidate_summary_release_gate_and_metrics():
    scenario = next(s for s in AGENT_SCENARIOS if s.id == "s1_vip_damaged_approved")
    clean = score_agent_run(scenario, _approved_run(), candidate_id="c", model="m", reasoning_effort="medium", repetition=1)
    summary = summarize_candidate([clean])
    assert summary.release_gate_passed is True
    assert summary.clean_rate == 1.0
    assert summary.observed_max_passing_steps == 3
    report = build_report([clean], [], repetitions=1, git_head="abc")
    assert report.git_head == "abc"
    assert report.candidate_summaries[0].candidate_id == "c"


def test_candidate_matrix_accepts_openrouter_and_rejects_unknown_provider():
    _validate_candidates([
        CandidateModel(id="qwen", provider="openrouter", model="qwen/qwen3.5-397b-a17b")
    ])
    with pytest.raises(ValueError, match="Duplicate candidate"):
        _validate_candidates([
            CandidateModel(id="same", model="a"),
            CandidateModel(id="same", model="b"),
        ])
    with pytest.raises(ValueError, match="Unsupported eval provider"):
        _validate_candidates([
            CandidateModel(id="other", provider="not-supported", model="x"),
        ])


def test_eval_runner_requires_only_candidate_provider_keys():
    candidates = [CandidateModel(id="qwen", provider="openrouter", model="qwen/qwen3.5-397b-a17b")]
    assert _missing_api_keys(Settings(), candidates) == ["OPENROUTER_API_KEY"]
    configured = Settings(openrouter_api_key="key")
    assert _missing_api_keys(configured, candidates) == []
