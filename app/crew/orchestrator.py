"""Sequential Stage 2 crew orchestration with deterministic handoff guardrails."""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.config import Settings
from app.crew.config import CrewSettings
from app.crew.contracts import CommunicationResult, CrewResult, DecisionHandoff, RiskReport
from app.crew.projection import HandoffProjectionError, latest_result, project_decision, project_risk_report
from app.crew.prompts import COMMS_PROMPT, DECISION_PROMPT, RESEARCHER_PROMPT
from app.crew.runtime import SpecialistAgent, SpecialistRun
from app.crew.tools import CrewToolKits
from app.llm.base import LLMProvider


@dataclass
class CrewTrace:
    researcher: SpecialistRun | None = None
    decision: SpecialistRun | None = None
    comms: SpecialistRun | None = None


@dataclass
class CrewRun:
    result: CrewResult
    trace: CrewTrace


class GlobalCartCrew:
    """Three capability-separated agents connected by typed trusted handoffs."""

    def __init__(
        self,
        settings: Settings,
        crew_settings: CrewSettings,
        provider: LLMProvider,
        toolkits: CrewToolKits,
    ) -> None:
        common = {
            "provider": provider,
            "max_steps": crew_settings.specialist_max_steps,
            "llm_max_retries": settings.llm_max_retries,
            "retry_backoff_seconds": settings.llm_retry_backoff_seconds,
        }
        self.researcher = SpecialistAgent(
            role="researcher",
            system_prompt=RESEARCHER_PROMPT,
            toolkit=toolkits.researcher,
            **common,
        )
        self.decision_agent = SpecialistAgent(
            role="decision",
            system_prompt=DECISION_PROMPT,
            toolkit=toolkits.decision,
            **common,
        )
        self.comms_agent = SpecialistAgent(
            role="comms",
            system_prompt=COMMS_PROMPT,
            toolkit=toolkits.comms,
            **common,
        )

    def handle_customer_message(self, text: str) -> CrewRun:
        trace = CrewTrace()
        claimed_order = _first_prefixed_token(text, "ORD-")
        claimed_user = _first_prefixed_token(text, "USR-")

        # An order identifier is the root trusted identifier for this workflow.
        # Do not let a probabilistic model invent one when the customer supplied none.
        if claimed_order is None:
            return CrewRun(
                result=CrewResult(
                    status="NEEDS_CLARIFICATION",
                    communication=CommunicationResult(
                        customer_response=(
                            "Please provide your order number in the format ORD-1234 so I can review your request."
                        ),
                        escalation_required=False,
                    ),
                    stop_reason="ORDER_ID_REQUIRED",
                ),
                trace=trace,
            )

        trace.researcher = self.researcher.run(
            _research_task(text),
            guard=self._research_guard(claimed_order, claimed_user),
            stop_when=_research_terminal,
        )

        order_result = latest_result(trace.researcher, "get_order_details")
        audit_result = latest_result(trace.researcher, "audit_fraud_risk")

        if isinstance(order_result, dict) and order_result.get("error") == "ORDER_NOT_FOUND":
            return CrewRun(
                result=CrewResult(
                    status="NEEDS_CLARIFICATION",
                    communication=CommunicationResult(
                        customer_response=f"I couldn't find order {claimed_order}. Please check the order number and send it again.",
                        escalation_required=False,
                    ),
                    stop_reason="ORDER_NOT_FOUND",
                ),
                trace=trace,
            )

        if isinstance(audit_result, dict) and audit_result.get("error") == "USER_ORDER_MISMATCH":
            trace.comms = self._run_mismatch_escalation(text, claimed_order, claimed_user)
            delivered = latest_result(trace.comms, "send_slack_alert") or {}
            return CrewRun(
                result=CrewResult(
                    status="ESCALATED",
                    communication=CommunicationResult(
                        customer_response=(
                            "We couldn't verify the customer information for this order. "
                            "Your request requires additional review."
                        ),
                        escalation_required=True,
                        channel_id=delivered.get("channel_id", "CH-FRAUD"),
                        severity=delivered.get("severity", "critical"),
                        alert_delivered=bool(delivered.get("delivered")),
                        alert_transport=delivered.get("transport"),
                        message_ts=delivered.get("message_ts"),
                    ),
                    stop_reason="USER_ORDER_MISMATCH",
                ),
                trace=trace,
            )

        try:
            risk = project_risk_report(trace.researcher)
        except HandoffProjectionError as exc:
            return CrewRun(result=_failed_safe(str(exc)), trace=trace)

        order_status = str(risk.evidence.get("order_status") or (order_result or {}).get("status") or "unknown")
        requested_amount = _requested_amount(text, risk)

        trace.decision = self.decision_agent.run(
            _decision_task(text, risk, requested_amount),
            guard=self._decision_guard(risk, requested_amount),
            stop_when=lambda run: _decision_terminal(run, risk),
        )

        try:
            decision = project_decision(
                trace.decision,
                risk,
                requested_amount=requested_amount,
                order_status=order_status,
            )
        except HandoffProjectionError as exc:
            return CrewRun(
                result=_failed_safe(f"Decision handoff invalid: {exc}", risk=risk),
                trace=trace,
            )

        if decision.refund_status == "NOT_ATTEMPTED":
            return CrewRun(
                result=_failed_safe(
                    "Eligible case ended without a trusted refund outcome.",
                    risk=risk,
                    decision=decision,
                ),
                trace=trace,
            )

        trace.comms = self.comms_agent.run(
            _comms_task(decision),
            guard=self._comms_guard(decision),
            stop_when=_comms_terminal,
        )
        communication = _project_communication(decision, trace.comms)
        status = "ESCALATED" if communication.escalation_required else "COMPLETED"
        return CrewRun(
            result=CrewResult(
                status=status,
                risk_report=risk,
                decision=decision,
                communication=communication,
            ),
            trace=trace,
        )

    @staticmethod
    def _research_guard(claimed_order: str | None, claimed_user: str | None):
        def guard(name: str, args: dict, run: SpecialistRun) -> tuple[bool, str | None]:
            order = latest_result(run, "get_order_details")
            profile = latest_result(run, "get_user_profile")

            if name == "get_order_details":
                if order is not None:
                    return False, "ORDER_LOOKUP_ALREADY_COMPLETED"
                if claimed_order and str(args.get("order_id", "")).upper() != claimed_order:
                    return False, "ORDER_ID_NOT_FROM_CUSTOMER_REQUEST"
                return True, None

            if name == "get_user_profile":
                if not isinstance(order, dict) or "error" in order:
                    return False, "ORDER_MUST_BE_VERIFIED_FIRST"
                if profile is not None:
                    return False, "USER_LOOKUP_ALREADY_COMPLETED"
                expected_user = claimed_user or order.get("user_id")
                if args.get("user_id") != expected_user:
                    return False, "USER_ID_MUST_PRESERVE_CUSTOMER_CLAIM"
                return True, None

            if name == "audit_fraud_risk":
                if not isinstance(order, dict) or "error" in order:
                    return False, "ORDER_MUST_BE_VERIFIED_FIRST"
                if not isinstance(profile, dict) or "error" in profile:
                    return False, "USER_MUST_BE_VERIFIED_FIRST"
                if args.get("order_id") != order.get("order_id"):
                    return False, "AUDIT_ORDER_MUST_MATCH_VERIFIED_ORDER"
                expected_user = claimed_user or order.get("user_id")
                audit_user = args.get("user_id")
                if audit_user is None:
                    if claimed_user:
                        return False, "AUDIT_MUST_INCLUDE_CLAIMED_USER_ID"
                elif audit_user != expected_user:
                    return False, "AUDIT_USER_ID_MUST_PRESERVE_CUSTOMER_CLAIM"
                return True, None

            return True, None

        return guard

    @staticmethod
    def _decision_guard(risk: RiskReport, requested_amount: float):
        def guard(name: str, args: dict, run: SpecialistRun) -> tuple[bool, str | None]:
            policy = latest_result(run, "check_return_policy")
            if name == "check_return_policy":
                if policy is not None:
                    return False, "POLICY_ALREADY_CHECKED"
                if args.get("order_id") != risk.order_id:
                    return False, "POLICY_ORDER_MUST_MATCH_RISK_REPORT"
                return True, None
            if name == "process_refund":
                prior_attempts = [i for i in run.interactions if i.tool_name == "process_refund"]
                if prior_attempts:
                    return False, "REFUND_ATTEMPT_ALREADY_MADE"
                if risk.blocks_automatic_refund:
                    return False, "HIGH_RISK_BLOCKS_AUTOMATIC_REFUND"
                if not isinstance(policy, dict) or policy.get("eligible") is not True:
                    return False, "ELIGIBLE_POLICY_REQUIRED"
                if args.get("order_id") != risk.order_id:
                    return False, "REFUND_ORDER_MUST_MATCH_RISK_REPORT"
                try:
                    amount = float(args.get("amount"))
                except (TypeError, ValueError):
                    return False, "REFUND_AMOUNT_INVALID"
                if abs(amount - requested_amount) > 0.001:
                    return False, "REFUND_AMOUNT_MUST_MATCH_CUSTOMER_REQUEST"
                return True, None
            return True, None

        return guard

    @staticmethod
    def _comms_guard(decision: DecisionHandoff):
        def guard(name: str, args: dict, run: SpecialistRun) -> tuple[bool, str | None]:
            route = latest_result(run, "get_escalation_route")
            if name == "get_escalation_route":
                if route is not None:
                    return False, "ROUTE_ALREADY_RESOLVED"
                expected = {
                    "risk_band": decision.risk_band,
                    "requested_amount": decision.requested_amount,
                    "prior_fraud_flags": decision.prior_fraud_flags,
                    "order_status": decision.order_status,
                    "verdict": decision.policy_verdict,
                }
                for key, value in expected.items():
                    if args.get(key) != value:
                        return False, f"ROUTE_{key.upper()}_MUST_MATCH_DECISION"
                return True, None

            if name == "send_slack_alert":
                prior_alert = latest_result(run, "send_slack_alert")
                if prior_alert is not None:
                    return False, "ALERT_ALREADY_SENT"
                if not isinstance(route, dict) or route.get("escalation_required") is not True:
                    return False, "TRUSTED_ESCALATION_ROUTE_REQUIRED"
                if args.get("channel_id") != route.get("channel_id"):
                    return False, "ALERT_CHANNEL_MUST_MATCH_ROUTE"
                if args.get("severity") != route.get("severity"):
                    return False, "ALERT_SEVERITY_MUST_MATCH_ROUTE"
                payload = args.get("payload")
                if not isinstance(payload, dict):
                    return False, "ALERT_PAYLOAD_REQUIRED"
                expected_payload = {
                    "order_id": decision.order_id,
                    "user_id": decision.user_id,
                    "risk_score": decision.risk_score,
                    "risk_band": decision.risk_band,
                    "requested_amount": decision.requested_amount,
                }
                for key, value in expected_payload.items():
                    if payload.get(key) != value:
                        return False, f"ALERT_{key.upper()}_MUST_MATCH_DECISION"
                return True, None

            return True, None

        return guard

    def _run_mismatch_escalation(
        self, text: str, order_id: str | None, user_id: str | None
    ) -> SpecialistRun:
        task = (
            "A trusted fraud-audit tool returned USER_ORDER_MISMATCH. This is a terminal "
            "identity/order consistency red flag. Send one critical structured alert to "
            "CH-FRAUD. Do not discuss fraud with the customer.\n"
            f"Customer ticket: {text}\nOrder: {order_id}\nClaimed user: {user_id}"
        )

        def guard(name: str, args: dict, run: SpecialistRun) -> tuple[bool, str | None]:
            if name != "send_slack_alert":
                return False, "MISMATCH_USES_DIRECT_SECURITY_ESCALATION"
            if latest_result(run, "send_slack_alert") is not None:
                return False, "ALERT_ALREADY_SENT"
            if args.get("channel_id") != "CH-FRAUD" or args.get("severity") != "critical":
                return False, "MISMATCH_MUST_ROUTE_TO_CRITICAL_FRAUD_CHANNEL"
            payload = args.get("payload")
            if not isinstance(payload, dict) or payload.get("order_id") != order_id:
                return False, "MISMATCH_ALERT_MUST_IDENTIFY_ORDER"
            return True, None

        return self.comms_agent.run(
            task,
            guard=guard,
            stop_when=lambda run: latest_result(run, "send_slack_alert") is not None,
        )


def _research_task(text: str) -> str:
    return (
        "Investigate this customer ticket. Produce trusted evidence by using your tools; "
        "the runtime will construct the RiskReport from the deterministic fraud audit.\n\n"
        f"Customer ticket:\n{text}"
    )


def _decision_task(text: str, risk: RiskReport, requested_amount: float) -> str:
    return (
        "Resolve the business outcome for this ticket. The RiskReport below is a validated "
        "handoff from the Researcher. Use policy/refund tools only within your authority.\n\n"
        f"Customer ticket:\n{text}\n\n"
        f"Requested amount used by the runtime: {requested_amount:.2f} USD\n"
        f"RiskReport:\n{risk.model_dump_json(indent=2)}"
    )


def _comms_task(decision: DecisionHandoff) -> str:
    payload = {
        "order_id": decision.order_id,
        "user_id": decision.user_id,
        "risk_score": decision.risk_score,
        "risk_band": decision.risk_band,
        "triggered_rules": decision.triggered_rule_ids,
        "requested_amount": decision.requested_amount,
    }
    return (
        "Route this validated Decision. Call get_escalation_route with every Decision routing "
        "field exactly as provided. If escalation is required, send exactly one alert using "
        "the returned channel/severity and the canonical payload below.\n\n"
        f"Decision:\n{decision.model_dump_json(indent=2)}\n\n"
        f"Canonical alert payload:\n{json.dumps(payload, indent=2)}"
    )


def _research_terminal(run: SpecialistRun) -> bool:
    order = latest_result(run, "get_order_details")
    if isinstance(order, dict) and order.get("error") == "ORDER_NOT_FOUND":
        return True
    return latest_result(run, "audit_fraud_risk") is not None


def _decision_terminal(run: SpecialistRun, risk: RiskReport) -> bool:
    policy = latest_result(run, "check_return_policy")
    if not isinstance(policy, dict):
        return False
    if "error" in policy or not policy.get("eligible") or risk.blocks_automatic_refund:
        return True
    return latest_result(run, "process_refund") is not None


def _comms_terminal(run: SpecialistRun) -> bool:
    route = latest_result(run, "get_escalation_route")
    if not isinstance(route, dict):
        return False
    if route.get("escalation_required") is False:
        return True
    return latest_result(run, "send_slack_alert") is not None


def _requested_amount(text: str, risk: RiskReport) -> float:
    """Extract obvious explicit amounts; otherwise treat the request as full-order value."""
    words = text.replace("$", " $ ").replace(",", " ").split()
    for index, word in enumerate(words):
        candidate = word.strip(".,!?()[]{}")
        if candidate == "$" and index + 1 < len(words):
            candidate = words[index + 1].strip(".,!?()[]{}")
        elif candidate.startswith("$"):
            candidate = candidate[1:]
        next_word = words[index + 1].lower().strip(".,!?()[]{}") if index + 1 < len(words) else ""
        try:
            value = float(candidate)
        except ValueError:
            continue
        if word.startswith("$") or word == "$" or next_word in {"dollar", "dollars", "usd"}:
            return round(value, 2)
    return round(float(risk.evidence.get("order_total_usd") or 0.0), 2)


def _first_prefixed_token(text: str, prefix: str) -> str | None:
    upper_prefix = prefix.upper()
    for raw in text.replace(",", " ").replace(".", " ").split():
        token = raw.strip("!?()[]{}:;\"'").upper()
        if token.startswith(upper_prefix) and token[len(upper_prefix):].isdigit():
            return token
    return None


def _project_communication(decision: DecisionHandoff, run: SpecialistRun) -> CommunicationResult:
    route = latest_result(run, "get_escalation_route") or {}
    alert = latest_result(run, "send_slack_alert") or {}

    if decision.refund_status == "APPROVED":
        response = f"Your refund of {decision.approved_amount:.2f} USD has been approved."
    elif decision.refund_status == "REJECTED":
        response = "Your request is not eligible for an automatic refund under the applicable return policy."
    else:
        response = "Your request requires additional review. No refund has been issued yet."

    escalation_required = bool(route.get("escalation_required"))
    return CommunicationResult(
        customer_response=response,
        escalation_required=escalation_required,
        channel_id=route.get("channel_id"),
        severity=route.get("severity"),
        alert_delivered=bool(alert.get("delivered")),
        alert_transport=alert.get("transport"),
        message_ts=alert.get("message_ts"),
    )


def _failed_safe(
    reason: str,
    *,
    risk: RiskReport | None = None,
    decision: DecisionHandoff | None = None,
) -> CrewResult:
    return CrewResult(
        status="FAILED_SAFE",
        risk_report=risk,
        decision=decision,
        communication=CommunicationResult(
            customer_response="Your request requires additional review. No refund has been issued yet.",
            escalation_required=False,
        ),
        stop_reason=reason,
    )
