"""Regression coverage for intake-only turns and ungrounded support cases."""

from __future__ import annotations

from types import SimpleNamespace

from app.config import Settings
from app.crew.config import CrewSettings
from app.crew.orchestrator import GlobalCartCrew
from app.crew.tools import CrewToolKits
from tests.unit.fakes import FakeProvider, intake_response


def _schema(name: str) -> dict:
    return {"name": name, "description": name, "input_schema": {"type": "object", "properties": {}}}


def _crew(provider: FakeProvider) -> GlobalCartCrew:
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
        raise AssertionError(f"No specialist tool may run before grounding is complete: {kwargs}")

    module = SimpleNamespace(
        RESEARCHER_TOOLS=[schemas[n] for n in names[:3]],
        DECISION_TOOLS=[schemas[n] for n in names[3:5]],
        COMMS_TOOLS=[schemas[n] for n in names[5:]],
        TOOL_REGISTRY={name: forbidden for name in names},
    )
    settings = Settings(llm_provider="groq", llm_model="fake", groq_api_key="x", llm_max_retries=0)
    return GlobalCartCrew(settings, CrewSettings(specialist_max_steps=4), provider, CrewToolKits(module))


def test_greeting_is_understood_before_any_specialist_or_tool_runs():
    provider = FakeProvider(
        [
            intake_response(
                intent="GREETING",
                support_goal="NONE",
                case_reason="unknown",
                issue_summary="Simple greeting.",
            )
        ]
    )
    run = _crew(provider).handle_customer_message("hi")

    assert run.result.status == "COMPLETED"
    assert run.result.stop_reason == "INTAKE_GREETING"
    assert "how can i help" in run.result.communication.customer_response.lower()
    assert run.trace.researcher is None
    assert run.trace.decision is None
    assert run.trace.comms is None
    assert len(provider.calls) == 1
    assert provider.calls[0][1] is None  # intake has no tools


def test_support_case_without_order_asks_for_id_without_starting_the_crew():
    provider = FakeProvider(
        [
            intake_response(
                support_goal="RESOLVE_ISSUE",
                case_reason="damaged_on_arrival",
                reason_evidence="arrived damaged",
            )
        ]
    )
    run = _crew(provider).handle_customer_message("My package arrived damaged. Can you help?")

    assert run.result.status == "NEEDS_CLARIFICATION"
    assert run.result.stop_reason == "ORDER_ID_REQUIRED"
    assert "ORD-1234" in run.result.communication.customer_response
    assert run.trace.researcher is None
    assert run.trace.decision is None
    assert run.trace.comms is None
    assert len(provider.calls) == 1


def test_even_wrong_intake_classification_cannot_bootstrap_a_hallucinated_order():
    provider = FakeProvider(
        [
            intake_response(
                intent="SUPPORT_CASE",
                support_goal="REFUND",
                case_reason="unknown",
                issue_summary="Classifier incorrectly thinks this is a refund case.",
            )
        ]
    )
    run = _crew(provider).handle_customer_message("hi")

    assert run.result.status == "NEEDS_CLARIFICATION"
    assert run.result.stop_reason == "ORDER_ID_REQUIRED"
    assert run.trace.researcher is None
    assert len(provider.calls) == 1
