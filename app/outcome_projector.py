"""Deterministic projection of trusted tool evidence into final business outcomes.

The LLM owns understanding, tool choice, ordering, and when to stop. Once the
supplied tools have produced a terminal business outcome, Python owns both the
structured representation of that outcome and the customer-facing action facts.
This prevents a probabilistic final response from turning trusted evidence into
an invented refund, a different terminal status, or an unsupported promise.

This module deliberately does not recompute policy. It only maps trusted tool
results already stored in AgentState into the public CaseResult contract.
"""

from __future__ import annotations

from app.customer_response import render_terminal_customer_response
from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState, ToolInteractionOutcome

_FACTUAL_OUTCOMES = (
    ToolInteractionOutcome.EXECUTED,
    ToolInteractionOutcome.CACHED,
    ToolInteractionOutcome.BUSINESS_ERROR,
)


def touched_case_ids(state: AgentState, turn_start_history: int = 0) -> set[str]:
    """Return order ids with factual order-scoped interactions in this turn."""
    return {
        interaction.arguments["order_id"]
        for interaction in state.tool_history[turn_start_history:]
        if interaction.outcome in _FACTUAL_OUTCOMES
        and isinstance(interaction.arguments.get("order_id"), str)
    }


def factual_tools_called(state: AgentState, turn_start_history: int = 0) -> list[str]:
    """Return the exact factual tool-name set for this turn."""
    return sorted(
        {
            interaction.tool_name
            for interaction in state.tool_history[turn_start_history:]
            if interaction.outcome in _FACTUAL_OUTCOMES
        }
    )


def project_case_result(case: CaseState) -> CaseResult | None:
    """Map a terminal trusted CaseState to CaseResult, or None if unresolved."""
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
        if status == "REJECTED":
            return CaseResult(
                order_id=case.order_id,
                decision=Decision.REJECTED,
                policy_verdict=verdict,
                escalation_reasons=list(refund.get("reasons") or []),
            )
        return None

    if case.terminal_error:
        return CaseResult(
            order_id=case.order_id,
            decision=Decision.NO_ACTION,
            error_code=case.terminal_error,
        )

    if isinstance(policy, dict) and policy.get("eligible") is False:
        return CaseResult(
            order_id=case.order_id,
            decision=Decision.REJECTED,
            policy_verdict=verdict,
        )

    return None


def project_result_from_state(
    result: AgentResult,
    state: AgentState,
    turn_start_history: int = 0,
) -> AgentResult:
    """Canonicalize runtime-owned final fields from trusted current-turn state.

    Resolved touched cases are replaced by deterministic projections. During a
    clarification turn, touched-but-unresolved cases are canonicalized to
    ``NO_ACTION`` with no terminal business fields; the model cannot smuggle an
    unsupported escalation/refund decision into structured output. Model-only
    cases remain long enough for the validator to reject hallucinations.

    Once all touched cases are terminal, the top-level status is COMPLETED and
    customer-facing action facts are rendered deterministically in the latest
    customer's language. Clarification wording is also rendered safely from
    structural state.
    """
    touched = sorted(touched_case_ids(state, turn_start_history))
    projected: dict[str, CaseResult] = {}
    all_touched_resolved = bool(touched)

    for order_id in touched:
        case = state.cases.get(order_id)
        canonical = project_case_result(case) if case is not None else None
        if canonical is None:
            all_touched_resolved = False
        else:
            projected[order_id] = canonical

    reported_by_id = {case.order_id: case for case in result.action_taken.cases}
    cases: list[CaseResult] = []

    for order_id in touched:
        if order_id in projected:
            cases.append(projected[order_id])
        elif result.status is FinalStatus.NEEDS_CLARIFICATION:
            cases.append(CaseResult(order_id=order_id, decision=Decision.NO_ACTION))
        elif order_id in reported_by_id:
            cases.append(reported_by_id[order_id])

    touched_set = set(touched)
    cases.extend(
        case for case in result.action_taken.cases if case.order_id not in touched_set
    )

    status = FinalStatus.COMPLETED if touched and all_touched_resolved else result.status

    projected_result = result.model_copy(
        update={
            "status": status,
            "action_taken": ActionTaken(
                tools_called=factual_tools_called(state, turn_start_history),
                cases=cases,
            ),
        }
    )

    canonical_response = render_terminal_customer_response(projected_result, state)
    if canonical_response is not None:
        projected_result = projected_result.model_copy(
            update={"customer_response": canonical_response}
        )

    return projected_result
