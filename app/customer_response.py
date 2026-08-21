"""Deterministic customer-facing rendering for terminal business outcomes.

Business truth is language-neutral structured data. Once a case is terminally
resolved, customer-facing action claims are rendered from that trusted outcome
instead of asking a free-form model response to restate money/status facts.

The LLM still owns understanding, tool selection, reasoning, and clarification.
This module owns only terminal presentation facts. That makes safety independent
of wording variants and keeps the same guarantees in English and Hebrew.
"""

from __future__ import annotations

import re

from app.messages import CanonicalMessage
from app.schemas import AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState

_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")


def latest_customer_language(state: AgentState) -> str:
    """Return the supported presentation language for the latest user turn."""
    for message in reversed(state.messages):
        if isinstance(message, CanonicalMessage) and message.role == "user":
            return "he" if _HEBREW_RE.search(message.content) else "en"
    return "en"


def render_terminal_customer_response(
    result: AgentResult, state: AgentState
) -> str | None:
    """Render terminal case outcomes, or None when free-form clarification is needed.

    COMPLETED turns with case outcomes are fully deterministic. NEEDS_CLARIFICATION
    remains conversational because no irreversible business outcome has been
    established yet. FAILED_SAFE has its own deterministic path in agent.py.
    """
    if result.status is not FinalStatus.COMPLETED or not result.action_taken.cases:
        return None

    language = latest_customer_language(state)
    return "\n".join(
        _render_case(case, language) for case in result.action_taken.cases
    )


def _render_case(case: CaseResult, language: str) -> str:
    if language == "he":
        return _render_case_he(case)
    return _render_case_en(case)


def _render_case_en(case: CaseResult) -> str:
    prefix = f"Order {case.order_id}: "
    if case.decision is Decision.AUTO_REFUND_APPROVED:
        amount = _format_amount(case.refund_amount)
        return (
            f"{prefix}a refund of {amount} was approved "
            f"(refund ID {case.refund_id})."
        )
    if case.decision is Decision.HUMAN_ESCALATION:
        return f"{prefix}additional review is required. No refund has been issued."
    if case.decision is Decision.REJECTED:
        return prefix + _rejection_text_en(case.policy_verdict)
    if case.error_code == "ORDER_NOT_FOUND":
        return f"{prefix}we could not verify this order. Please confirm the order number."
    return f"{prefix}no action was completed based on the available verified information."


def _render_case_he(case: CaseResult) -> str:
    prefix = f"הזמנה {case.order_id}: "
    if case.decision is Decision.AUTO_REFUND_APPROVED:
        amount = _format_amount(case.refund_amount)
        return (
            f"{prefix}אושר החזר כספי בסך {amount} "
            f"(מספר החזר {case.refund_id})."
        )
    if case.decision is Decision.HUMAN_ESCALATION:
        return f"{prefix}נדרשת בדיקה נוספת. לא בוצע החזר כספי."
    if case.decision is Decision.REJECTED:
        return prefix + _rejection_text_he(case.policy_verdict)
    if case.error_code == "ORDER_NOT_FOUND":
        return f"{prefix}לא הצלחנו לאמת את ההזמנה. נא לוודא את מספר ההזמנה."
    return f"{prefix}לא בוצעה פעולה על סמך המידע המאומת הזמין."


def _rejection_text_en(verdict: str | None) -> str:
    if verdict == "OUTSIDE_RETURN_WINDOW":
        return "the request was rejected because the order is outside the return window."
    if verdict == "NON_RETURNABLE_CATEGORY":
        return "the request was rejected because this category is non-returnable."
    if verdict == "ORDER_NOT_REFUNDABLE":
        return "the request was rejected because the order status is not refundable through this flow."
    return "the request was rejected under the verified return-policy outcome."


def _rejection_text_he(verdict: str | None) -> str:
    if verdict == "OUTSIDE_RETURN_WINDOW":
        return "הבקשה נדחתה משום שההזמנה מחוץ לחלון ההחזרות."
    if verdict == "NON_RETURNABLE_CATEGORY":
        return "הבקשה נדחתה משום שהקטגוריה אינה ניתנת להחזרה."
    if verdict == "ORDER_NOT_REFUNDABLE":
        return "הבקשה נדחתה משום שמצב ההזמנה אינו ניתן להחזר במסגרת התהליך הזה."
    return "הבקשה נדחתה בהתאם לתוצאת מדיניות ההחזרות המאומתת."


def _format_amount(value: float | None) -> str:
    """Format a trusted approved amount without inventing a fallback value."""
    if value is None:
        return "an unspecified amount"
    return f"${value:,.2f}"
