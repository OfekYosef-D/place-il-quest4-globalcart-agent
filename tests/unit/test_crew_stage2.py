"""Deterministic tests for Stage 2 handoffs, authority and routing."""

from __future__ import annotations

from types import SimpleNamespace

from app.config import Settings
from app.crew.config import CrewSettings
from app.crew.orchestrator import GlobalCartCrew
from app.crew.tools import CrewToolKits
from tests.unit.fakes import FakeProvider, tool_call_response


def _schema(name: str) -> dict:
    return {"name": name, "description": name, "input_schema": {"type": "object", "properties": {}}}


def _module(*, high_risk: bool):
    order_total = 480.0 if high_risk else 35.0
    user_id = "USR-105" if high_risk else "USR-101"
    calls: list[tuple[str, dict]] = []

    def record(name, result):
        def fn(**kwargs):
            calls.append((name, dict(kwargs)))
            return result
        return fn

    order = {
        "order_id": "ORD-1005" if high_risk else "ORD-1001",
        "user_id": user_id,
        "status": "delivered",
        "total_amount": order_total,
    }
    profile = {"user_id": user_id, "prior_fraud_flags": 1 if high_risk else 0}
    audit = {
        "order_id": order["order_id"],
        "user_id": user_id,
        "risk_score": 90 if high_risk else 0,
        "risk_band": "high" if high_risk else "low",
        "action_hint": "manual review" if high_risk else "normal handling",
        "triggered_rules": ([{"rule_id": "FR-01", "name": "repeat", "weight": 25, "why": "history"}] if high_risk else []),
        "evidence": {
            "order_total_usd": order_total,
            "order_status": "delivered",
            "prior_fraud_flags": 1 if high_risk else 0,
        },
        "blocks_automatic_refund": high_risk,
        "requires_security_channel": high_risk,
        "rulebook_version": "test",
    }
    policy = {
        "order_id": order["order_id"],
        "user_id": user_id,
        "eligible": True,
        "verdict": "ELIGIBLE",
        "applicable_policies": ["POL-REF-01"],
        "explanation": "Eligible under test policy.",
    }
    approved = {
        "status": "APPROVED",
        "order_id": order["order_id"],
        "user_id": user_id,
        "requested_amount": order_total,
        "approved_amount": order_total,
        "refund_id": "RF-TEST",
        "applicable_policies": ["POL-REF-01"],
        "reasons": ["within authority"],
    }

    def route(**kwargs):
        calls.append(("get_escalation_route", dict(kwargs)))
        if high_risk:
            return {
                "escalation_required": True,
                "channel_id": "CH-FRAUD",
                "channel": "#fraud-security",
                "severity": "critical",
            }
        return {"escalation_required": False, "channel_id": None, "severity": None}

    alert = record(
        "send_slack_alert",
        {
            "delivered": True,
            "channel_id": "CH-FRAUD",
            "severity": "critical",
            "transport": "outbox",
            "message_ts": "test.1",
        },
    )

    names = [
        "get_order_details",
        "get_user_profile",
        "audit_fraud_risk",
        "check_return_policy",
        "process_refund",
        "get_escalation_route",
        "send_slack_alert",
    ]
    schemas = {name: _schema(name) for name in names}
    module = SimpleNamespace(
        RESEARCHER_TOOLS=[schemas[n] for n in names[:3]],
        DECISION_TOOLS=[schemas[n] for n in names[3:5]],
        COMMS_TOOLS=[schemas[n] for n in names[5:]],
        TOOL_REGISTRY={
            "get_order_details": record("get_order_details", order),
            "get_user_profile": record("get_user_profile", profile),
            "audit_fraud_risk": record("audit_fraud_risk", audit),
            "check_return_policy": record("check_return_policy", policy),
            "process_refund": record("process_refund", approved),
            "get_escalation_route": route,
            "send_slack_alert": alert,
        },
    )
    module.calls = calls
    return module


def _settings() -> Settings:
    return Settings(llm_provider="groq", llm_model="fake", groq_api_key="x", llm_max_retries=0)


def test_high_risk_handoff_blocks_refund_and_sends_one_fraud_alert():
    module = _module(high_risk=True)
    provider = FakeProvider(
        [
            tool_call_response(
                ("r1", "get_order_details", {"order_id": "ORD-1005"}),
                ("r2", "get_user_profile", {"user_id": "USR-105"}),
                ("r3", "audit_fraud_risk", {"order_id": "ORD-1005", "user_id": "USR-105"}),
            ),
            tool_call_response(("d1", "check_return_policy", {"order_id": "ORD-1005", "reason": "damaged_on_arrival"})),
            tool_call_response(
                (
                    "c1",
                    "get_escalation_route",
                    {
                        "risk_band": "high",
                        "requested_amount": 480.0,
                        "prior_fraud_flags": 1,
                        "order_status": "delivered",
                        "verdict": "ELIGIBLE",
                    },
                ),
                (
                    "c2",
                    "send_slack_alert",
                    {
                        "channel_id": "CH-FRAUD",
                        "severity": "critical",
                        "payload": {
                            "order_id": "ORD-1005",
                            "user_id": "USR-105",
                            "risk_score": 90,
                            "risk_band": "high",
                            "requested_amount": 480.0,
                        },
                    },
                ),
            ),
        ]
    )
    crew = GlobalCartCrew(_settings(), CrewSettings(specialist_max_steps=4), provider, CrewToolKits(module))

    run = crew.handle_customer_message(
        "This is USR-105, order ORD-1005. The screen was smashed. Refund me the full 480 dollars."
    )

    assert run.result.status == "ESCALATED"
    assert run.result.risk_report is not None
    assert run.result.risk_report.risk_score == 90
    assert run.result.decision is not None
    assert run.result.decision.refund_status == "ESCALATION_REQUIRED"
    assert not any(name == "process_refund" for name, _ in module.calls)
    assert sum(name == "send_slack_alert" for name, _ in module.calls) == 1
    assert "fraud" not in run.result.communication.customer_response.lower()


def test_clean_case_approves_once_and_does_not_alert():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            tool_call_response(
                ("r1", "get_order_details", {"order_id": "ORD-1001"}),
                ("r2", "get_user_profile", {"user_id": "USR-101"}),
                ("r3", "audit_fraud_risk", {"order_id": "ORD-1001"}),
            ),
            tool_call_response(
                ("d1", "check_return_policy", {"order_id": "ORD-1001", "reason": "damaged_on_arrival"}),
                ("d2", "process_refund", {"order_id": "ORD-1001", "amount": 35.0, "reason": "damaged_on_arrival"}),
            ),
            tool_call_response(
                (
                    "c1",
                    "get_escalation_route",
                    {
                        "risk_band": "low",
                        "requested_amount": 35.0,
                        "prior_fraud_flags": 0,
                        "order_status": "delivered",
                        "verdict": "ELIGIBLE",
                    },
                )
            ),
        ]
    )
    crew = GlobalCartCrew(_settings(), CrewSettings(specialist_max_steps=4), provider, CrewToolKits(module))

    run = crew.handle_customer_message("Order ORD-1001 arrived damaged. Please refund 35 dollars.")

    assert run.result.status == "COMPLETED"
    assert run.result.decision is not None
    assert run.result.decision.refund_status == "APPROVED"
    assert run.result.decision.approved_amount == 35.0
    assert sum(name == "process_refund" for name, _ in module.calls) == 1
    assert not any(name == "send_slack_alert" for name, _ in module.calls)


def test_capability_bundles_are_disjoint():
    module = _module(high_risk=False)
    kits = CrewToolKits(module)
    assert kits.researcher.tool_names == {"get_order_details", "get_user_profile", "audit_fraud_risk"}
    assert kits.decision.tool_names == {"check_return_policy", "process_refund"}
    assert kits.comms.tool_names == {"get_escalation_route", "send_slack_alert"}
    assert "process_refund" not in kits.comms.tool_names
