"""Multi-turn conversation tests for typed pending customer context."""

from app.crew.config import CrewSettings
from app.crew.context import PendingCustomerContext
from app.crew.conversation import ConversationSession
from app.crew.orchestrator import GlobalCartCrew
from app.crew.tools import CrewToolKits
from tests.unit.fakes import FakeProvider, intake_response, tool_call_response
from tests.unit.test_crew_stage2 import _module, _settings


def _clean_completion_script(*, intake_evidence: str):
    return [
        intake_response(reason_evidence=intake_evidence),
        tool_call_response(
            ("r1", "get_order_details", {"order_id": "ORD-1001"}),
            ("r2", "get_user_profile", {"user_id": "USR-101"}),
            ("r3", "audit_fraud_risk", {"order_id": "ORD-1001", "user_id": "USR-101"}),
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


def test_missing_order_can_be_supplied_on_next_turn_without_raw_chat_memory():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            intake_response(
                support_goal="REFUND",
                case_reason="damaged_on_arrival",
                reason_evidence="arrived damaged",
                issue_summary="Damaged package refund request.",
            ),
            *_clean_completion_script(intake_evidence="arrived damaged"),
        ]
    )
    crew = GlobalCartCrew(
        _settings(), CrewSettings(specialist_max_steps=4), provider, CrewToolKits(module)
    )
    session = ConversationSession(crew)

    first = session.handle_customer_message("My package arrived damaged. Can I get a refund?")
    assert first.result.stop_reason == "ORDER_ID_REQUIRED"
    assert session.pending is not None
    assert session.pending.order_id is None
    assert session.pending.case_reason == "damaged_on_arrival"

    second = session.handle_customer_message("ORD-1001")
    assert second.result.status == "COMPLETED"
    assert second.result.decision is not None
    assert second.result.decision.refund_status == "APPROVED"
    assert session.pending is None
    assert [name for name, _ in module.calls].count("process_refund") == 1

    # The second intake call was given semantic context separately from tools.
    second_intake_messages, second_intake_tools = provider.calls[1]
    assert second_intake_tools is None
    assert any("Prior unresolved semantic context" in message.content for message in second_intake_messages)


def test_greeting_does_not_create_case_memory_for_next_turn():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            intake_response(
                intent="GREETING",
                support_goal="NONE",
                case_reason="unknown",
                issue_summary="Greeting.",
            ),
            intake_response(
                intent="UNCLEAR",
                support_goal="NONE",
                case_reason="unknown",
                issue_summary="Only an order id was supplied without a request.",
            ),
        ]
    )
    session = ConversationSession(
        GlobalCartCrew(_settings(), CrewSettings(specialist_max_steps=4), provider, CrewToolKits(module))
    )

    first = session.handle_customer_message("hi")
    assert first.result.stop_reason == "INTAKE_GREETING"
    assert session.pending is None

    second = session.handle_customer_message("ORD-1001")
    assert second.result.stop_reason == "INTAKE_UNCLEAR"
    assert session.pending is None
    assert module.calls == []


def test_latest_grounded_order_replaces_prior_order_in_pending_context():
    module = _module(high_risk=False)
    provider = FakeProvider(_clean_completion_script(intake_evidence="damaged"))
    session = ConversationSession(
        GlobalCartCrew(_settings(), CrewSettings(specialist_max_steps=4), provider, CrewToolKits(module))
    )
    session.pending = PendingCustomerContext(
        support_goal="REFUND",
        case_reason="damaged_on_arrival",
        reason_evidence="damaged",
        issue_summary="Earlier damaged-item request.",
        order_id="ORD-1005",
    )

    run = session.handle_customer_message(
        "Actually use ORD-1001. It arrived damaged; refund the full order."
    )

    assert run.result.status == "COMPLETED"
    order_calls = [args for name, args in module.calls if name == "get_order_details"]
    assert order_calls == [{"order_id": "ORD-1001"}]
    assert run.trace.grounded is not None
    assert run.trace.grounded.order_id == "ORD-1001"
