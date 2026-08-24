"""Strict Stage 2 contracts passed between specialist agents.

Agents do not receive one another's conversation history. They receive these
validated artifacts plus the minimum customer context needed for their role.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


RiskBand = Literal["low", "medium", "high"]
RefundStatus = Literal["APPROVED", "REJECTED", "ESCALATION_REQUIRED", "NOT_ATTEMPTED"]
CrewStatus = Literal["COMPLETED", "NEEDS_CLARIFICATION", "ESCALATED", "FAILED_SAFE"]


class RiskReport(BaseModel):
    """Trusted Agent 1 -> Agent 2 handoff, preserving the deterministic audit."""

    model_config = ConfigDict(extra="forbid")

    order_id: str
    user_id: str
    risk_score: int = Field(ge=0, le=100)
    risk_band: RiskBand
    action_hint: str | None = None
    triggered_rules: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    blocks_automatic_refund: bool
    requires_security_channel: bool
    rulebook_version: str | None = None


class DecisionHandoff(BaseModel):
    """Trusted Agent 2 -> Agent 3 handoff."""

    model_config = ConfigDict(extra="forbid")

    order_id: str
    user_id: str
    policy_verdict: str
    requested_amount: float = Field(ge=0)
    refund_status: RefundStatus
    approved_amount: float = Field(ge=0)
    refund_id: str | None = None
    applicable_policies: list[str] = Field(default_factory=list)
    risk_score: int = Field(ge=0, le=100)
    risk_band: RiskBand
    triggered_rule_ids: list[str] = Field(default_factory=list)
    prior_fraud_flags: int = Field(default=0, ge=0)
    order_status: str
    rationale: list[str] = Field(default_factory=list)


class CommunicationResult(BaseModel):
    """Agent 3 terminal artifact."""

    model_config = ConfigDict(extra="forbid")

    customer_response: str = Field(min_length=1)
    escalation_required: bool
    channel_id: str | None = None
    severity: str | None = None
    alert_delivered: bool = False
    alert_transport: str | None = None
    message_ts: str | None = None


class CrewResult(BaseModel):
    """Public Stage 2 run result plus inspectable handoffs."""

    model_config = ConfigDict(extra="forbid")

    status: CrewStatus
    risk_report: RiskReport | None = None
    decision: DecisionHandoff | None = None
    communication: CommunicationResult
    stop_reason: str | None = None
