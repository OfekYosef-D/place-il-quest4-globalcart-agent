"""Deterministic customer-facing rendering from trusted runtime state.

Business truth is language-neutral structured data. Once a case is resolved,
customer-facing action claims are rendered from that trusted outcome instead of
asking a free-form model response to restate money/status facts.

Clarification turns are also rendered by the runtime. The LLM still decides
that clarification is needed, but the delivered question is derived from
structural state (missing order identifier or unresolved return reason) so a
nonterminal response cannot smuggle in an invented refund, internal risk data,
or an unsupported future promise.

The LLM still owns understanding, tool selection, reasoning, and the decision
to request clarification. This module owns only customer presentation safety.
"""

from __future__ import annotations

import re

from app.messages import CanonicalMessage
from app.schemas import AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, CaseState

_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_ORDER_ID_RE = re.compile(r"\bORD-[A-Z0-9-]+\b", re.IGNORECASE)


def latest_customer_language(state: AgentState) -> str:
    """Return the supported presentation language for the latest user turn."""
    for message in reversed(state.messages):
        if isinstance(message, CanonicalMessage) and message.role == "user":
            return "he" if _HEBREW_RE.search(message.content) else "en"
    return "en"


def render_terminal_customer_response(
    result: AgentResult, state: AgentState
) -> str | None:
    """Render safe customer text for resolved or clarification outcomes.

    COMPLETED case outcomes are fully deterministic. NEEDS_CLARIFICATION uses a
    deterministic, targeted question and includes only already-resolved case
    facts. FAILED_SAFE has its own deterministic path in ``app.agent``.
    """
    language = latest_customer_language(state)

    if result.status is FinalStatus.COMPLETED:
        if not result.action_taken.cases:
            return None
        return "\n".join(
            _render_case(case, language) for case in result.action_taken.cases
        )

    if result.status is FinalStatus.NEEDS_CLARIFICATION:
        return _render_clarification(result, state, language)

    return None


def _render_clarification(
    result: AgentResult, state: AgentState, language: str
) -> str:
    """Render one safe clarification question plus any trusted resolved facts."""
    resolved_lines: list[str] = []
    unresolved_ids: list[str] = []

    for case_result in result.action_taken.cases:
        case_state = state.cases.get(case_result.order_id)
        if _case_state_resolved(case_state):
            # The projector replaces resolved case_result values with canonical
            # trusted fields before this renderer is called.
            resolved_lines.append(_render_case(case_result, language))
        elif case_state is not None:
            unresolved_ids.append(case_result.order_id)

    latest_text = _latest_customer_text(state)
    mentioned_ids = _unique_order_ids(latest_text)
    for order_id in mentioned_ids:
        if order_id not in unresolved_ids and not _case_state_resolved(
            state.cases.get(order_id)
        ):
            unresolved_ids.append(order_id)

    question = _clarification_question(
        language,
        unresolved_ids=unresolved_ids,
        order_id_was_mentioned=bool(mentioned_ids),
    )
    return "\n".join([*resolved_lines, question])


def _clarification_question(
    language: str,
    *,
    unresolved_ids: list[str],
    order_id_was_mentioned: bool,
) -> str:
    """Ask exactly one targeted, non-business-claim clarification question."""
    if unresolved_ids:
        orders = ", ".join(unresolved_ids)
        if language == "he":
            return f"מה הסיבה לבקשת ההחזר או ההחזרה עבור הזמנה {orders}?"
        return f"What is the reason for the refund or return request for order {orders}?"

    if not order_id_was_mentioned:
        if language == "he":
            return "מה מספר ההזמנה שברצונך שנבדוק?"
        return "What is the order number you would like us to review?"

    # Defensive fallback. Reaching this branch means the model requested
    # clarification although the latest turn contained an identifier and no
    # unresolved case is represented in trusted state. Keep the response safe
    # and ask only for the missing case detail rather than inventing an action.
    if language == "he":
        return "איזה פרט נוסף לגבי בקשת ההחזר או ההחזרה צריך שנבדוק?"
    return "What additional detail about the refund or return request should we review?"


def _latest_customer_text(state: AgentState) -> str:
    for message in reversed(state.messages):
        if isinstance(message, CanonicalMessage) and message.role == "user":
            return message.content
    return ""


def _unique_order_ids(text: str) -> list[str]:
    seen: set[str] = set()
    order_ids: list[str] = []
    for match in _ORDER_ID_RE.finditer(text):
        order_id = match.group(0).upper()
        if order_id not in seen:
            seen.add(order_id)
            order_ids.append(order_id)
    return order_ids


def _case_state_resolved(case: CaseState | None) -> bool:
    if case is None:
        return False
    if case.refund_result is not None or case.terminal_error:
        return True
    policy = case.policy_result
    return isinstance(policy, dict) and policy.get("eligible") is False


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
