"""Deterministic consistency validator.

The validator checks structured final output against trusted tool evidence. It
intentionally does not parse natural-language wording. Terminal customer-facing
business claims are rendered by app.customer_response from canonical structured
outcomes, so safety does not depend on English/Hebrew phrase lists.
"""

from __future__ import annotations

from app.schemas import AgentResult, Decision, FinalStatus
from app.state import AgentState, CaseState, ToolInteractionOutcome

_EXECUTED_OUTCOMES = (
    ToolInteractionOutcome.EXECUTED,
    ToolInteractionOutcome.CACHED,
    ToolInteractionOutcome.BUSINESS_ERROR,
)


def validate_result(
    result: AgentResult, state: AgentState, turn_start_history: int = 0
) -> list[str]:
    """Return evidence-consistency violations; empty means structurally safe."""
    issues: list[str] = []

    reported_ids = [case_result.order_id for case_result in result.action_taken.cases]
    reported_set = set(reported_ids)

    for order_id in sorted({oid for oid in reported_set if reported_ids.count(oid) > 1}):
        issues.append(f"Case {order_id}: appears more than once in action_taken.cases.")

    for order_id in sorted(touched_case_ids(state, turn_start_history)):
        case = state.cases.get(order_id)
        if case is not None and _case_is_resolved(case) and order_id not in reported_set:
            issues.append(
                f"Case {order_id}: resolved by evidence collected in the current "
                "turn but missing from action_taken.cases."
            )

    for case_result in result.action_taken.cases:
        order_id = case_result.order_id
        case = state.cases.get(order_id)
        if case is None:
            issues.append(
                f"Case {order_id}: final output reports facts for an order that "
                "was never grounded in trusted tool state."
            )
            continue
        issues.extend(_validate_case(order_id, case_result.decision, case_result, case))
        if result.status is FinalStatus.COMPLETED and not _case_is_resolved(case):
            issues.append(
                f"Case {order_id}: COMPLETED cannot report an unresolved business case."
            )

    issues.extend(_validate_tools_called(result, state, turn_start_history))

    if not result.customer_response.strip():
        issues.append("customer_response must contain customer-facing text.")

    return issues


def touched_case_ids(state: AgentState, turn_start_history: int = 0) -> set[str]:
    """Order ids referenced by factual order-scoped tool interactions."""
    return {
        interaction.arguments["order_id"]
        for interaction in state.tool_history[turn_start_history:]
        if interaction.outcome in _EXECUTED_OUTCOMES
        and isinstance(interaction.arguments.get("order_id"), str)
    }


def _case_is_resolved(case: CaseState) -> bool:
    if case.refund_result is not None or case.terminal_error:
        return True
    policy = case.policy_result
    return isinstance(policy, dict) and policy.get("eligible") is False


def _validate_tools_called(
    result: AgentResult, state: AgentState, turn_start_history: int
) -> list[str]:
    issues: list[str] = []
    executed = {
        interaction.tool_name
        for interaction in state.tool_history[turn_start_history:]
        if interaction.outcome in _EXECUTED_OUTCOMES
    }
    reported = result.action_taken.tools_called
    reported_set = set(reported)

    for name in sorted(reported_set - executed):
        issues.append(
            f"tools_called invents {name!r}, which was never executed or cache-served."
        )
    omitted = sorted(executed - reported_set)
    if omitted:
        issues.append(
            "tools_called omits executed/cache-served tools: "
            + ", ".join(repr(name) for name in omitted)
            + "."
        )
    duplicates = sorted({name for name in reported_set if reported.count(name) > 1})
    if duplicates:
        issues.append(
            "tools_called lists duplicates: "
            + ", ".join(repr(name) for name in duplicates)
            + "."
        )
    return issues


def _validate_case(
    order_id: str, decision: Decision, case_result, case: CaseState
) -> list[str]:
    issues: list[str] = []
    refund = case.refund_result
    policy = case.policy_result

    refund_status = refund.get("status") if isinstance(refund, dict) else None
    approved = refund_status == "APPROVED"
    escalation_required = refund_status == "ESCALATION_REQUIRED"

    if approved:
        if decision is not Decision.AUTO_REFUND_APPROVED:
            issues.append(
                f"Case {order_id}: trusted refund is APPROVED but decision is {decision.value}."
            )
        if case_result.refund_amount != refund.get("approved_amount"):
            issues.append(
                f"Case {order_id}: refund_amount {case_result.refund_amount!r} does not match "
                f"trusted approved_amount {refund.get('approved_amount')!r}."
            )
        if case_result.refund_id != refund.get("refund_id"):
            issues.append(
                f"Case {order_id}: refund_id {case_result.refund_id!r} does not match "
                f"trusted refund_id {refund.get('refund_id')!r}."
            )

    if decision is Decision.AUTO_REFUND_APPROVED and not approved:
        issues.append(
            f"Case {order_id}: AUTO_REFUND_APPROVED without a trusted APPROVED process_refund result."
        )

    if escalation_required and decision is not Decision.HUMAN_ESCALATION:
        issues.append(
            f"Case {order_id}: trusted refund status is ESCALATION_REQUIRED but decision is "
            f"{decision.value}."
        )

    if refund_status == "REJECTED" and decision is not Decision.REJECTED:
        issues.append(
            f"Case {order_id}: trusted refund status is REJECTED but decision is {decision.value}."
        )

    if not approved and (case_result.refund_amount is not None or case_result.refund_id):
        issues.append(
            f"Case {order_id}: refund fields reported without a trusted APPROVED refund result."
        )

    if (
        isinstance(policy, dict)
        and policy.get("eligible") is False
        and refund is None
        and decision is not Decision.REJECTED
    ):
        issues.append(
            f"Case {order_id}: trusted policy result is ineligible but decision is "
            f"{decision.value} instead of REJECTED."
        )

    if case.terminal_error:
        if decision is not Decision.NO_ACTION:
            issues.append(
                f"Case {order_id}: trusted terminal error {case.terminal_error} requires "
                f"NO_ACTION, got {decision.value}."
            )
        if case_result.policy_verdict:
            issues.append(
                f"Case {order_id}: policy verdict reported despite terminal error "
                f"{case.terminal_error}."
            )
        if case_result.error_code != case.terminal_error:
            issues.append(
                f"Case {order_id}: error_code {case_result.error_code!r} does not match "
                f"trusted terminal error {case.terminal_error!r}."
            )

    if case_result.policy_verdict:
        trusted_verdict = policy.get("verdict") if isinstance(policy, dict) else None
        if case_result.policy_verdict != trusted_verdict:
            issues.append(
                f"Case {order_id}: policy_verdict {case_result.policy_verdict!r} does not match "
                f"trusted verdict {trusted_verdict!r}."
            )

    return issues
