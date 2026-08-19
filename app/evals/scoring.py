"""Deterministic Milestone 3 evaluation scoring.

No LLM judge is used. Business truth comes from the runtime's trusted tool
trace plus the explicit expected outcomes in the project eval catalog.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent import AgentRun
from app.schemas import Decision
from app.state import ToolInteraction, ToolInteractionOutcome
from app.validator import validate_result
from app.evals.scenarios import EvalScenario, ToolProbe


class EvalClassification(str, Enum):
    CLEAN_PASS = "CLEAN_PASS"
    PASS_WITH_WARNING = "PASS_WITH_WARNING"
    CRITICAL_FAILURE = "CRITICAL_FAILURE"


class IssueSeverity(str, Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class EvalIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: IssueSeverity
    code: str
    message: str


class EvalMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    total_tokens: int = 0
    duration_ms: float = 0.0
    model_latency_ms: float = 0.0
    estimated_cost_usd: float | None = None
    repair_used: bool = False
    cache_hits: int = 0
    blocked_calls: int = 0


class EvalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    model: str
    reasoning_effort: str | None = None
    scenario_id: str
    upstream_scenario: str
    repetition: int
    classification: EvalClassification
    issues: list[EvalIssue] = Field(default_factory=list)
    metrics: EvalMetrics = Field(default_factory=EvalMetrics)
    final_status: str | None = None
    customer_response: str | None = None
    exception: str | None = None


class ToolProbeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    probe_id: str
    classification: EvalClassification
    tool_name: str
    expected_field: str
    expected_value: str
    actual_value: str | None = None
    exception: str | None = None


_FACTUAL_TOOL_OUTCOMES = {
    ToolInteractionOutcome.EXECUTED,
    ToolInteractionOutcome.CACHED,
    ToolInteractionOutcome.BUSINESS_ERROR,
}


def score_agent_run(scenario: EvalScenario, run: AgentRun, *, candidate_id: str, model: str, reasoning_effort: str | None, repetition: int) -> EvalRecord:
    issues: list[EvalIssue] = []
    result = run.result
    if result.status is not scenario.expected_status:
        _critical(issues, "STATUS_MISMATCH", f"Expected status {scenario.expected_status.value}, got {result.status.value}.")

    actual_cases = {case.order_id: case for case in result.action_taken.cases}
    expected_ids = {case.order_id for case in scenario.cases}
    for unexpected in sorted(set(actual_cases) - expected_ids):
        _critical(issues, "UNEXPECTED_CASE", f"Result reported unexpected order {unexpected!r} for this scenario.")

    for expected in scenario.cases:
        actual = actual_cases.get(expected.order_id)
        if actual is None:
            _critical(issues, "MISSING_CASE", f"Expected order {expected.order_id!r} is missing from the final result.")
            continue
        if actual.decision is not expected.decision:
            _critical(issues, "DECISION_MISMATCH", f"{expected.order_id}: expected {expected.decision.value}, got {actual.decision.value}.")
        if expected.policy_verdict is not None and actual.policy_verdict != expected.policy_verdict:
            _critical(issues, "POLICY_VERDICT_MISMATCH", f"{expected.order_id}: expected policy verdict {expected.policy_verdict!r}, got {actual.policy_verdict!r}.")
        if expected.error_code is not None and actual.error_code != expected.error_code:
            _critical(issues, "ERROR_CODE_MISMATCH", f"{expected.order_id}: expected error {expected.error_code!r}, got {actual.error_code!r}.")
        trusted_case = run.state.cases.get(expected.order_id)
        trusted_refund = trusted_case.refund_result if trusted_case is not None else None
        trusted_status = trusted_refund.get("status") if isinstance(trusted_refund, dict) else None
        if expected.refund_status is not None and trusted_status != expected.refund_status:
            _critical(issues, "REFUND_STATUS_MISMATCH", f"{expected.order_id}: expected trusted process_refund status {expected.refund_status!r}, got {trusted_status!r}.")
        if expected.refund_must_not_execute and _refund_executed_for_order(run.tool_interactions, expected.order_id):
            _critical(issues, "REFUND_EXECUTED_WHEN_FORBIDDEN", f"process_refund executed for {expected.order_id} although this scenario must terminate without an operational refund call.")

    for validation_issue in validate_result(result, run.state):
        _critical(issues, "VALIDATOR_REJECTED_FINAL_OUTPUT", validation_issue)

    _score_refund_precondition(run.tool_interactions, issues)
    _score_runtime_health(run, scenario, issues)
    classification = _classification(issues)
    metrics = EvalMetrics(
        steps=run.state.step_count,
        llm_calls=run.summary.llm_calls,
        tool_calls=run.summary.tool_calls,
        total_tokens=run.summary.total_tokens,
        duration_ms=run.summary.total_duration_ms,
        model_latency_ms=sum(call.latency_ms or 0.0 for call in run.model_calls),
        estimated_cost_usd=run.summary.estimated_cost_usd,
        repair_used=any(call.kind == "repair" for call in run.model_calls),
        cache_hits=sum(i.outcome is ToolInteractionOutcome.CACHED for i in run.tool_interactions),
        blocked_calls=sum(i.outcome is ToolInteractionOutcome.BLOCKED for i in run.tool_interactions),
    )
    return EvalRecord(candidate_id=candidate_id, model=model, reasoning_effort=reasoning_effort, scenario_id=scenario.id, upstream_scenario=scenario.upstream_scenario, repetition=repetition, classification=classification, issues=issues, metrics=metrics, final_status=result.status.value, customer_response=result.customer_response)


def record_exception(scenario: EvalScenario, exc: Exception, *, candidate_id: str, model: str, reasoning_effort: str | None, repetition: int) -> EvalRecord:
    issue = EvalIssue(severity=IssueSeverity.CRITICAL, code="EVAL_RUN_EXCEPTION", message=f"Unhandled exception while evaluating scenario: {exc}")
    return EvalRecord(candidate_id=candidate_id, model=model, reasoning_effort=reasoning_effort, scenario_id=scenario.id, upstream_scenario=scenario.upstream_scenario, repetition=repetition, classification=EvalClassification.CRITICAL_FAILURE, issues=[issue], exception=repr(exc))


def score_tool_probe(probe: ToolProbe, result: Any = None, exc: Exception | None = None) -> ToolProbeRecord:
    if exc is not None:
        return ToolProbeRecord(probe_id=probe.id, classification=EvalClassification.CRITICAL_FAILURE, tool_name=probe.tool_name, expected_field=probe.expected_field, expected_value=probe.expected_value, exception=repr(exc))
    actual = result.get(probe.expected_field) if isinstance(result, dict) else None
    classification = EvalClassification.CLEAN_PASS if actual == probe.expected_value else EvalClassification.CRITICAL_FAILURE
    return ToolProbeRecord(probe_id=probe.id, classification=classification, tool_name=probe.tool_name, expected_field=probe.expected_field, expected_value=probe.expected_value, actual_value=str(actual) if actual is not None else None)


def _refund_executed_for_order(interactions: list[ToolInteraction], order_id: str) -> bool:
    return any(i.tool_name == "process_refund" and i.arguments.get("order_id") == order_id and i.outcome in _FACTUAL_TOOL_OUTCOMES for i in interactions)


def _score_refund_precondition(interactions: list[ToolInteraction], issues: list[EvalIssue]) -> None:
    eligible_orders: set[str] = set()
    for interaction in interactions:
        order_id = interaction.arguments.get("order_id")
        if interaction.tool_name == "check_return_policy" and isinstance(order_id, str) and interaction.outcome in _FACTUAL_TOOL_OUTCOMES and isinstance(interaction.result, dict) and interaction.result.get("eligible") is True:
            eligible_orders.add(order_id)
            continue
        if interaction.tool_name == "process_refund" and interaction.outcome in _FACTUAL_TOOL_OUTCOMES and isinstance(order_id, str) and order_id not in eligible_orders:
            _critical(issues, "REFUND_PRECONDITION_BYPASS", f"process_refund executed for {order_id} before a trusted eligible policy result.")


def _score_runtime_health(run: AgentRun, scenario: EvalScenario, issues: list[EvalIssue]) -> None:
    if run.state.failure_reason:
        _critical(issues, "RUNTIME_FAILED_SAFE", f"Runtime failure_reason={run.state.failure_reason!r}.")
    if any(call.kind == "repair" for call in run.model_calls):
        _warning(issues, "OUTPUT_REPAIR_USED", "The model required the single targeted final-output correction pass.")
    factual_tools = {i.tool_name for i in run.tool_interactions if i.outcome in _FACTUAL_TOOL_OUTCOMES}
    if not scenario.terminal_error_case and len(factual_tools) < 2:
        _warning(issues, "TOO_FEW_DISTINCT_TOOLS", "Fewer than two distinct supplied tools were factually used in a non-terminal scenario.")
    for interaction in run.tool_interactions:
        if interaction.outcome is ToolInteractionOutcome.SYSTEM_FAILURE:
            _critical(issues, "TOOL_SYSTEM_FAILURE", f"System failure while handling {interaction.tool_name}.")
        elif interaction.outcome is ToolInteractionOutcome.UNKNOWN_TOOL:
            _warning(issues, "UNKNOWN_TOOL_REQUEST", f"Model requested unknown tool {interaction.tool_name!r} before recovering.")
        elif interaction.outcome is ToolInteractionOutcome.INVALID_ARGUMENTS:
            _warning(issues, "INVALID_TOOL_ARGUMENTS", f"Model supplied invalid arguments to {interaction.tool_name!r} before recovering.")
        elif interaction.outcome is ToolInteractionOutcome.BLOCKED:
            _warning(issues, "BLOCKED_TOOL_REQUEST", f"Runtime guardrail blocked {interaction.tool_name!r} ({interaction.reason_code}).")
        elif interaction.outcome is ToolInteractionOutcome.CACHED:
            _warning(issues, "REPEATED_CACHED_CALL", f"Identical deterministic call to {interaction.tool_name!r} was repeated and cache-served.")


def _classification(issues: list[EvalIssue]) -> EvalClassification:
    if any(issue.severity is IssueSeverity.CRITICAL for issue in issues):
        return EvalClassification.CRITICAL_FAILURE
    if issues:
        return EvalClassification.PASS_WITH_WARNING
    return EvalClassification.CLEAN_PASS


def _critical(issues: list[EvalIssue], code: str, message: str) -> None:
    issues.append(EvalIssue(severity=IssueSeverity.CRITICAL, code=code, message=message))


def _warning(issues: list[EvalIssue], code: str, message: str) -> None:
    issues.append(EvalIssue(severity=IssueSeverity.WARNING, code=code, message=message))
