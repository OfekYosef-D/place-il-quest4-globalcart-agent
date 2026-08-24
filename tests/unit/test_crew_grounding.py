"""Deterministic customer-fact grounding tests for Stage 2."""

from app.crew.config import CrewSettings
from app.crew.grounding import ground_customer_text
from app.crew.orchestrator import GlobalCartCrew
from app.crew.tools import CrewToolKits
from tests.unit.fakes import FakeProvider, intake_response, tool_call_response
from tests.unit.test_crew_stage2 import _module, _settings


def test_currency_grounder_handles_common_literal_formats_without_llm_extraction():
    assert ground_customer_text("refund $1,234.50").explicit_amount == 1234.50
    assert ground_customer_text("refund USD 480").explicit_amount == 480.0
    assert ground_customer_text("refund 35 USD").explicit_amount == 35.0
    assert ground_customer_text("refund 35 dollars").explicit_amount == 35.0


def test_word_amount_partial_refund_never_defaults_to_full_order_total():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            intake_response(
                support_goal="REFUND",
                refund_scope="PARTIAL",
                case_reason="damaged_on_arrival",
                reason_evidence="damaged",
            ),
            tool_call_response(
                ("r1", "get_order_details", {"order_id": "ORD-1001"}),
                ("r2", "get_user_profile", {"user_id": "USR-101"}),
                ("r3", "audit_fraud_risk", {"order_id": "ORD-1001", "user_id": "USR-101"}),
            ),
        ]
    )
    crew = GlobalCartCrew(
        _settings(), CrewSettings(specialist_max_steps=4), provider, CrewToolKits(module)
    )

    run = crew.handle_customer_message(
        "Order ORD-1001 arrived damaged. Please refund me ten dollars."
    )

    assert run.result.status == "NEEDS_CLARIFICATION"
    assert run.result.stop_reason == "REFUND_AMOUNT_REQUIRED"
    assert run.result.decision is None
    assert not any(name == "process_refund" for name, _ in module.calls)


def test_explicit_full_refund_scope_may_use_trusted_order_total_without_literal_amount():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            intake_response(
                support_goal="REFUND",
                refund_scope="FULL",
                case_reason="damaged_on_arrival",
                reason_evidence="damaged",
            ),
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
    )
    crew = GlobalCartCrew(
        _settings(), CrewSettings(specialist_max_steps=4), provider, CrewToolKits(module)
    )

    run = crew.handle_customer_message(
        "Order ORD-1001 arrived damaged. Please give me a full refund."
    )

    assert run.result.status == "COMPLETED"
    assert run.result.decision is not None
    assert run.result.decision.requested_amount == 35.0
    assert run.result.decision.approved_amount == 35.0
