"""Typed short-term customer context for multi-turn support conversations.

Only two categories survive between turns:
1. semantic intake facts (probabilistic, no business authority), and
2. literal customer-grounded identifiers/money.
Raw model reasoning and specialist message histories never become conversation
memory and are never passed between agents.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.crew.intake import CaseReason, RefundScope, SupportGoal


class PendingCustomerContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    support_goal: SupportGoal
    refund_scope: RefundScope
    case_reason: CaseReason
    reason_evidence: str | None = Field(default=None, max_length=160)
    issue_summary: str = Field(min_length=1, max_length=240)

    # These values were literally supplied by the customer on a prior turn.
    order_id: str | None = None
    user_id: str | None = None
    explicit_amount: float | None = None
