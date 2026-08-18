"""Deterministic consistency validator (spec section 16, GUARDRAILS section 4).

Checks the final output against trusted tool evidence only. It never
recomputes return windows, caps, fraud rules, or eligibility - supplied tool
results stay the business authority. FAILED_SAFE never disables these checks;
it may only relax completeness for genuinely unresolved cases.
"""

from __future__ import annotations

import re

from app.schemas import AgentResult, Decision
from app.state import AgentState, CaseState, ToolInteractionOutcome

#: Customer-facing phrases that assert a refund succeeded.
REFUND_SUCCESS_PHRASES = (
    "refund was approved",
    "refund has been approved",
    "refund is approved",
    "refund was issued",
    "refund has been issued",
    "refund was processed",
    "refund has been processed",
    "refund was completed",
    "refund has been completed",
    "refund succeeded",
    "refunds were approved",
    "refunds have been approved",
    "refunds are approved",
)

#: Narrow deterministic disclosure patterns for internal risk/profile signals
#: (GUARDRAILS section 4.1). Deliberately not a generic moderation system.
DISCLOSURE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"fraud\s*score",
        r"fraud\s*flag",
        r"risk\s*score",
        r"initial_fraud_score",
        r"prior_fraud_flags",
        r"\bltv\b",
        r"lifetime\s+value",
    )
)

#: Order-id mentions used for case-aware refund-claim checks.
_ORDER_ID_RE = re.compile(r"\bORD-\w+\b")

#: Sentence boundaries for claim association (kept narrow and deterministic).
_SENTENCE_SPLIT_RE = re.compile(r"[.!?;\n]+")

#: Blanket multi-case claim words ("all/both/every refunds were approved").
_BLANKET_CLAIM_RE = re.compile(r"\b(all|both|every)\b", re.IGNORECASE)

#: Interaction outcomes that count as a tool actually executed or cache-served.
#: A business-error result is still a real execution (the supplied tool ran
#: and returned error data); blocked/invalid/unknown/system-failure requests
#: were never executed.
_EXECUTED_OUTCOMES = (
    ToolInteractionOutcome.EXECUTED,
    ToolInteractionOutcome.CACHED,
    ToolInteractionOutcome.BUSINESS_ERROR,
)


def validate_result(
    result: AgentResult, state: AgentState, turn_start_history: int = 0
) -> list[str]:
    """Return consistency violations (empty list means consistent).

    Action validation is scoped to the current customer turn: `AgentResult`
    describes this turn, while `AgentState` stays the cumulative short-term
    trusted memory. `turn_start_history` is the tool_history index at turn
    start (0 validates the whole history, which is correct for single turns).
    Trusted prior-turn cases/evidence remain available for continuation and
    evidence-consistency checks.
    """
    issues: list[str] = []

    reported_ids = [case_result.order_id for case_result in result.action_taken.cases]
    reported_set = set(reported_ids)

    # Each order may appear at most once in the final output.
    for order_id in sorted({oid for oid in reported_set if reported_ids.count(oid) > 1}):
        issues.append(f"Case {order_id}: appears more than once in action_taken.cases.")

    # A case resolved by evidence collected in the current turn may never
    # silently disappear from this turn's output. Cases resolved in earlier
    # turns stay trusted in AgentState but are not re-demanded here:
    # action_taken describes the current turn only. FAILED_SAFE may omit
    # genuinely unresolved cases, never resolved ones.
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

    issues.extend(_validate_tools_called(result, state, turn_start_history))
    issues.extend(_validate_customer_response(result, state))
    return issues


def touched_case_ids(state: AgentState, turn_start_history: int = 0) -> set[str]:
    """Order ids referenced by factual tool interactions in the turn scope.

    Small explicit turn scope derived from the cumulative trusted history:
    only EXECUTED/CACHED/BUSINESS_ERROR interactions count, and only
    order-scoped ones (auxiliary profile lookups never resolve a case on
    their own, so they cannot create completeness obligations).
    """
    return {
        interaction.arguments["order_id"]
        for interaction in state.tool_history[turn_start_history:]
        if interaction.outcome in _EXECUTED_OUTCOMES
        and isinstance(interaction.arguments.get("order_id"), str)
    }


def _case_is_resolved(case: CaseState) -> bool:
    """True when trusted evidence terminally resolves the business outcome."""
    if case.refund_result is not None or case.terminal_error:
        return True
    policy = case.policy_result
    return isinstance(policy, dict) and policy.get("eligible") is False


def _validate_tools_called(
    result: AgentResult, state: AgentState, turn_start_history: int
) -> list[str]:
    """tools_called must be exactly the factual executed/cache-served tool set
    of the current customer turn (prior turns are out of scope)."""
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
            "tools_called lists duplicates: " + ", ".join(repr(name) for name in duplicates) + "."
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

    # Trusted APPROVED evidence requires AUTO_REFUND_APPROVED with matching
    # amount/id, and no other decision may coexist with an APPROVED refund.
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

    # Trusted ESCALATION_REQUIRED requires human escalation.
    if escalation_required and decision is not Decision.HUMAN_ESCALATION:
        issues.append(
            f"Case {order_id}: trusted refund status is ESCALATION_REQUIRED but decision is "
            f"{decision.value}."
        )

    # Trusted REJECTED process_refund requires a REJECTED decision.
    if refund_status == "REJECTED" and decision is not Decision.REJECTED:
        issues.append(
            f"Case {order_id}: trusted refund status is REJECTED but decision is "
            f"{decision.value}."
        )

    # Refund fields may only exist alongside an APPROVED result.
    if not approved and (case_result.refund_amount is not None or case_result.refund_id):
        issues.append(
            f"Case {order_id}: refund fields reported without a trusted APPROVED refund result."
        )

    # Trusted ineligible policy resolves the case as REJECTED.
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

    # Trusted terminal error resolves the case as NO_ACTION without facts.
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

    # Verdict must map to the stored trusted evidence for the same order.
    if case_result.policy_verdict:
        trusted_verdict = policy.get("verdict") if isinstance(policy, dict) else None
        if case_result.policy_verdict != trusted_verdict:
            issues.append(
                f"Case {order_id}: policy_verdict {case_result.policy_verdict!r} does not match "
                f"trusted verdict {trusted_verdict!r}."
            )

    return issues


def _approved_reported_orders(result: AgentResult, state: AgentState) -> set[str]:
    """Reported orders whose cumulative trusted evidence contains APPROVED."""
    return {
        case_result.order_id
        for case_result in result.action_taken.cases
        if (
            (case := state.cases.get(case_result.order_id)) is not None
            and isinstance(case.refund_result, dict)
            and case.refund_result.get("status") == "APPROVED"
        )
    }


def _validate_customer_response(result: AgentResult, state: AgentState) -> list[str]:
    issues: list[str] = []
    response = result.customer_response
    lowered_response = response.lower()
    approved_orders = _approved_reported_orders(result, state)

    # Generic refund-success wording must be grounded in a case represented by
    # this AgentResult. An approval from an unrelated earlier turn cannot make
    # a false current-turn claim valid. If a prior order is intentionally
    # reported again, its cumulative trusted evidence remains usable here.
    if not approved_orders and any(
        phrase in lowered_response for phrase in REFUND_SUCCESS_PHRASES
    ):
        issues.append(
            "customer_response claims a refund succeeded without a trusted APPROVED "
            "result for any case reported in the current AgentResult."
        )

    for sentence in _SENTENCE_SPLIT_RE.split(response):
        lowered = sentence.lower()
        if not any(phrase in lowered for phrase in REFUND_SUCCESS_PHRASES):
            continue

        # A success phrase explicitly tied to an order id needs that same
        # reported order to have trusted APPROVED evidence.
        for order_id in set(_ORDER_ID_RE.findall(sentence)):
            if order_id not in approved_orders:
                issues.append(
                    f"customer_response claims a refund succeeded for {order_id} "
                    "without a trusted APPROVED result for that reported order."
                )

        # Blanket success applies to all cases represented in the current
        # AgentResult, not only cases where process_refund happened to run.
        # Any rejection, human escalation, or NO_ACTION makes the blanket
        # statement false. APPROVED decisions must also have trusted evidence.
        blanket = _BLANKET_CLAIM_RE.search(lowered)
        if blanket:
            reported_cases = result.action_taken.cases
            all_reported_approved = bool(reported_cases) and all(
                case_result.decision is Decision.AUTO_REFUND_APPROVED
                and case_result.order_id in approved_orders
                for case_result in reported_cases
            )
            if not all_reported_approved:
                issues.append(
                    f"customer_response makes a blanket refund claim "
                    f"({blanket.group(0)!r}) but not every case reported in this turn "
                    "has a trusted approved refund outcome."
                )
            elif blanket.group(0).lower() == "both" and len(reported_cases) < 2:
                issues.append(
                    "customer_response claims 'both' refunds were approved but fewer "
                    "than two cases are reported in this turn."
                )

    for pattern in DISCLOSURE_PATTERNS:
        if pattern.search(response):
            issues.append(
                f"customer_response discloses internal risk/profile details "
                f"(pattern {pattern.pattern!r})."
            )

    return issues
