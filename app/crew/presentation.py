"""Safe presentation helpers for the Stage 2 demo UI.

The UI exposes observable execution facts (tool calls, trusted handoffs,
guardrail outcomes, and final status). It intentionally does not expose private
model chain-of-thought or hidden reasoning tokens.
"""

from __future__ import annotations

from typing import Any

from app.crew.orchestrator import CrewRun
from app.crew.runtime import SpecialistRun


_AGENT_LABELS = {
    "researcher": "Researcher & Fraud Auditor",
    "decision": "Decision Maker / Ops Lead",
    "comms": "Communications & Escalation",
}


def present_crew_run(run: CrewRun) -> dict[str, Any]:
    """Convert a CrewRun into a JSON-safe, customer-demo-friendly payload."""
    result = run.result
    return {
        "status": result.status,
        "stop_reason": result.stop_reason,
        "customer_response": result.communication.customer_response,
        "risk_report": result.risk_report.model_dump(mode="json") if result.risk_report else None,
        "decision": result.decision.model_dump(mode="json") if result.decision else None,
        "communication": result.communication.model_dump(mode="json"),
        "agents": [
            _present_agent("researcher", run.trace.researcher),
            _present_agent("decision", run.trace.decision),
            _present_agent("comms", run.trace.comms),
        ],
    }


def _present_agent(role: str, specialist: SpecialistRun | None) -> dict[str, Any]:
    if specialist is None:
        return {
            "role": role,
            "label": _AGENT_LABELS[role],
            "status": "skipped",
            "steps": [],
            "failure": None,
        }

    steps: list[dict[str, Any]] = []
    for interaction in specialist.interactions:
        steps.append(
            {
                "step": interaction.step,
                "tool": interaction.tool_name,
                "outcome": interaction.outcome,
                "arguments": interaction.arguments,
                "result": _compact_result(interaction.result),
                "guardrail_reason": interaction.reason if interaction.outcome == "BLOCKED" else None,
            }
        )

    status = "failed" if specialist.failure_reason else "completed"
    return {
        "role": role,
        "label": _AGENT_LABELS[role],
        "status": status,
        "steps": steps,
        "failure": specialist.failure_reason,
    }


def _compact_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return result

    preferred = (
        "error",
        "message",
        "status",
        "verdict",
        "eligible",
        "risk_score",
        "risk_band",
        "blocks_automatic_refund",
        "escalation_required",
        "channel_id",
        "severity",
        "delivered",
        "transport",
        "approved_amount",
        "refund_id",
    )
    compact = {key: result[key] for key in preferred if key in result}
    if compact:
        return compact
    return result
