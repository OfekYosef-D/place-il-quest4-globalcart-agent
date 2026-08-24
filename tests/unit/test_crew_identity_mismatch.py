"""Regression coverage for claimed user/order identity mismatches."""

from __future__ import annotations

from types import SimpleNamespace

from app.config import Settings
from app.crew.config import CrewSettings
from app.crew.orchestrator import GlobalCartCrew
from app.crew.tools import CrewToolKits
from tests.unit.fakes import FakeProvider, final_response, tool_call_response


def _schema(name: str) -> dict:
    return {"name": name, "description": name, "input_schema": {"type": "object", "properties": {}}}


def test_claimed_user_is_preserved_until_audit_reports_mismatch_even_after_premature_stop():
    calls: list[tuple[str, dict]] = []

    def order(**kwargs):
        calls.append(("get_order_details", dict(kwargs)))
        return {"order_id": "ORD-1005", "user_id": "USR-105", "status": "delivered", "total_amount": 480.0}

    def profile(**kwargs):
        calls.append(("get_user_profile", dict(kwargs)))
        return {"user_id": kwargs["user_id"], "prior_fraud_flags": 0}

    def audit(**kwargs):
        calls.append(("audit_fraud_risk", dict(kwargs)))
        return {
            "error": "USER_ORDER_MISMATCH",
            "message": "Order ORD-1005 belongs to USR-105, not USR-101.",
        }

    def forbidden_refund(**kwargs):
        calls.append(("process_refund", dict(kwargs)))
        raise AssertionError("refund must never run for an identity mismatch")

    def alert(**kwargs):
        calls.append(("send_slack_alert", dict(kwargs)))
        return {
            "delivered": True,
            "channel_id": "CH-FRAUD",
            "severity": "critical",
            "transport": "outbox",
            "message_ts": "mismatch.1",
        }

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
            "get_order_details": order,
            "get_user_profile": profile,
            "audit_fraud_risk": audit,
            "check_return_policy": lambda **kwargs: (_ for _ in ()).throw(AssertionError("decision agent must not run")),
            "process_refund": forbidden_refund,
            "get_escalation_route": lambda **kwargs: (_ for _ in ()).throw(AssertionError("mismatch uses direct security escalation")),
            "send_slack_alert": alert,
        },
    )

    provider = FakeProvider(
        [
            tool_call_response(("r1", "get_order_details", {"order_id": "ORD-1005"})),
            final_response("I have enough information."),
            tool_call_response(
                ("r2", "get_user_profile", {"user_id": "USR-101"}),
                ("r3", "audit_fraud_risk", {"order_id": "ORD-1005", "user_id": "USR-101"}),
            ),
            tool_call_response(
                (
                    "c1",
                    "send_slack_alert",
                    {
                        "channel_id": "CH-FRAUD",
                        "severity": "critical",
                        "payload": {"order_id": "ORD-1005", "claimed_user_id": "USR-101"},
                    },
                )
            ),
        ]
    )
    settings = Settings(llm_provider="groq", llm_model="fake", groq_api_key="x", llm_max_retries=0)
    crew = GlobalCartCrew(settings, CrewSettings(specialist_max_steps=5), provider, CrewToolKits(module))

    run = crew.handle_customer_message("I am USR-101 and I need a refund for order ORD-1005.")

    assert run.result.status == "ESCALATED"
    assert run.result.stop_reason == "USER_ORDER_MISMATCH"
    assert run.result.decision is None
    assert run.result.risk_report is None
    assert ("get_user_profile", {"user_id": "USR-101"}) in calls
    assert ("audit_fraud_risk", {"order_id": "ORD-1005", "user_id": "USR-101"}) in calls
    assert not any(name == "get_user_profile" and args.get("user_id") == "USR-105" for name, args in calls)
    assert not any(name == "process_refund" for name, _ in calls)
    assert sum(name == "send_slack_alert" for name, _ in calls) == 1
