"""Cross-run side-effect idempotency for the Stage 2 refund boundary."""

from app.crew.config import CrewSettings
from app.crew.orchestrator import GlobalCartCrew
from app.crew.tools import CrewToolKits
from tests.unit.fakes import FakeProvider, intake_response, tool_call_response
from tests.unit.test_crew_stage2 import _module, _settings


def _one_clean_run_script():
    return [
        intake_response(reason_evidence="damaged"),
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


def test_same_refund_across_two_turns_executes_irreversible_tool_once():
    module = _module(high_risk=False)
    provider = FakeProvider(_one_clean_run_script() + _one_clean_run_script())
    toolkits = CrewToolKits(module)
    crew = GlobalCartCrew(
        _settings(),
        CrewSettings(specialist_max_steps=4),
        provider,
        toolkits,
    )
    message = "Order ORD-1001 arrived damaged. Please refund 35 dollars."

    first = crew.handle_customer_message(message)
    second = crew.handle_customer_message(message)

    assert first.result.status == "COMPLETED"
    assert second.result.status == "COMPLETED"
    assert first.result.decision is not None
    assert second.result.decision is not None
    assert first.result.decision.refund_status == "APPROVED"
    assert second.result.decision.refund_status == "APPROVED"
    assert first.result.decision.refund_id == "RF-TEST"
    assert second.result.decision.refund_id == "RF-TEST"

    actual_refund_calls = [args for name, args in module.calls if name == "process_refund"]
    assert actual_refund_calls == [
        {"order_id": "ORD-1001", "amount": 35.0, "reason": "damaged_on_arrival"}
    ]

    replay = [
        item.result
        for item in second.trace.decision.interactions
        if item.tool_name == "process_refund"
    ][0]
    assert replay["idempotent_replay"] is True
    assert replay["refund_id"] == "RF-TEST"


def test_existing_approved_refund_is_not_replayed_for_different_parameters():
    module = _module(high_risk=False)
    toolkits = CrewToolKits(module)

    first = toolkits.decision.call(
        "process_refund",
        order_id="ORD-1001",
        amount=35.0,
        reason="damaged_on_arrival",
    )
    second = toolkits.decision.call(
        "process_refund",
        order_id="ORD-1001",
        amount=10.0,
        reason="damaged_on_arrival",
    )

    assert first["status"] == "APPROVED"
    assert second["error"] == "REFUND_ALREADY_APPROVED_DIFFERENT_REQUEST"
    assert second["existing_refund_id"] == "RF-TEST"
    assert [name for name, _ in module.calls].count("process_refund") == 1
