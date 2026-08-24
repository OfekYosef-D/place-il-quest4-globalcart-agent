"""Short-term conversation coordinator for the Stage 2 support demo.

This is not another LLM agent. It remembers only typed unresolved customer
context and hands that context back to the intake/grounding boundary on the
next turn. Specialist histories and private model text are never persisted.
"""

from __future__ import annotations

from app.crew.context import PendingCustomerContext
from app.crew.orchestrator import CrewRun, GlobalCartCrew


_CLEAR_ORDER_REASONS = {"ORDER_NOT_FOUND", "MULTIPLE_ORDER_IDS"}
_CLEAR_USER_REASONS = {"USER_NOT_FOUND", "MULTIPLE_USER_IDS"}
_CLEAR_AMOUNT_REASONS = {"MULTIPLE_REQUESTED_AMOUNTS", "REQUESTED_AMOUNT_INVALID"}


class ConversationSession:
    """One customer conversation with bounded typed pending context."""

    def __init__(self, crew: GlobalCartCrew) -> None:
        self._crew = crew
        self.pending: PendingCustomerContext | None = None

    def reset(self) -> None:
        self.pending = None

    def handle_customer_message(self, text: str) -> CrewRun:
        run = self._crew.handle_customer_message(text, prior_context=self.pending)
        self.pending = self._next_pending(run)
        return run

    @staticmethod
    def _next_pending(run: CrewRun) -> PendingCustomerContext | None:
        # Only unresolved support cases continue. Completed/escalated/fail-safe
        # cases start fresh on the next customer turn.
        if run.result.status != "NEEDS_CLARIFICATION":
            return None
        intake_trace = run.trace.intake
        if intake_trace is None or intake_trace.assessment is None:
            return None
        intake = intake_trace.assessment
        if intake.intent != "SUPPORT_CASE":
            return None

        grounded = run.trace.grounded
        order_id = grounded.order_id if grounded is not None else None
        user_id = grounded.user_id if grounded is not None else None
        amount = grounded.explicit_amount if grounded is not None else None
        reason = run.result.stop_reason or ""

        if reason in _CLEAR_ORDER_REASONS:
            order_id = None
        if reason in _CLEAR_USER_REASONS:
            user_id = None
        if reason in _CLEAR_AMOUNT_REASONS:
            amount = None

        return PendingCustomerContext(
            support_goal=intake.support_goal,
            case_reason=intake.case_reason,
            reason_evidence=intake.reason_evidence,
            issue_summary=intake.issue_summary,
            order_id=order_id,
            user_id=user_id,
            explicit_amount=amount,
        )
