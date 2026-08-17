"""Autonomous Operations Resolver runtime (spec sections 4, 7, 13, 16, 17).

One agent loop with probabilistic intelligence and deterministic guardrails:

- The model chooses which allowed tools to call, in what order, and when it
  is done. Python owns execution, trusted state, caching, retries, loop
  bounds, guardrail blocking, validation, the single repair pass, fail-safe
  behavior, and tracing.
- There is deliberately no fixed order->user->policy->refund workflow. The
  only hard rules are safety guardrails (refund precondition, terminal-case
  stop, loop bounds) and evidence-consistency validation.
- Multi-turn continuation preserves short-term trusted state/messages/cases/
  tool_history and resets per-turn runtime fields (execution clarification 1).
- Tool system failures are not recoverable business errors: they are traced
  and fail safe, preserving already-resolved trusted outcomes where possible
  (execution clarification 4).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.llm.base import LLMProvider, TransientLLMFailure
from app.llm.retry import generate_with_retry
from app.messages import (
    AssistantToolCallMessage,
    CanonicalMessage,
    ToolObservationMessage,
)
from app.output_parser import ModelAssessment, OutputParseError, parse_final_output
from app.prompts import load_system_prompt
from app.repair import RepairFailed, repair_final_output
from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import (
    AgentState,
    CaseState,
    RuntimeStatus,
    ToolInteraction,
    ToolInteractionOutcome,
)
from app.tool_cache import ToolCache, normalize_args
from app.tool_executor import ToolExecutor, ToolSystemFailure
from app.tools_adapter import ToolKit
from app.tracing import BlockedToolEvent, ModelCallRecord, RunSummary, estimate_cost
from app.validator import validate_result

#: Consecutive steps of only-identical repeated calls before fail-safe.
#: Tracked per customer turn; resets at the start of every turn.
NO_PROGRESS_LIMIT = 3

#: Interaction outcomes whose stored results reseed a continuation cache.
_CACHEABLE_OUTCOMES = (
    ToolInteractionOutcome.EXECUTED,
    ToolInteractionOutcome.CACHED,
    ToolInteractionOutcome.BUSINESS_ERROR,
)

_STATUS_MAP = {
    FinalStatus.COMPLETED: RuntimeStatus.COMPLETED,
    FinalStatus.NEEDS_CLARIFICATION: RuntimeStatus.NEEDS_CLARIFICATION,
    FinalStatus.FAILED_SAFE: RuntimeStatus.FAILED_SAFE,
}


@dataclass
class AgentRun:
    """Outcome of one customer turn; hand it back to continue the session."""

    result: AgentResult
    state: AgentState
    model_calls: list[ModelCallRecord] = field(default_factory=list)
    tool_interactions: list[ToolInteraction] = field(default_factory=list)
    blocked_events: list[BlockedToolEvent] = field(default_factory=list)
    summary: RunSummary = field(default_factory=RunSummary)


class OperationsResolverAgent:
    """Single autonomous operations resolver (no orchestrator, no sub-agents)."""

    def __init__(self, settings, provider: LLMProvider, kit: ToolKit) -> None:
        self._settings = settings
        self._provider = provider
        self._kit = kit
        self._system_prompt = load_system_prompt()

    # ------------------------------------------------------------------
    # Turn entry point
    # ------------------------------------------------------------------

    def handle_customer_message(
        self, text: str, previous: AgentRun | None = None
    ) -> AgentRun:
        """Process one customer message, optionally continuing a prior run."""
        started = time.perf_counter()
        state, cache = self._prepare_state(previous)
        executor = ToolExecutor(self._kit, cache)
        model_calls: list[ModelCallRecord] = []
        blocked_events: list[BlockedToolEvent] = []
        turn_start_history = len(state.tool_history)

        state.messages.append(CanonicalMessage(role="user", content=text))

        failure_reason: str | None = None
        result: AgentResult | None = None
        assessment: ModelAssessment | None = None
        no_progress_steps = 0
        max_steps = self._settings.agent_max_steps

        while failure_reason is None and result is None:
            if max_steps is not None and state.step_count >= max_steps:
                failure_reason = "MAX_STEPS_EXCEEDED"
                break
            state.step_count += 1

            try:
                response, attempts = generate_with_retry(
                    self._provider,
                    state.messages,
                    self._kit.schemas,
                    max_retries=self._settings.llm_max_retries,
                    backoff_seconds=self._settings.llm_retry_backoff_seconds,
                )
            except TransientLLMFailure as exc:
                failure_reason = f"LLM_FAILURE: transient retries exhausted ({exc})"
                break
            except Exception as exc:  # non-transient provider failure: fail closed
                failure_reason = f"LLM_FAILURE: {exc}"
                break

            model_calls.append(
                ModelCallRecord(
                    step=state.step_count,
                    provider=response.provider,
                    model=response.model,
                    kind="tool_call" if response.tool_calls else "final",
                    latency_ms=response.latency_ms,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    total_tokens=response.total_tokens,
                    attempt=attempts,
                    retries=attempts - 1,
                )
            )

            if response.tool_calls:
                duplicate_step = self._all_calls_duplicate(response.tool_calls, state)
                state.messages.append(
                    AssistantToolCallMessage(
                        content=response.content, tool_calls=list(response.tool_calls)
                    )
                )
                for call in response.tool_calls:
                    try:
                        observation, interaction = executor.execute(
                            state.step_count, call, state
                        )
                    except ToolSystemFailure as exc:
                        # Not a recoverable business error: traced, fail safe.
                        failure_reason = f"TOOL_SYSTEM_FAILURE: {exc}"
                        break
                    state.messages.append(
                        ToolObservationMessage(
                            tool_call_id=call.id, tool_name=call.name, content=observation
                        )
                    )
                    if interaction.outcome is ToolInteractionOutcome.BLOCKED:
                        blocked_events.append(
                            BlockedToolEvent(
                                step=state.step_count,
                                tool_name=call.name,
                                arguments=dict(call.arguments),
                                reason=interaction.reason_code or "BLOCKED",
                            )
                        )
                if failure_reason is not None:
                    break
                no_progress_steps = no_progress_steps + 1 if duplicate_step else 0
                if no_progress_steps >= NO_PROGRESS_LIMIT:
                    failure_reason = "NO_PROGRESS: repeated identical calls made no progress"
                continue

            result, assessment, failure_reason = self._finalize_from_content(
                response.content, state, model_calls
            )

        if result is None:
            state.failure_reason = failure_reason or "UNKNOWN_FAILURE"
            result = self._build_fail_safe_result(state, state.failure_reason)
        else:
            state.failure_reason = None

        state.sentiment = assessment.sentiment if assessment is not None else None
        state.urgency = assessment.urgency if assessment is not None else None
        state.final_result = result
        state.status = _STATUS_MAP[result.status]

        return AgentRun(
            result=result,
            state=state,
            model_calls=model_calls,
            tool_interactions=list(state.tool_history[turn_start_history:]),
            blocked_events=blocked_events,
            summary=self._build_summary(model_calls, state, turn_start_history, started),
        )

    # ------------------------------------------------------------------
    # Turn preparation
    # ------------------------------------------------------------------

    def _prepare_state(self, previous: AgentRun | None) -> tuple[AgentState, ToolCache]:
        """Continue prior trusted state or start fresh; reset per-turn fields."""
        if previous is None:
            state = AgentState()
            state.messages.append(
                CanonicalMessage(role="system", content=self._system_prompt)
            )
            return state, ToolCache()

        state = previous.state
        # Per-turn runtime reset (execution clarification 1): trusted state,
        # messages, cases, and tool_history continue; counters start fresh.
        state.step_count = 0
        state.status = RuntimeStatus.RUNNING
        state.final_result = None
        state.failure_reason = None
        return state, self._reseed_cache(state)

    @staticmethod
    def _reseed_cache(state: AgentState) -> ToolCache:
        """Session-local cache reseeded from prior trusted tool_history results."""
        cache = ToolCache()
        for interaction in state.tool_history:
            if interaction.result is not None and interaction.outcome in _CACHEABLE_OUTCOMES:
                cache.put(interaction.tool_name, interaction.arguments, interaction.result)
        return cache

    @staticmethod
    def _all_calls_duplicate(calls, state: AgentState) -> bool:
        """True when every requested call matches an identical prior interaction."""
        prior = {
            (interaction.tool_name, normalize_args(interaction.arguments))
            for interaction in state.tool_history
        }
        return all(
            (call.name, normalize_args(dict(call.arguments or {}))) in prior
            for call in calls
        )

    # ------------------------------------------------------------------
    # Final output handling
    # ------------------------------------------------------------------

    def _finalize_from_content(
        self,
        content: str | None,
        state: AgentState,
        model_calls: list[ModelCallRecord],
    ) -> tuple[AgentResult | None, ModelAssessment | None, str | None]:
        """Parse and validate final content; at most one ephemeral repair pass."""
        problems: list[str] | None = None
        result: AgentResult | None = None
        assessment: ModelAssessment | None = None

        try:
            result, assessment = parse_final_output(content)
        except OutputParseError as exc:
            problems = [str(exc)]

        if result is not None:
            issues = validate_result(result, state)
            if issues:
                problems = issues
                result, assessment = None, None

        if result is None:
            try:
                repaired, assessment = repair_final_output(
                    self._provider,
                    state.messages,
                    content,
                    problems or ["Unknown validation failure."],
                    max_retries=self._settings.llm_max_retries,
                    backoff_seconds=self._settings.llm_retry_backoff_seconds,
                )
            except RepairFailed as exc:
                return None, None, f"OUTPUT_REPAIR_FAILED: {exc}"
            model_calls.append(
                ModelCallRecord(
                    step=state.step_count,
                    provider="repair-pass",
                    model="repair-pass",
                    kind="repair",
                )
            )
            issues = validate_result(repaired, state)
            if issues:
                return None, None, f"VALIDATION_FAILED_AFTER_REPAIR: {'; '.join(issues)}"
            result = repaired

        return result, assessment, None

    # ------------------------------------------------------------------
    # Fail-safe and summary
    # ------------------------------------------------------------------

    def _build_fail_safe_result(self, state: AgentState, reason: str) -> AgentResult:
        """Deterministic FAILED_SAFE output grounded in trusted evidence.

        Resolved case outcomes are preserved exactly as the evidence supports
        them; only genuinely unresolved cases are escalated for human review
        (spec section 13, validator rule 9, execution clarification 4).
        """
        cases = [_case_result_from_evidence(case) for case in _ordered_cases(state)]
        tools_called = sorted(
            {
                interaction.tool_name
                for interaction in state.tool_history
                if interaction.outcome in _CACHEABLE_OUTCOMES
            }
        )

        if cases:
            lines = "; ".join(
                f"Order {case.order_id}: {_case_customer_line(case)}" for case in cases
            )
            response = (
                "We were unable to complete processing of your request, so a human "
                f"agent will review it. Status by order - {lines}"
            )
        else:
            response = (
                "We were unable to complete processing of your request. A human "
                "agent will review it and follow up with you."
            )

        return AgentResult(
            status=FinalStatus.FAILED_SAFE,
            reasoning_chain=[
                f"Run failed safely: {reason}.",
                *(
                    f"Preserved trusted evidence for order {case.order_id}."
                    for case in cases
                ),
            ],
            action_taken=ActionTaken(tools_called=tools_called, cases=cases),
            customer_response=response,
        )

    def _build_summary(
        self,
        model_calls: list[ModelCallRecord],
        state: AgentState,
        turn_start_history: int,
        started: float,
    ) -> RunSummary:
        input_tokens = sum(call.input_tokens or 0 for call in model_calls)
        output_tokens = sum(call.output_tokens or 0 for call in model_calls)
        return RunSummary(
            llm_calls=len(model_calls),
            tool_calls=len(state.tool_history) - turn_start_history,
            total_tokens=input_tokens + output_tokens,
            total_duration_ms=(time.perf_counter() - started) * 1000.0,
            estimated_cost_usd=estimate_cost(
                input_tokens,
                output_tokens,
                self._settings.llm_input_cost_per_million,
                self._settings.llm_output_cost_per_million,
            ),
        )


# ----------------------------------------------------------------------
# Evidence-grounded helpers
# ----------------------------------------------------------------------


def _ordered_cases(state: AgentState) -> list[CaseState]:
    return [state.cases[order_id] for order_id in sorted(state.cases)]


def _case_result_from_evidence(case: CaseState) -> CaseResult:
    """Map trusted CaseState evidence to a CaseResult without inventing facts."""
    policy = case.policy_result
    verdict = policy.get("verdict") if isinstance(policy, dict) else None
    refund = case.refund_result

    if isinstance(refund, dict):
        status = refund.get("status")
        if status == "APPROVED":
            return CaseResult(
                order_id=case.order_id,
                decision=Decision.AUTO_REFUND_APPROVED,
                refund_amount=refund.get("approved_amount"),
                refund_id=refund.get("refund_id"),
                policy_verdict=verdict,
            )
        if status == "ESCALATION_REQUIRED":
            return CaseResult(
                order_id=case.order_id,
                decision=Decision.HUMAN_ESCALATION,
                policy_verdict=verdict,
                escalation_reasons=list(refund.get("reasons") or []),
            )
        return CaseResult(
            order_id=case.order_id,
            decision=Decision.REJECTED,
            policy_verdict=verdict,
            escalation_reasons=list(refund.get("reasons") or []),
        )

    if case.terminal_error:
        return CaseResult(
            order_id=case.order_id,
            decision=Decision.NO_ACTION,
            error_code=case.terminal_error,
        )

    if isinstance(policy, dict) and policy.get("eligible") is False:
        return CaseResult(
            order_id=case.order_id, decision=Decision.REJECTED, policy_verdict=verdict
        )

    return CaseResult(
        order_id=case.order_id,
        decision=Decision.HUMAN_ESCALATION,
        escalation_reasons=["UNRESOLVED_AT_FAILURE"],
    )


def _case_customer_line(case: CaseResult) -> str:
    """Customer-safe wording per resolved case; never implies a false refund."""
    if case.decision is Decision.AUTO_REFUND_APPROVED:
        return f"your refund was approved (refund id {case.refund_id})."
    if case.decision is Decision.REJECTED:
        return "the request could not be approved under the return policy."
    if case.decision is Decision.NO_ACTION:
        return "we could not verify this order; please confirm the order number."
    return "requires additional review by a human agent."
