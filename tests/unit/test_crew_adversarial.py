"""Adversarial Stage 2 tests: try to make probabilistic agents cross authority boundaries."""

from __future__ import annotations

import json

from app.crew.config import CrewSettings
from app.crew.orchestrator import GlobalCartCrew
from app.crew.tools import CrewToolKits
from tests.unit.fakes import FakeProvider, final_response, intake_response, tool_call_response
from tests.unit.test_crew_stage2 import _module, _settings


def _crew(module, provider, *, max_steps: int = 4) -> GlobalCartCrew:
    return GlobalCartCrew(_settings(), CrewSettings(specialist_max_steps=max_steps), provider, CrewToolKits(module))


def _clean_research():
    return tool_call_response(
        ("r1", "get_order_details", {"order_id": "ORD-1001"}),
        ("r2", "get_user_profile", {"user_id": "USR-101"}),
        ("r3", "audit_fraud_risk", {"order_id": "ORD-1001", "user_id": "USR-101"}),
    )


def _clean_decision():
    return tool_call_response(
        ("d1", "check_return_policy", {"order_id": "ORD-1001", "reason": "damaged_on_arrival"}),
        ("d2", "process_refund", {"order_id": "ORD-1001", "amount": 35.0, "reason": "damaged_on_arrival"}),
    )


def _clean_route():
    return tool_call_response(
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
    )


def test_multiple_order_ids_stop_before_any_specialist_tool():
    module = _module(high_risk=False)
    provider = FakeProvider([intake_response(reason_evidence="damaged")])
    run = _crew(module, provider).handle_customer_message(
        "Order ORD-1001 and ORD-1005 both arrived damaged. Refund 35 dollars."
    )
    assert run.result.stop_reason == "MULTIPLE_ORDER_IDS"
    assert module.calls == []
    assert run.trace.researcher is None


def test_multiple_user_ids_stop_before_any_specialist_tool():
    module = _module(high_risk=False)
    provider = FakeProvider([intake_response(reason_evidence="damaged")])
    run = _crew(module, provider).handle_customer_message(
        "I am USR-101 or maybe USR-105. Order ORD-1001 arrived damaged. Refund 35 dollars."
    )
    assert run.result.stop_reason == "MULTIPLE_USER_IDS"
    assert module.calls == []


def test_multiple_money_amounts_are_not_arbitrarily_selected():
    module = _module(high_risk=False)
    provider = FakeProvider([intake_response(reason_evidence="damaged")])
    run = _crew(module, provider).handle_customer_message(
        "Order ORD-1001 arrived damaged. Refund $35, not $25."
    )
    assert run.result.stop_reason == "MULTIPLE_REQUESTED_AMOUNTS"
    assert module.calls == []


def test_semantic_reason_requires_verbatim_customer_evidence():
    module = _module(high_risk=False)
    provider = FakeProvider([intake_response(reason_evidence="screen exploded")])
    run = _crew(module, provider).handle_customer_message(
        "Order ORD-1001 arrived damaged. Please refund 35 dollars."
    )
    assert run.result.status == "FAILED_SAFE"
    assert run.result.stop_reason == "INTAKE_REASON_EVIDENCE_NOT_GROUNDED"
    assert module.calls == []


def test_researcher_cannot_switch_to_a_different_order_then_recovers():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            intake_response(reason_evidence="damaged"),
            tool_call_response(("bad", "get_order_details", {"order_id": "ORD-1005"})),
            _clean_research(),
            _clean_decision(),
            _clean_route(),
        ]
    )
    run = _crew(module, provider).handle_customer_message(
        "Order ORD-1001 arrived damaged. Please refund 35 dollars."
    )
    assert run.result.status == "COMPLETED"
    assert not any(name == "get_order_details" and args.get("order_id") == "ORD-1005" for name, args in module.calls)
    assert any(
        item.reason == "ORDER_ID_NOT_FROM_CUSTOMER_REQUEST"
        for item in run.trace.researcher.interactions
        if item.outcome == "BLOCKED"
    )


def test_researcher_cannot_call_decision_tool_even_if_model_requests_it():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            intake_response(reason_evidence="damaged"),
            tool_call_response(
                ("evil", "process_refund", {"order_id": "ORD-1001", "amount": 35.0, "reason": "damaged_on_arrival"})
            ),
            _clean_research(),
            _clean_decision(),
            _clean_route(),
        ]
    )
    run = _crew(module, provider).handle_customer_message(
        "Order ORD-1001 arrived damaged. Please refund 35 dollars."
    )
    assert run.result.status == "COMPLETED"
    assert sum(name == "process_refund" for name, _ in module.calls) == 1
    assert any(
        item.tool_name == "process_refund" and item.reason == "UNAUTHORIZED_TOOL"
        for item in run.trace.researcher.interactions
    )


def test_decision_cannot_change_policy_reason_then_can_recover_with_grounded_reason():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            intake_response(reason_evidence="damaged"),
            _clean_research(),
            tool_call_response(
                ("bad", "check_return_policy", {"order_id": "ORD-1001", "reason": "changed_mind"})
            ),
            _clean_decision(),
            _clean_route(),
        ]
    )
    run = _crew(module, provider).handle_customer_message(
        "Order ORD-1001 arrived damaged. Please refund 35 dollars."
    )
    assert run.result.status == "COMPLETED"
    policy_calls = [args for name, args in module.calls if name == "check_return_policy"]
    assert policy_calls == [{"order_id": "ORD-1001", "reason": "damaged_on_arrival"}]
    assert any(
        item.reason == "POLICY_REASON_MUST_MATCH_GROUNDED_CASE_REASON"
        for item in run.trace.decision.interactions
        if item.outcome == "BLOCKED"
    )


def test_wrong_refund_amount_is_blocked_before_side_effect_and_second_attempt_is_not_allowed():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            intake_response(reason_evidence="damaged"),
            _clean_research(),
            tool_call_response(
                ("d1", "check_return_policy", {"order_id": "ORD-1001", "reason": "damaged_on_arrival"}),
                ("d2", "process_refund", {"order_id": "ORD-1001", "amount": 5.0, "reason": "damaged_on_arrival"}),
            ),
            tool_call_response(
                ("d3", "process_refund", {"order_id": "ORD-1001", "amount": 35.0, "reason": "damaged_on_arrival"})
            ),
        ]
    )
    run = _crew(module, provider, max_steps=2).handle_customer_message(
        "Order ORD-1001 arrived damaged. Please refund 35 dollars."
    )
    assert run.result.status == "FAILED_SAFE"
    assert not any(name == "process_refund" for name, _ in module.calls)
    reasons = [item.reason for item in run.trace.decision.interactions if item.outcome == "BLOCKED"]
    assert "REFUND_AMOUNT_MUST_MATCH_GROUNDED_REQUEST" in reasons
    assert "REFUND_ATTEMPT_ALREADY_MADE" in reasons


def test_amount_above_trusted_order_total_is_blocked_before_refund_tool():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            intake_response(reason_evidence="damaged"),
            _clean_research(),
            tool_call_response(
                ("d1", "check_return_policy", {"order_id": "ORD-1001", "reason": "damaged_on_arrival"}),
                ("d2", "process_refund", {"order_id": "ORD-1001", "amount": 999.0, "reason": "damaged_on_arrival"}),
            ),
            final_response("done"),
        ]
    )
    run = _crew(module, provider, max_steps=2).handle_customer_message(
        "Order ORD-1001 arrived damaged. Please refund 999 dollars."
    )
    assert run.result.status == "FAILED_SAFE"
    assert not any(name == "process_refund" for name, _ in module.calls)
    assert any(
        item.reason == "REFUND_AMOUNT_EXCEEDS_TRUSTED_ORDER_TOTAL"
        for item in run.trace.decision.interactions
        if item.outcome == "BLOCKED"
    )


def test_clean_route_blocks_an_overeager_alert_and_still_completes_cleanly():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            intake_response(reason_evidence="damaged"),
            _clean_research(),
            _clean_decision(),
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
                ),
                (
                    "c2",
                    "send_slack_alert",
                    {
                        "channel_id": "CH-FRAUD",
                        "severity": "critical",
                        "payload": {
                            "order_id": "ORD-1001",
                            "user_id": "USR-101",
                            "risk_score": 0,
                            "risk_band": "low",
                            "requested_amount": 35.0,
                            "triggered_rules": [],
                        },
                    },
                ),
            ),
        ]
    )
    run = _crew(module, provider).handle_customer_message(
        "Order ORD-1001 arrived damaged. Please refund 35 dollars."
    )
    assert run.result.status == "COMPLETED"
    assert not any(name == "send_slack_alert" for name, _ in module.calls)
    assert any(
        item.tool_name == "send_slack_alert" and item.reason == "TRUSTED_ESCALATION_ROUTE_REQUIRED"
        for item in run.trace.comms.interactions
    )


def test_forged_high_risk_alert_payload_is_blocked_then_correct_payload_can_recover():
    module = _module(high_risk=True)
    provider = FakeProvider(
        [
            intake_response(reason_evidence="smashed"),
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
                    "bad",
                    "send_slack_alert",
                    {
                        "channel_id": "CH-FRAUD",
                        "severity": "critical",
                        "payload": {
                            "order_id": "ORD-1005",
                            "user_id": "USR-105",
                            "risk_score": 12,
                            "risk_band": "high",
                            "requested_amount": 480.0,
                            "triggered_rules": [],
                        },
                    },
                ),
            ),
            tool_call_response(
                (
                    "good",
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
                            "triggered_rules": ["FR-01"],
                        },
                    },
                )
            ),
        ]
    )
    run = _crew(module, provider).handle_customer_message(
        "Order ORD-1005 was smashed on arrival. Refund the full 480 dollars."
    )
    assert run.result.status == "ESCALATED"
    assert sum(name == "send_slack_alert" for name, _ in module.calls) == 1
    assert any(
        item.reason == "ALERT_RISK_SCORE_MUST_MATCH_DECISION"
        for item in run.trace.comms.interactions
        if item.outcome == "BLOCKED"
    )


def test_status_only_intent_runs_order_lookup_but_no_fraud_policy_or_refund():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            intake_response(
                support_goal="ORDER_STATUS",
                case_reason="unknown",
                issue_summary="Customer asks for order status.",
            ),
            tool_call_response(("r1", "get_order_details", {"order_id": "ORD-1001"})),
        ]
    )
    run = _crew(module, provider).handle_customer_message("Where is order ORD-1001?")
    assert run.result.status == "COMPLETED"
    assert run.result.stop_reason == "ORDER_STATUS_RESOLVED"
    assert [name for name, _ in module.calls] == ["get_order_details"]
    assert run.trace.decision is None
    assert run.trace.comms is None


def test_malformed_intake_cannot_smuggle_identifier_into_semantic_contract():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [
            final_response(
                json.dumps(
                    {
                        "intent": "SUPPORT_CASE",
                        "support_goal": "REFUND",
                        "case_reason": "damaged_on_arrival",
                        "reason_evidence": "damaged",
                        "issue_summary": "damaged item",
                        "order_id": "ORD-1001",
                    }
                )
            )
        ]
    )
    run = _crew(module, provider).handle_customer_message(
        "Order ORD-1001 arrived damaged. Please refund 35 dollars."
    )
    assert run.result.status == "FAILED_SAFE"
    assert run.result.stop_reason.startswith("INTAKE_INVALID_RESPONSE")
    assert module.calls == []


def test_intake_tool_call_is_rejected_without_executing_the_tool():
    module = _module(high_risk=False)
    provider = FakeProvider(
        [tool_call_response(("x", "get_order_details", {"order_id": "ORD-1001"}))]
    )
    run = _crew(module, provider).handle_customer_message(
        "Order ORD-1001 arrived damaged. Please refund 35 dollars."
    )
    assert run.result.status == "FAILED_SAFE"
    assert run.result.stop_reason == "INTAKE_TOOL_CALL_FORBIDDEN"
    assert module.calls == []
