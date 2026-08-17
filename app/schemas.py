"""Typed contracts for the agent's structured final output (spec section 10).

The Quest requires three parseable top-level fields: `reasoning_chain`,
`action_taken`, and `customer_response`. `cases` is always a list so
single-order and multi-order runs share one schema.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Decision(str, Enum):
    """Business decision for a single order case."""

    AUTO_REFUND_APPROVED = "AUTO_REFUND_APPROVED"
    REJECTED = "REJECTED"
    HUMAN_ESCALATION = "HUMAN_ESCALATION"
    NO_ACTION = "NO_ACTION"


class FinalStatus(str, Enum):
    """Terminal status of an agent run."""

    COMPLETED = "COMPLETED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    FAILED_SAFE = "FAILED_SAFE"


class CaseResult(BaseModel):
    """Outcome for one order, grounded in trusted tool evidence."""

    order_id: str
    decision: Decision
    refund_amount: float | None = None
    refund_id: str | None = None
    policy_verdict: str | None = None
    error_code: str | None = None
    escalation_reasons: list[str] = Field(default_factory=list)


class ActionTaken(BaseModel):
    """What the agent did: tools called and per-case outcomes."""

    tools_called: list[str] = Field(default_factory=list)
    cases: list[CaseResult] = Field(default_factory=list)


class AgentResult(BaseModel):
    """Final structured output; must always parse or the run fails safe.

    `reasoning_chain` must be explicitly supplied with at least one step and
    `customer_response` must be non-empty — a final output without them is
    malformed and fails validation rather than silently defaulting.
    """

    status: FinalStatus
    reasoning_chain: list[str] = Field(min_length=1)
    action_taken: ActionTaken
    customer_response: str = Field(min_length=1)
