"""Deterministic consistency validator (spec section 16, GUARDRAILS section 4).

Checks the final output against trusted tool evidence only. It never
recomputes return windows, caps, fraud rules, or eligibility - supplied tool
results stay the business authority. FAILED_SAFE never disables these checks;
it may only relax completeness for genuinely unresolved cases.
"""

from __future__ import annotations

import re
from typing import Any

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

#: Negation immediately preceding a success phrase means the sentence denies
#: success ("no refund has been issued") rather than claiming it.
_NEGATION_BEFORE_SUCCESS_RE = re.compile(
    r"\b(?:no|not|never)\b(?:\s+\w+){0,3}\s*$", re.IGNORECASE
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

#: Operational timeline indicators (refund settlement / payment / shipping /
#: delivery). A timeline claim is only allowed when the same phrase exists in
#: trusted tool output.
_TIMELINE_CONTEXT_TERMS = (
    "refund",
    "payment",
    "account",
    "method",
    "shipping",
    "delivery",
    "settlement",
    "arrive",
    "appear",
)

#: Phrases that assert a specific duration.
_TIMELINE_RE = re.compile(
    r"\b(within|in)\b[^.!?;\n]*?\b(?:\d+(?:[-–]\d+)?\s+|a\s+few\s+)?(?:business\s+)?(?:days?|hours?|weeks?)\b",
    re.IGNORECASE,
)

#: A duration is an operational promise only when the sentence also predicts
#: an operational event. This prevents policy-window statements such as
#: "refund requests are allowed within 30 days" from being mistaken for a
#: settlement/shipping promise, while still catching "the refund will appear
#: within 3-5 business days".
_OPERATIONAL_TIMELINE_PREDICATE_RE = re.compile(
    r"\b(?:will|should|expected\s+to|takes?|processed|completed|issued|credited|settled|arrive|appear|receive)\b",
    re.IGNORECASE,
)

#: The supplied tools can require human review, but they never promise that a
#: representative will contact/reach out/follow up with the customer. Keep
#: review status distinct from an invented future service commitment.
_UNSUPPORTED_FOLLOWUP_PROMISE_RE = re.compile(
    r"\b(?:will|shall|going\s+to)\b[^.!?;\n]{0,80}\b(?:contact|reach\s+out|follow\s+up)\b",
    re.IGNORECASE,
)

#: If no reported case has a trusted APPROVED refund, the response may not
#: promise that a refund will later be completed/processed/issued/credited.
#: Escalation means review is required, not that eventual payout is certain.
_UNAPPROVED_FUTURE_REFUND_RE = re.compile(
    r"\b(?:will|shall|going\s+to|expected\s+to)\b[^.!?;\n]{0,100}\b"
    r"(?:complete|process|issue|credit|settle)\w*\b[^.!?;\n]{0,80}\brefund\b"
    r"|\brefund\b[^.!?;\n]{0,80}\b(?:will|shall|going\s+to|expected\s+to)\b"
    r"[^.!?;\n]{0,80}\b(?:complete|process|issue|credit|settle)\w*\b",
    re.IGNORECASE,
)

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


def _string_values(obj: Any) -> list[str]:
    """Recursively collect all string leaf values from a JSON-like object."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for value in obj.values() for s in _string_values(value)]
    if isinstance(obj, list):
        return [s for item in obj for s in _string_values(item)]
    return []


def _case_evidence_text(state: AgentState, order_id: str) -> str:
    """Lowercased concatenation of trusted string evidence for one case only.

    Includes the stored case payloads plus the results of order-scoped tool
    interactions whose arguments target this order id. Evidence from other
    cases or from generic interactions is deliberately out of scope.
    """
    parts: list[str] = []
    case = state.cases.get(order_id)
    if case is not None:
        for payload in (
            case.verified_order,
            case.verified_user,
            case.policy_result,
            case.refund_result,
        ):
            if isinstance(payload, dict):
                parts.extend(_string_values(payload))
    for interaction in state.tool_history:
        if (
            interaction.outcome in _EXECUTED_OUTCOMES
            and isinstance(interaction.result, dict)
            and interaction.arguments.get("order_id") == order_id
        ):
            parts.extend(_string_values(interaction.result))
    return " ".join(parts).lower()


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


def _sentence_claims_refund_success(sentence: str) -> bool:
    """True only for affirmative refund-success wording, not explicit denial."""
    lowered = sentence.lower()
    for phrase in REFUND_SUCCESS_PHRASES:
        start = lowered.find(phrase)
        while start != -1:
            prefix = lowered[max(0, start - 32) : start]
            if not _NEGATION_BEFORE_SUCCESS_RE.search(prefix):
                return True
            start = lowered.find(phrase, start + 1)
    return False


def _validate_customer_response(result: AgentResult, state: AgentState) -> list[str]:
    issues: list[str] = []
    response = result.customer_response
    approved_orders = _approved_reported_orders(result, state)
    sentences = _SENTENCE_SPLIT_RE.split(response)

    # Generic refund-success wording must be grounded in a case represented by
    # this AgentResult. Explicit denials such as "No refund has been issued"
    # are safe and must never be misclassified as success.
    if not approved_orders and any(
        _sentence_claims_refund_success(sentence) for sentence in sentences
    ):
        issues.append(
            "customer_response claims a refund succeeded without a trusted APPROVED "
            "result for any case reported in the current AgentResult."
        )

    for sentence in sentences:
        if not _sentence_claims_refund_success(sentence):
            continue
        lowered = sentence.lower()

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

    # Human review is supported by ESCALATION_REQUIRED; a promise that someone
    # will contact/reach out/follow up is not. No supplied tool commits another
    # person to a future customer interaction.
    if any(_UNSUPPORTED_FOLLOWUP_PROMISE_RE.search(sentence) for sentence in sentences):
        issues.append(
            "customer_response invents an unsupported future contact/follow-up commitment "
            "(issue: UNSUPPORTED_FOLLOWUP_PROMISE)."
        )

    # Likewise, escalation must not be phrased as a guarantee that the refund
    # will eventually be completed. Only trusted APPROVED evidence authorizes
    # a successful refund claim.
    if not approved_orders and any(
        _UNAPPROVED_FUTURE_REFUND_RE.search(sentence) for sentence in sentences
    ):
        issues.append(
            "customer_response promises future refund completion without a trusted "
            "APPROVED result (issue: UNAPPROVED_FUTURE_REFUND_PROMISE)."
        )

    # Operational timelines (refund settlement, payment processing, shipping,
    # delivery) must be grounded in trusted tool output for the relevant
    # reported case(s). A policy eligibility/window statement is not an
    # operational promise, so duration text is checked only when the sentence
    # also predicts an operational event.
    reported_order_ids = {case.order_id for case in result.action_taken.cases}
    for sentence in sentences:
        timeline_match = _TIMELINE_RE.search(sentence)
        if not timeline_match:
            continue
        lowered_sentence = sentence.lower()
        if not any(term in lowered_sentence for term in _TIMELINE_CONTEXT_TERMS):
            continue
        if not _OPERATIONAL_TIMELINE_PREDICATE_RE.search(sentence):
            continue
        phrase = timeline_match.group(0).strip().lower()

        sentence_order_ids = [
            order_id
            for order_id in set(_ORDER_ID_RE.findall(sentence))
            if order_id in reported_order_ids
        ]
        if sentence_order_ids:
            # Explicitly referenced cases must each independently support the
            # timeline phrase.
            scope_order_ids = sentence_order_ids
        else:
            # Generic timeline not tied to a specific reported case: every
            # case reported in the current AgentResult must support it.
            scope_order_ids = sorted(reported_order_ids)

        if not scope_order_ids or any(
            phrase not in _case_evidence_text(state, order_id)
            for order_id in scope_order_ids
        ):
            issues.append(
                "customer_response invents an unsupported operational timeline "
                f"({phrase!r}; issue: UNSUPPORTED_TIMELINE_CLAIM)."
            )

    return issues
