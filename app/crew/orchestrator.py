"""Sequential Stage 2 crew orchestration with deterministic handoff guardrails."""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.config import Settings
from app.crew.config import CrewSettings
from app.crew.context import PendingCustomerContext
from app.crew.contracts import CommunicationResult, CrewResult, DecisionHandoff, RiskReport
from app.crew.grounding import GroundedCustomerFacts, ground_customer_text
from app.crew.intake import IntakeAssessment, IntakeClassifier, IntakeTrace
from app.crew.projection import HandoffProjectionError, latest_result, project_decision, project_risk_report
from app.crew.prompts import COMMS_PROMPT, DECISION_PROMPT, RESEARCHER_PROMPT
from app.crew.runtime import SpecialistAgent, SpecialistRun
from app.crew.tools import CrewToolKits
from app.llm.base import LLMProvider


@dataclass
class CrewTrace:
    intake: IntakeTrace | None = None
    grounded: GroundedCustomerFacts | None = None
    researcher: SpecialistRun | None = None
    decision: SpecialistRun | None = None
    comms: SpecialistRun | None = None


@dataclass
class CrewRun:
    result: CrewResult
    trace: CrewTrace


class GlobalCartCrew:
    """Three capability-separated agents behind a semantic/grounding intake gate."""

    def __init__(
        self,
        settings: Settings,
        crew_settings: CrewSettings,
        provider: LLMProvider,
        toolkits: CrewToolKits,
    ) -> None:
        self.intake_classifier = IntakeClassifier(
            provider,
            max_retries=settings.llm_max_retries,
            retry_backoff_seconds=settings.llm_retry_backoff_seconds,
        )
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

    def handle_customer_message(
        self,
        text: str,
        *,
        prior_context: PendingCustomerContext | None = None,
    ) -> CrewRun:
        trace = CrewTrace()
        current_grounded = ground_customer_text(text)
        grounded = _resolve_grounded_context(current_grounded, prior_context)
        trace.grounded = grounded

        # Phase 0: semantic understanding with no tools and no business authority.
        trace.intake = self.intake_classifier.classify(
            text,
            prior_semantic=_prior_semantic_context(prior_context),
        )
        if trace.intake.assessment is None:
            return CrewRun(
                result=_failed_safe(
                    trace.intake.failure_reason or "INTAKE_FAILED",
                    customer_response=(
                        "I couldn't safely determine what you need from that message. "
                        "Please describe the order issue again."
                    ),
                ),
                trace=trace,
            )
        intake = trace.intake.assessment

        if not _reason_evidence_is_grounded(text, intake, prior_context):
            return CrewRun(
                result=_failed_safe(
                    "INTAKE_REASON_EVIDENCE_NOT_GROUNDED",
                    customer_response=(
                        "I couldn't safely determine the reason for the request. "
                        "Please describe what happened to the order."
                    ),
                ),
                trace=trace,
            )

        non_case = _non_case_intake_result(intake)
        if non_case is not None:
            return CrewRun(result=non_case, trace=trace)

        grounding_result = _validate_grounded_customer_facts(grounded)
        if grounding_result is not None:
            return CrewRun(result=grounding_result, trace=trace)

        claimed_order = grounded.order_id
        claimed_user = grounded.user_id
        assert claimed_order is not None

        if intake.support_goal == "NONE":
            return CrewRun(
                result=_clarification(
                    "INTAKE_SUPPORT_GOAL_REQUIRED",
                    "I have the order number, but I still need to know what went wrong or what you'd like help with.",
                ),
                trace=trace,
            )

        if intake.support_goal == "ORDER_STATUS":
            return self._handle_status_case(text, intake, claimed_order, trace)

        trace.researcher = self.researcher.run(
            _research_task(text, intake, claimed_order, claimed_user),
            guard=self._research_guard(claimed_order, claimed_user),
            stop_when=_research_terminal,
        )

        order_result = latest_result(trace.researcher, "get_order_details")
        profile_result = latest_result(trace.researcher, "get_user_profile")
        audit_result = latest_result(trace.researcher, "audit_fraud_risk")

        if isinstance(order_result, dict) and order_result.get("error") == "ORDER_NOT_FOUND":
            return CrewRun(
                result=_clarification(
                    "ORDER_NOT_FOUND",
                    f"I couldn't find order {claimed_order}. Please check the order number and send it again.",
                ),
                trace=trace,
            )

        if isinstance(profile_result, dict) and profile_result.get("error") == "USER_NOT_FOUND":
            if claimed_user:
                return CrewRun(
                    result=_clarification(
                        "USER_NOT_FOUND",
                        f"I couldn't verify customer id {claimed_user}. Please check the customer information and try again.",
                    ),
                    trace=trace,
                )
            return CrewRun(
                result=_failed_safe("VERIFIED_ORDER_OWNER_PROFILE_NOT_FOUND"),
                trace=trace,
            )

        if isinstance(audit_result, dict) and audit_result.get("error") == "USER_ORDER_MISMATCH":
            trace.comms = self._run_mismatch_escalation(text, claimed_order, claimed_user)
            delivered = latest_result(trace.comms, "send_slack_alert") or {}
            if trace.comms.failure_reason or not delivered.get("delivered"):
                return CrewRun(
                    result=_failed_safe("USER_ORDER_MISMATCH_ALERT_FAILED"),
                    trace=trace,
                )
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
                        alert_delivered=True,
                        alert_transport=delivered.get("transport"),
                        message_ts=delivered.get("message_ts"),
                    ),
                    stop_reason="USER_ORDER_MISMATCH",
                ),
                trace=trace,
            )

        if isinstance(audit_result, dict) and "error" in audit_result:
            return CrewRun(
                result=_failed_safe(f"FRAUD_AUDIT_ERROR: {audit_result.get('error')}"),
                trace=trace,
            )

        try:
            risk = project_risk_report(trace.researcher)
        except HandoffProjectionError as exc:
            return CrewRun(result=_failed_safe(str(exc)), trace=trace)

        if not isinstance(order_result, dict) or "error" in order_result:
            return CrewRun(result=_failed_safe("VERIFIED_ORDER_RESULT_MISSING"), trace=trace)

        policy_reason = _resolve_policy_reason(intake, order_result)
        if policy_reason is None:
            return CrewRun(
                result=_clarification(
                    "RETURN_REASON_REQUIRED",
                    "I found the order, but I need a little more detail about what happened before I can evaluate a return or refund.",
                    risk=risk,
                ),
                trace=trace,
            )

        # A numeric amount is a deterministic monetary fact. If a customer asks
        # for a partial/unspecified refund without a literal amount, never turn
        # that ambiguity into a full-order refund. Only an explicit FULL scope
        # may intentionally resolve to the trusted order total.
        if (
            intake.support_goal == "REFUND"
            and grounded.explicit_amount is None
            and intake.refund_scope != "FULL"
        ):
            return CrewRun(
                result=_clarification(
                    "REFUND_AMOUNT_REQUIRED",
                    "Please tell me the exact refund amount you are requesting, or say that you want a full refund.",
                    risk=risk,
                ),
                trace=trace,
            )

        order_status = str(risk.evidence.get("order_status") or order_result.get("status") or "unknown")
        requested_amount = _requested_amount(grounded, risk)
        if requested_amount <= 0:
            return CrewRun(
                result=_clarification(
                    "REQUESTED_AMOUNT_INVALID",
                    "Please provide a valid positive refund amount.",
                    risk=risk,
                ),
                trace=trace,
            )

        trace.decision = self.decision_agent.run(
            _decision_task(text, risk, requested_amount, policy_reason),
            guard=self._decision_guard(risk, requested_amount, policy_reason),
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
        try:
            communication = _project_communication(decision, trace.comms)
        except HandoffProjectionError as exc:
            return CrewRun(
                result=_failed_safe(f"Communication handoff invalid: {exc}", risk=risk, decision=decision),
                trace=trace,
            )

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

    def _handle_status_case(
        self,
        text: str,
        intake: IntakeAssessment,
        order_id: str,
        trace: CrewTrace,
    ) -> CrewRun:
        trace.researcher = self.researcher.run(
            _status_task(text, intake, order_id),
            guard=self._status_guard(order_id),
            stop_when=_status_terminal,
        )
        order = latest_result(trace.researcher, "get_order_details")
        if trace.researcher.failure_reason:
            return CrewRun(
                result=_failed_safe(f"STATUS_LOOKUP_FAILED: {trace.researcher.failure_reason}"),
                trace=trace,
            )
        if not isinstance(order, dict):
            return CrewRun(result=_failed_safe("STATUS_ORDER_RESULT_MISSING"), trace=trace)
        if order.get("error") == "ORDER_NOT_FOUND":
            return CrewRun(
                result=_clarification(
                    "ORDER_NOT_FOUND",
                    f"I couldn't find order {order_id}. Please check the order number and send it again.",
                ),
                trace=trace,
            )
        if "error" in order:
            return CrewRun(result=_failed_safe(f"STATUS_ORDER_ERROR: {order['error']}"), trace=trace)

        status = str(order.get("status") or "unknown")
        delivery = order.get("delivery_date")
        suffix = f" It was delivered on {delivery}." if status == "delivered" and delivery else ""
        return CrewRun(
            result=CrewResult(
                status="COMPLETED",
                communication=CommunicationResult(
                    customer_response=f"Order {order_id} is currently {status}.{suffix}",
                    escalation_required=False,
                ),
                stop_reason="ORDER_STATUS_RESOLVED",
            ),
            trace=trace,
        )

    @staticmethod
    def _status_guard(order_id: str):
        def guard(name: str, args: dict, run: SpecialistRun) -> tuple[bool, str | None]:
            if name != "get_order_details":
                return False, "STATUS_QUERY_ALLOWS_ORDER_LOOKUP_ONLY"
            if latest_result(run, "get_order_details") is not None:
                return False, "ORDER_LOOKUP_ALREADY_COMPLETED"
            if str(args.get("order_id", "")).upper() != order_id:
                return False, "ORDER_ID_NOT_FROM_CUSTOMER_REQUEST"
            return True, None

        return guard

    @staticmethod
    def _research_guard(claimed_order: str, claimed_user: str | None):
        def guard(name: str, args: dict, run: SpecialistRun) -> tuple[bool, str | None]:
            order = latest_result(run, "get_order_details")
            profile = latest_result(run, "get_user_profile")

            if name == "get_order_details":
                if order is not None:
                    return False, "ORDER_LOOKUP_ALREADY_COMPLETED"
                if str(args.get("order_id", "")).upper() != claimed_order:
                    return False, "ORDER_ID_NOT_FROM_CUSTOMER_REQUEST"
                return True, None

            if name == "get_user_profile":
                if not isinstance(order, dict) or "error" in order:
                    return False, "ORDER_MUST_BE_VERIFIED_FIRST"
                if profile is not None:
                    return False, "USER_LOOKUP_ALREADY_COMPLETED"
                expected_user = claimed_user or order.get("user_id")
                if args.get("user_id") != expected_user:
                    return False, "USER_ID_MUST_PRESERVE_GROUNDED_CONTEXT"
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
                    return False, "AUDIT_USER_ID_MUST_PRESERVE_GROUNDED_CONTEXT"
                return True, None

            return True, None

        return guard

    @staticmethod
    def _decision_guard(risk: RiskReport, requested_amount: float, policy_reason: str):
        def guard(name: str, args: dict, run: SpecialistRun) -> tuple[bool, str | None]:
            policy = latest_result(run, "check_return_policy")
            if name == "check_return_policy":
                if policy is not None:
                    return False, "POLICY_ALREADY_CHECKED"
                if args.get("order_id") != risk.order_id:
                    return False, "POLICY_ORDER_MUST_MATCH_RISK_REPORT"
                if args.get("reason") != policy_reason:
                    return False, "POLICY_REASON_MUST_MATCH_GROUNDED_CASE_REASON"
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
                if args.get("reason") != policy_reason:
                    return False, "REFUND_REASON_MUST_MATCH_GROUNDED_CASE_REASON"
                try:
                    amount = float(args.get("amount"))
                except (TypeError, ValueError):
                    return False, "REFUND_AMOUNT_INVALID"
                if amount <= 0:
                    return False, "REFUND_AMOUNT_INVALID"
                if abs(amount - requested_amount) > 0.001:
                    return False, "REFUND_AMOUNT_MUST_MATCH_GROUNDED_REQUEST"
                trusted_total = float(risk.evidence.get("order_total_usd") or 0.0)
                if trusted_total > 0 and amount - trusted_total > 0.001:
                    return False, "REFUND_AMOUNT_EXCEEDS_TRUSTED_ORDER_TOTAL"
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
                    "triggered_rules": decision.triggered_rule_ids,
                }
                for key, value in expected_payload.items():
                    if payload.get(key) != value:
                        return False, f"ALERT_{key.upper()}_MUST_MATCH_DECISION"
                return True, None

            return True, None

        return guard

    def _run_mismatch_escalation(
        self, text: str, order_id: str, user_id: str | None
    ) -> SpecialistRun:
        canonical_payload = {
            "order_id": order_id,
            "claimed_user_id": user_id,
            "reason": "USER_ORDER_MISMATCH",
        }
        task = (
            "A trusted fraud-audit tool returned USER_ORDER_MISMATCH. Send one critical "
            "structured alert to CH-FRAUD using exactly the canonical payload below. "
            "Do not invent a fraud score or any other risk facts.\n\n"
            f"Customer ticket: {text}\n"
            f"Canonical payload: {json.dumps(canonical_payload, sort_keys=True)}"
        )

        def guard(name: str, args: dict, run: SpecialistRun) -> tuple[bool, str | None]:
            if name != "send_slack_alert":
                return False, "MISMATCH_USES_DIRECT_SECURITY_ESCALATION"
            if latest_result(run, "send_slack_alert") is not None:
                return False, "ALERT_ALREADY_SENT"
            if args.get("channel_id") != "CH-FRAUD" or args.get("severity") != "critical":
                return False, "MISMATCH_MUST_ROUTE_TO_CRITICAL_FRAUD_CHANNEL"
            if args.get("payload") != canonical_payload:
                return False, "MISMATCH_ALERT_PAYLOAD_MUST_MATCH_CANONICAL_FACTS"
            return True, None

        return self.comms_agent.run(
            task,
            guard=guard,
            stop_when=lambda run: latest_result(run, "send_slack_alert") is not None,
        )


def _prior_semantic_context(context: PendingCustomerContext | None) -> dict | None:
    if context is None:
        return None
    return {
        "support_goal": context.support_goal,
        "refund_scope": context.refund_scope,
        "case_reason": context.case_reason,
        "reason_evidence": context.reason_evidence,
        "issue_summary": context.issue_summary,
    }


def _resolve_grounded_context(
    current: GroundedCustomerFacts,
    prior: PendingCustomerContext | None,
) -> GroundedCustomerFacts:
    if prior is None:
        return current

    order_ids = current.order_ids or ((prior.order_id,) if prior.order_id else ())
    user_ids = current.user_ids or ((prior.user_id,) if prior.user_id else ())
    explicit_amounts = current.explicit_amounts or (
        (prior.explicit_amount,) if prior.explicit_amount is not None else ()
    )
    return GroundedCustomerFacts(
        order_ids=tuple(order_ids),
        user_ids=tuple(user_ids),
        explicit_amounts=tuple(explicit_amounts),
    )


def _non_case_intake_result(intake: IntakeAssessment) -> CrewResult | None:
    if intake.intent == "SUPPORT_CASE":
        return None
    if intake.intent == "GREETING":
        return CrewResult(
            status="COMPLETED",
            communication=CommunicationResult(
                customer_response="Hi! How can I help with your GlobalCart order today?",
                escalation_required=False,
            ),
            stop_reason="INTAKE_GREETING",
        )
    if intake.intent == "GENERAL_QUESTION":
        return _clarification(
            "INTAKE_GENERAL_QUESTION",
            "I can review how GlobalCart support rules apply to a specific order. Tell me what happened and include the order number if you have it.",
        )
    if intake.intent == "OUT_OF_SCOPE":
        return _clarification(
            "INTAKE_OUT_OF_SCOPE",
            "I can help with GlobalCart orders, deliveries, returns, and refunds. What can I help you with there?",
        )
    return _clarification(
        "INTAKE_UNCLEAR",
        "Tell me what happened with your order and what you'd like help with.",
    )


def _validate_grounded_customer_facts(grounded: GroundedCustomerFacts) -> CrewResult | None:
    if not grounded.order_ids:
        return _clarification(
            "ORDER_ID_REQUIRED",
            "Please provide your order number in the format ORD-1234 so I can review your request.",
        )
    if len(grounded.order_ids) > 1:
        return _clarification(
            "MULTIPLE_ORDER_IDS",
            "I see more than one order number. Please send one order per support request so I don't act on the wrong order.",
        )
    if len(grounded.user_ids) > 1:
        return _clarification(
            "MULTIPLE_USER_IDS",
            "I see more than one customer id. Please confirm which customer id belongs to this request.",
        )
    if len(grounded.explicit_amounts) > 1:
        return _clarification(
            "MULTIPLE_REQUESTED_AMOUNTS",
            "I see more than one refund amount. Please confirm the single amount you are requesting.",
        )
    if grounded.explicit_amount is not None and grounded.explicit_amount <= 0:
        return _clarification(
            "REQUESTED_AMOUNT_INVALID",
            "Please provide a valid positive refund amount.",
        )
    return None


def _reason_evidence_is_grounded(
    text: str,
    intake: IntakeAssessment,
    prior_context: PendingCustomerContext | None,
) -> bool:
    if intake.case_reason == "unknown":
        return True
    evidence = (intake.reason_evidence or "").strip()
    if not evidence:
        return False
    if evidence.casefold() in text.casefold():
        return True
    if prior_context is None or prior_context.case_reason != intake.case_reason:
        return False
    prior_evidence = (prior_context.reason_evidence or "").strip()
    return bool(prior_evidence) and evidence.casefold() == prior_evidence.casefold()


def _resolve_policy_reason(intake: IntakeAssessment, order: dict) -> str | None:
    if intake.case_reason != "unknown":
        return intake.case_reason

    conditions = {
        str(item.get("condition"))
        for item in order.get("items", [])
        if isinstance(item, dict) and item.get("condition")
    }
    derived = set()
    if "damaged_on_arrival" in conditions:
        derived.add("damaged_on_arrival")
    if "wrong_item" in conditions:
        derived.add("wrong_item")
    if "missing" in conditions:
        derived.add("item_missing")
    if str(order.get("status")) == "delayed":
        derived.add("late_delivery")
    if len(derived) == 1:
        return next(iter(derived))
    return None


def _research_task(
    text: str,
    intake: IntakeAssessment,
    order_id: str,
    user_id: str | None,
) -> str:
    grounded = {"order_id": order_id, "claimed_user_id": user_id}
    return (
        "Investigate this grounded customer support case. Produce trusted evidence using your tools; "
        "the runtime constructs RiskReport from the deterministic fraud audit. The semantic intake "
        "below has no business authority. The grounded identifier context is authoritative and must "
        "be preserved exactly.\n\n"
        f"Grounded identifiers: {json.dumps(grounded, sort_keys=True)}\n"
        f"Semantic intake: {intake.model_dump_json()}\n"
        f"Latest customer message:\n{text}"
    )


def _status_task(text: str, intake: IntakeAssessment, order_id: str) -> str:
    return (
        "This is a status-only support request. Use the order lookup tool for the exact grounded "
        "order id and stop when the trusted order result is available. Do not investigate fraud, "
        "policy, or refunds for this task.\n\n"
        f"Grounded order id: {order_id}\n"
        f"Semantic intake: {intake.model_dump_json()}\n"
        f"Latest customer message:\n{text}"
    )


def _decision_task(text: str, risk: RiskReport, requested_amount: float, policy_reason: str) -> str:
    return (
        "Resolve the business outcome for this ticket. RiskReport is a validated Researcher handoff. "
        "The runtime has also resolved the policy reason and requested amount from grounded customer "
        "facts and trusted order data. Use those exact values; do not substitute another reason or amount.\n\n"
        f"Latest customer message:\n{text}\n\n"
        f"Grounded policy reason: {policy_reason}\n"
        f"Grounded requested amount: {requested_amount:.2f} USD\n"
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


def _status_terminal(run: SpecialistRun) -> bool:
    return latest_result(run, "get_order_details") is not None


def _research_terminal(run: SpecialistRun) -> bool:
    order = latest_result(run, "get_order_details")
    if isinstance(order, dict) and order.get("error") == "ORDER_NOT_FOUND":
        return True
    profile = latest_result(run, "get_user_profile")
    if isinstance(profile, dict) and profile.get("error") == "USER_NOT_FOUND":
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


def _requested_amount(grounded: GroundedCustomerFacts, risk: RiskReport) -> float:
    if grounded.explicit_amount is not None:
        return round(grounded.explicit_amount, 2)
    return round(float(risk.evidence.get("order_total_usd") or 0.0), 2)


def _project_communication(decision: DecisionHandoff, run: SpecialistRun) -> CommunicationResult:
    if run.failure_reason:
        raise HandoffProjectionError(f"Comms agent failed: {run.failure_reason}")
    route = latest_result(run, "get_escalation_route")
    if not isinstance(route, dict) or "error" in route:
        raise HandoffProjectionError("Comms produced no valid trusted escalation route.")
    alert = latest_result(run, "send_slack_alert")
    if route.get("escalation_required") is True:
        if not isinstance(alert, dict) or "error" in alert or not alert.get("delivered"):
            raise HandoffProjectionError("Escalation route required an alert, but no trusted delivered alert exists.")
    elif alert is not None:
        raise HandoffProjectionError("An alert exists even though the trusted route required no escalation.")

    if decision.refund_status == "APPROVED":
        response = f"Your refund of {decision.approved_amount:.2f} USD has been approved."
    elif decision.refund_status == "REJECTED":
        response = "Your request is not eligible for an automatic refund under the applicable return policy."
    else:
        response = "Your request requires additional review. No refund has been issued yet."

    return CommunicationResult(
        customer_response=response,
        escalation_required=bool(route.get("escalation_required")),
        channel_id=route.get("channel_id"),
        severity=route.get("severity"),
        alert_delivered=bool((alert or {}).get("delivered")),
        alert_transport=(alert or {}).get("transport"),
        message_ts=(alert or {}).get("message_ts"),
    )


def _clarification(
    reason: str,
    customer_response: str,
    *,
    risk: RiskReport | None = None,
) -> CrewResult:
    return CrewResult(
        status="NEEDS_CLARIFICATION",
        risk_report=risk,
        communication=CommunicationResult(
            customer_response=customer_response,
            escalation_required=False,
        ),
        stop_reason=reason,
    )


def _failed_safe(
    reason: str,
    *,
    risk: RiskReport | None = None,
    decision: DecisionHandoff | None = None,
    customer_response: str = "Your request requires additional review. No refund has been issued yet.",
) -> CrewResult:
    return CrewResult(
        status="FAILED_SAFE",
        risk_report=risk,
        decision=decision,
        communication=CommunicationResult(
            customer_response=customer_response,
            escalation_required=False,
        ),
        stop_reason=reason,
    )
