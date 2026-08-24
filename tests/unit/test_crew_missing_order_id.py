"""Regression coverage for customer messages that do not ground an order id."""

from __future__ import annotations

from types import SimpleNamespace

from app.config import Settings
from app.crew.config import CrewSettings
from app.crew.orchestrator import GlobalCartCrew
from app.crew.tools import CrewToolKits
from tests.unit.fakes import FakeProvider


def _schema(name: str) -> dict:
    return {"name": name, "description": name, "input_schema": {"type": "object", "properties": {}}}


def test_missing_order_id_never_reaches_llm_or_tools():
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

    def forbidden(**kwargs):
        raise AssertionError(f"No tool may run without a customer-grounded order id: {kwargs}")

    module = SimpleNamespace(
        RESEARCHER_TOOLS=[schemas[n] for n in names[:3]],
        DECISION_TOOLS=[schemas[n] for n in names[3:5]],
        COMMS_TOOLS=[schemas[n] for n in names[5:]],
        TOOL_REGISTRY={name: forbidden for name in names},
    )

    provider = FakeProvider([])
    settings = Settings(llm_provider="groq", llm_model="fake", groq_api_key="x", llm_max_retries=0)
    crew = GlobalCartCrew(settings, CrewSettings(specialist_max_steps=4), provider, CrewToolKits(module))

    run = crew.handle_customer_message("hi")

    assert run.result.status == "NEEDS_CLARIFICATION"
    assert run.result.stop_reason == "ORDER_ID_REQUIRED"
    assert "ORD-1234" in run.result.communication.customer_response
    assert run.trace.researcher is None
    assert run.trace.decision is None
    assert run.trace.comms is None
    assert provider.calls == []
