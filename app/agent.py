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

import json
import re
import time
from dataclasses import dataclass, field

from app.llm.base import LLMProvider, TransientLLMFailure, ensure_unique_tool_call_ids
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

#: Observation used to close outstanding tool calls after a system failure.
#: Protocol cleanup only - no tool is executed and no retry is implied.
_CANCELLED_TOOL_OBSERVATION = json.dumps(
    {
        "error": "SYSTEM_FAILURE",
        "message": (
            "This tool call was cancelled due to an internal system failure. "
            "No result is available and it will not be retried."
        ),
    },
    ensure_ascii=False,
)

#: Narrow Hebrew-character check for fail-safe wording language selection.
_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")


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

            # Defense in depth: even if a provider skipped boundary id
            # normalization, canonical tool exchanges always carry stable,
            # non-empty, unique tool_call ids.
            response = ensure_unique_tool_call_ids(response)

            if response.tool_calls:
                duplicate_step = self._all_calls_duplicate(response.tool_calls, state)
                state.messages.append(
                    AssistantToolCallMessage(
                        content=response.content, tool_calls=list(response.tool_calls)
                    )
                )
                for index, call in enumerate(response.tool_calls):
                    try:
                        observation, interaction = executor.execute(
                            state.step_count, call, state
                        )
                    except ToolSystemFailure as exc:
                        # Not a recoverable business error: traced, fail safe.
                        failure_reason = f"TOOL_SYSTEM_FAILURE: {exc}"
                        # Protocol cleanup only: close every outstanding
                        # tool_call_id so the stored conversation remains a
                        # valid tool exchange. Remaining tools never execute
                        # and the model is not invoked again this turn.
                        _close_outstanding_calls(state.messages, response.tool_calls[index:])
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

        # Accepted CaseResult decisions mirror into CaseState for short-term
        # audit/continuation; trusted tool evidence stays authoritative.
        for case_result in result.action_taken.cases:
            case = state.cases.get(case_result.order_id)
            if case is not None:
                case.decision = case_result.decision

        state.sentiment = assessment.sentiment if assessment is not None else None
        state.urgency = assessment.urgency if assessment is not None else None
        state.final_result = result
        state.status = _STATUS_MAP[result.status]

        # The next customer turn must see the conversation the customer
        # actually experienced: append the delivered response. Repair-internal
        # messages are ephemeral and never reach conversation state.
        state.messages.append(
            CanonicalMessage(role="assistant", content=result.customer_response)
        )

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
                repair = repair_final_output(
                    self._provider,
                    state.messages,
                    content,
                    problems or ["Unknown validation failure."],
                    max_retries=self._settings.llm_max_retries,
                    backoff_seconds=self._settings.llm_retry_backoff_seconds,
                )
            except RepairFailed as exc:
                return None, None, f"OUTPUT_REPAIR_FAILED: {exc}"
            # The repair pass is a real model call: preserve its actual
            # provider/model/latency/token/retry metadata so it reaches the
            # trace and RunSummary totals like any other call.
            model_calls.append(
                ModelCallRecord(
                    step=state.step_count,
                    provider=repair.response.provider,
                    model=repair.response.model,
                    kind="repair",
                    latency_ms=repair.response.latency_ms,
                    input_tokens=repair.response.input_tokens,
                    output_tokens=repair.response.output_tokens,
                    total_tokens=repair.response.total_tokens,
                    attempt=repair.attempts,
                    retries=repair.attempts - 1,
                )
            )
            issues = validate_result(repair.result, state)
            if issues:
                return None, None, f"VALIDATION_FAILED_AFTER_REPAIR: {'; '.join(issues)}"
            result = repair.result
            assessment = repair.assessment

        return result, assessment, None

    # ------------------------------------------------------------------
    # Fail-safe and summary
    # ------------------------------------------------------------------

    def _build_fail_safe_result(self, state: AgentState, reason: str) -> AgentResult:
        """Deterministic FAILED_SAFE output grounded in trusted evidence.

        Resolved case outcomes are preserved exactly as the evidence supports
        them. Human-review wording appears only for genuinely unresolved cases:
        when every known case already has a trusted terminal outcome, those
        outcomes are reported directly (spec section 13, validator rule 9,
        execution clarification 4, review fix 6).
        """
        cases = [_case_result_from_evidence(case) for case in _ordered_cases(state)]
        has_unresolved = any(_is_unresolved_escalation(case) for case in cases)
        tools_called = sorted(
            {
                interaction.tool_name
                for interaction in state.tool_history
                if interaction.outcome in _CACHEABLE_OUTCOMES
            }
        )

        wording = _FAIL_SAFE_WORDING[_latest_customer_language(state)]
        if not cases:
            response = wording["no_cases"]
        else:
            lines = "; ".join(
                f"{wording['order_prefix']}{case.order_id}: "
                f"{_case_customer_line(case, wording)}"
                for case in cases
            )
            if has_unresolved:
                response = (
                    f"{wording['unresolved_preamble']} {wording['status_by_order']} {lines}"
                )
            else:
                response = lines

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


#: Deterministic fail-safe customer wording per supported language. The
#: customer's language is detected with a narrow Hebrew-character check on
#: the latest customer message; anything else defaults to English.
_FAIL_SAFE_WORDING = {
    "en": {
        "no_cases": (
            "We were unable to complete processing of your request. A human "
            "agent will review it and follow up with you."
        ),
        "unresolved_preamble": (
            "We were unable to complete processing of your request, so a "
            "human agent will review it."
        ),
        "status_by_order": "Status by order -",
        "order_prefix": "Order ",
        "refund_approved": "your refund was approved (refund id {refund_id}).",
        "rejected": "the request could not be approved under the return policy.",
        "no_action": "we could not verify this order; please confirm the order number.",
        "escalation": "requires additional review by a human agent.",
    },
    "he": {
        "no_cases": (
            "לא הצלחנו להשלים את הטיפול בפנייה שלך. נציג אנושי יבדוק אותה ויחזור אליך."
        ),
        "unresolved_preamble": (
            "לא הצלחנו להשלים את הטיפול בפנייה שלך, ולכן נציג אנושי יבדוק אותה."
        ),
        "status_by_order": "סטטוס לפי הזמנה -",
        "order_prefix": "הזמנה ",
        "refund_approved": "ההחזר הכספי שלך אושר (מספר החזר {refund_id}).",
        "rejected": "הבקשה לא אושרה בהתאם למדיניות ההחזרות.",
        "no_action": "לא הצלחנו לאמת הזמנה זו; נא לוודא את מספר ההזמנה.",
        "escalation": "נדרשת בדיקה נוספת על ידי נציג אנושי.",
    },
}


def _close_outstanding_calls(messages: list, outstanding: list) -> None:
    """Protocol cleanup after a tool system failure.

    Every still-open tool_call_id from the assistant tool-call message gets a
    canonical cancellation observation, so the stored conversation stays a
    valid tool exchange for future continuation. No tool is executed here and
    the model is never re-invoked in the failed turn.
    """
    for call in outstanding:
        messages.append(
            ToolObservationMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=_CANCELLED_TOOL_OBSERVATION,
            )
        )


def _latest_customer_language(state: AgentState) -> str:
    """Narrow Hebrew-character check on the latest customer message."""
    for message in reversed(state.messages):
        if isinstance(message, CanonicalMessage) and message.role == "user":
            return "he" if _HEBREW_RE.search(message.content) else "en"
    return "en"


def _ordered_cases(state: AgentState) -> list[CaseState]:
    return [state.cases[order_id] for order_id in sorted(state.cases)]


def _is_unresolved_escalation(case_result: CaseResult) -> bool:
    """True only for the fail-safe placeholder escalation of genuinely
    unresolved cases, never for trusted ESCALATION_REQUIRED evidence."""
    return (
        case_result.decision is Decision.HUMAN_ESCALATION
        and "UNRESOLVED_AT_FAILURE" in case_result.escalation_reasons
    )


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


def _case_customer_line(case: CaseResult, wording: dict[str, str]) -> str:
    """Customer-safe wording per case; never implies a false refund."""
    if case.decision is Decision.AUTO_REFUND_APPROVED:
        return wording["refund_approved"].format(refund_id=case.refund_id)
    if case.decision is Decision.REJECTED:
        return wording["rejected"]
    if case.decision is Decision.NO_ACTION:
        return wording["no_action"]
    return wording["escalation"]
