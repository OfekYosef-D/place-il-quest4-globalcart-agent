"""Deterministic consistency validator (spec section 16, GUARDRAILS section 4).

Checks the final output against trusted tool evidence only. It never
recomputes return windows, caps, fraud rules, or eligibility - supplied tool
results stay the business authority. FAILED_SAFE never disables these checks;
it may only relax completeness for genuinely unresolved cases.
"""

from __future__ import annotations

import re

from app.schemas import AgentResult, Decision, FinalStatus
from app.state import AgentState, CaseState

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


def validate_result(result: AgentResult, state: AgentState) -> list[str]:
    """Return consistency violations (empty list means consistent)."""
    issues: list[str] = []

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

    issues.extend(_validate_customer_response(result, state))
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

    # Refund fields may only exist alongside an APPROVED result.
    if not approved and (case_result.refund_amount is not None or case_result.refund_id):
        issues.append(
            f"Case {order_id}: refund fields reported without a trusted APPROVED refund result."
        )

    # Trusted ESCALATION_REQUIRED requires human escalation.
    if escalation_required and decision is not Decision.HUMAN_ESCALATION:
        issues.append(
            f"Case {order_id}: trusted refund status is ESCALATION_REQUIRED but decision is "
            f"{decision.value}."
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


def _validate_customer_response(result: AgentResult, state: AgentState) -> list[str]:
    issues: list[str] = []
    response = result.customer_response.lower()

    has_trusted_approval = any(
        isinstance(case.refund_result, dict) and case.refund_result.get("status") == "APPROVED"
        for case in state.cases.values()
    )
    if not has_trusted_approval and any(phrase in response for phrase in REFUND_SUCCESS_PHRASES):
        issues.append(
            "customer_response claims a refund succeeded without a trusted APPROVED result."
        )

    for pattern in DISCLOSURE_PATTERNS:
        if pattern.search(result.customer_response):
            issues.append(
                f"customer_response discloses internal risk/profile details "
                f"(pattern {pattern.pattern!r})."
            )

    return issues
