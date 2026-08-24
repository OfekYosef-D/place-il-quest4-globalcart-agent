"""Tests for the safe Stage 2 web/demo execution trace."""

from app.crew.contracts import CommunicationResult, CrewResult, DecisionHandoff, RiskReport
from app.crew.orchestrator import CrewRun, CrewTrace
from app.crew.presentation import present_crew_run
from app.crew.runtime import CrewToolInteraction, SpecialistRun


def test_presenter_exposes_tools_and_handoffs_without_model_messages():
    risk = RiskReport(
        order_id="ORD-1005",
        user_id="USR-105",
        risk_score=90,
        risk_band="high",
        action_hint="review",
        triggered_rules=[{"rule_id": "FR-01"}],
        evidence={"order_status": "delivered", "prior_fraud_flags": 1},
        blocks_automatic_refund=True,
        requires_security_channel=True,
        rulebook_version="test",
    )
    decision = DecisionHandoff(
        order_id="ORD-1005",
        user_id="USR-105",
        policy_verdict="ELIGIBLE",
        requested_amount=480.0,
        refund_status="ESCALATION_REQUIRED",
        approved_amount=0.0,
        applicable_policies=["POL-RET-01"],
        risk_score=90,
        risk_band="high",
        triggered_rule_ids=["FR-01"],
        prior_fraud_flags=1,
        order_status="delivered",
        rationale=["trusted rationale"],
    )
    researcher = SpecialistRun(
        role="researcher",
        final_content="private free-form model text that the UI must not expose",
        interactions=[
            CrewToolInteraction(
                step=1,
                role="researcher",
                tool_name="audit_fraud_risk",
                arguments={"order_id": "ORD-1005", "user_id": "USR-105"},
                result={"risk_score": 90, "risk_band": "high", "blocks_automatic_refund": True},
                outcome="EXECUTED",
            )
        ],
    )
    run = CrewRun(
        result=CrewResult(
            status="ESCALATED",
            risk_report=risk,
            decision=decision,
            communication=CommunicationResult(
                customer_response="Your request requires additional review. No refund has been issued yet.",
                escalation_required=True,
                channel_id="CH-FRAUD",
                severity="critical",
                alert_delivered=True,
            ),
        ),
        trace=CrewTrace(researcher=researcher),
    )

    payload = present_crew_run(run)

    assert payload["status"] == "ESCALATED"
    assert payload["risk_report"]["risk_score"] == 90
    assert payload["decision"]["refund_status"] == "ESCALATION_REQUIRED"
    assert payload["agents"][0]["steps"][0]["tool"] == "audit_fraud_risk"
    assert "messages" not in payload["agents"][0]
    assert "final_content" not in payload["agents"][0]
    assert "private free-form model text" not in str(payload)


def test_blocked_guardrail_is_visible_as_observable_execution_fact():
    comms = SpecialistRun(
        role="comms",
        interactions=[
            CrewToolInteraction(
                step=1,
                role="comms",
                tool_name="send_slack_alert",
                arguments={"channel_id": "CH-FRAUD"},
                result={"error": "GUARDRAIL_BLOCKED"},
                outcome="BLOCKED",
                reason="TRUSTED_ESCALATION_ROUTE_REQUIRED",
            )
        ],
    )
    run = CrewRun(
        result=CrewResult(
            status="FAILED_SAFE",
            communication=CommunicationResult(
                customer_response="Your request requires additional review. No refund has been issued yet.",
                escalation_required=False,
            ),
        ),
        trace=CrewTrace(comms=comms),
    )

    payload = present_crew_run(run)
    step = payload["agents"][2]["steps"][0]
    assert step["outcome"] == "BLOCKED"
    assert step["guardrail_reason"] == "TRUSTED_ESCALATION_ROUTE_REQUIRED"
