"""Mutable per-run state owned by the runtime (spec section 7).

State stores trusted tool results and progress only. It is not a policy
engine: business truth comes from the supplied tools.

Authority split (Milestone 2):
- `AgentState.tool_history` is the authoritative trusted execution history
  used by the runtime and the validator.
- `app.tracing` records are developer-presentation views derived from it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.messages import Message
from app.schemas import AgentResult, Decision


class RuntimeStatus(str, Enum):
    """Internal runtime status while a run is in flight."""

    RUNNING = "RUNNING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    COMPLETED = "COMPLETED"
    FAILED_SAFE = "FAILED_SAFE"


class ToolInteractionOutcome(str, Enum):
    """How a model-requested tool interaction ended."""

    EXECUTED = "EXECUTED"
    CACHED = "CACHED"
    BLOCKED = "BLOCKED"
    BUSINESS_ERROR = "BUSINESS_ERROR"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    SYSTEM_FAILURE = "SYSTEM_FAILURE"


class ToolInteraction(BaseModel):
    """One authoritative record of a model-requested tool interaction.

    Every interaction is recorded - including cache-served and blocked ones -
    so the runtime/validator can distinguish "never checked" from a trusted
    terminal error such as ORDER_NOT_FOUND.
    """

    step: int
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    outcome: ToolInteractionOutcome
    #: Trusted result payload for EXECUTED / CACHED / BUSINESS_ERROR.
    result: dict[str, Any] | None = None
    #: Guardrail reason code or business error code, when applicable.
    reason_code: str | None = None
    #: Measured duration of the interaction (execution or cache service).
    duration_ms: float | None = None


class CaseState(BaseModel):
    """Trusted evidence collected for one order."""

    order_id: str
    reason: str | None = None
    verified_order: dict[str, Any] | None = None
    verified_user: dict[str, Any] | None = None
    policy_result: dict[str, Any] | None = None
    refund_result: dict[str, Any] | None = None
    decision: Decision | None = None
    #: Trusted terminal business error for this order (e.g. ORDER_NOT_FOUND).
    #: Once set, further order-scoped tool calls for it are stopped.
    terminal_error: str | None = None


class AgentState(BaseModel):
    """Full runtime state for one agent run (short-term, in-memory only)."""

    messages: list[Message] = Field(default_factory=list)
    cases: dict[str, CaseState] = Field(default_factory=dict)
    tool_history: list[ToolInteraction] = Field(default_factory=list)
    sentiment: str | None = None
    urgency: str | None = None
    step_count: int = 0
    status: RuntimeStatus = RuntimeStatus.RUNNING
    final_result: AgentResult | None = None
    failure_reason: str | None = None
