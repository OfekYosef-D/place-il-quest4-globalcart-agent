"""Mutable per-run state owned by the runtime (spec section 7).

State stores trusted tool results and progress only. It is not a policy
engine: business truth comes from the supplied tools.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas import AgentResult, Decision


class RuntimeStatus(str, Enum):
    """Internal runtime status while a run is in flight."""

    RUNNING = "RUNNING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    COMPLETED = "COMPLETED"
    FAILED_SAFE = "FAILED_SAFE"


class CaseState(BaseModel):
    """Trusted evidence collected for one order."""

    order_id: str
    reason: str | None = None
    verified_order: dict[str, Any] | None = None
    verified_user: dict[str, Any] | None = None
    policy_result: dict[str, Any] | None = None
    refund_result: dict[str, Any] | None = None
    decision: Decision | None = None


class AgentState(BaseModel):
    """Full runtime state for one agent run."""

    messages: list[dict[str, Any]] = Field(default_factory=list)
    cases: dict[str, CaseState] = Field(default_factory=dict)
    tool_history: list[dict[str, Any]] = Field(default_factory=list)
    sentiment: str | None = None
    urgency: str | None = None
    step_count: int = 0
    status: RuntimeStatus = RuntimeStatus.RUNNING
    final_result: AgentResult | None = None
    failure_reason: str | None = None
