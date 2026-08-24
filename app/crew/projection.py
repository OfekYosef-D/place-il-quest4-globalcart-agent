"""Deterministic Stage 2 handoff projection from trusted tool evidence."""

from __future__ import annotations

from typing import Any

from app.crew.contracts import DecisionHandoff, RiskReport
from app.crew.runtime import SpecialistRun


class HandoffProjectionError(RuntimeError):
    pass


def latest_result(run: SpecialistRun, tool_name: str) -> dict[str, Any] | None:
    for interaction in reversed(run.interactions):
        if interaction.tool_name == tool_name and interaction.outcome in {"EXECUTED", "BUSINESS_ERROR"}:
            return interaction.result
    return None


def latest_arguments(run: SpecialistRun, tool_name: str) -> dict[str, Any] | None:
    for interaction in reversed(run.interactions):
        if interaction.tool_name == tool_name and interaction.outcome in {"EXECUTED", "BUSINESS_ERROR"}:
            return interaction.arguments
    return None


def project_risk_report(run: SpecialistRun) -> RiskReport:
    """Build Agent 1 handoff only from audit_fraud_risk's trusted payload."""
    audit = latest_result(run, "audit_fraud_risk")
    if not isinstance(audit, dict):
        raise HandoffProjectionError("Researcher produced no fraud audit result.")
    if "error" in audit:
        raise HandoffProjectionError(f"Fraud audit failed: {audit['error']}")
    try:
        return RiskReport.model_validate(audit)
    except Exception as exc:
        raise HandoffProjectionError(f"Fraud audit payload is not a valid RiskReport: {exc}") from exc


def project_decision(
    run: SpecialistRun,
    risk: RiskReport,
    *,
    requested_amount: float,
    order_status: str,
) -> DecisionHandoff:
    """Build Agent 2 handoff from trusted policy/refund results + RiskReport."""
    policy = latest_result(run, "check_return_policy")
    if not isinstance(policy, dict) or "error" in policy:
        raise HandoffProjectionError("Decision agent produced no valid policy result.")

    refund = latest_result(run, "process_refund")
    if risk.blocks_automatic_refund:
        refund_status = "ESCALATION_REQUIRED"
        approved_amount = 0.0
        refund_id = None
        rationale = [
            f"Stage 2 fraud audit is {risk.risk_score}/{risk.risk_band} and blocks automatic refund.",
            f"Return-policy verdict is {policy.get('verdict') or 'UNKNOWN'}; policy eligibility does not override the fraud guardrail.",
        ]
    elif not policy.get("eligible"):
        refund_status = "REJECTED"
        approved_amount = 0.0
        refund_id = None
        rationale = [policy.get("explanation", "Return policy rejected the claim.")]
    elif isinstance(refund, dict) and "error" not in refund:
        refund_status = refund.get("status", "NOT_ATTEMPTED")
        approved_amount = float(refund.get("approved_amount") or 0.0)
        refund_id = refund.get("refund_id")
        rationale = list(refund.get("reasons") or [])
    else:
        refund_status = "NOT_ATTEMPTED"
        approved_amount = 0.0
        refund_id = None
        rationale = ["Eligible policy result exists, but no trusted refund result was produced."]

    triggered_rule_ids = [
        str(rule.get("rule_id"))
        for rule in risk.triggered_rules
        if isinstance(rule, dict) and rule.get("rule_id")
    ]
    return DecisionHandoff(
        order_id=risk.order_id,
        user_id=risk.user_id,
        policy_verdict=str(policy.get("verdict") or "UNKNOWN"),
        requested_amount=requested_amount,
        refund_status=refund_status,
        approved_amount=approved_amount,
        refund_id=refund_id,
        applicable_policies=list(policy.get("applicable_policies") or []),
        risk_score=risk.risk_score,
        risk_band=risk.risk_band,
        triggered_rule_ids=triggered_rule_ids,
        prior_fraud_flags=int(risk.evidence.get("prior_fraud_flags") or 0),
        order_status=order_status,
        rationale=rationale,
    )
