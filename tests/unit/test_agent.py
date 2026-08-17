"""Integration-style tests for the autonomous operations resolver runtime.

Uses the scripted FakeProvider (never a real LLM) and the real supplied tool
layer. Proves: no hardcoded workflow, guardrail blocking + recovery, terminal
stop, multi-order isolation, cross-turn continuation with per-turn resets,
cache continuity, repair path, assessment capture, and fail-safe behavior.
"""

import json
from types import SimpleNamespace

import pytest

from app.agent import OperationsResolverAgent
from app.config import Settings
from app.llm.base import TransientLLMFailure
from app.schemas import Decision, FinalStatus
from app.state import RuntimeStatus, ToolInteractionOutcome
from app.tools_adapter import ToolKit, load_toolkit
from app.validator import validate_result

from tests.unit.fakes import FakeProvider, final_response, tool_call_response


@pytest.fixture(scope="module")
def kit():
    return load_toolkit(Settings().quest4_starter_kit_path)


def make_agent(scripted, kit, **settings_overrides):
    provider = FakeProvider(list(scripted))
    agent = OperationsResolverAgent(Settings(**settings_overrides), provider, kit)
    return agent, provider


def tc(call_id, name, arguments):
    return (call_id, name, arguments)


ORDER_1001 = {"order_id": "ORD-1001"}
POLICY_1001 = {"order_id": "ORD-1001", "reason": "damaged_on_arrival"}
REFUND_1001 = {"order_id": "ORD-1001", "amount": 35.0, "reason": "damaged_on_arrival"}


def approved_final(extra=None):
    payload = {
        "status": "COMPLETED",
        "reasoning_chain": [
            "Verified ORD-1001",
            "Policy verdict ELIGIBLE",
            "process_refund returned APPROVED",
        ],
        "action_taken": {
            "tools_called": [
                "get_order_details",
                "get_user_profile",
                "check_return_policy",
                "process_refund",
            ],
            "cases": [
                {
                    "order_id": "ORD-1001",
                    "decision": "AUTO_REFUND_APPROVED",
                    "refund_amount": 35.0,
                    "refund_id": "RF-1001-3500",
                    "policy_verdict": "ELIGIBLE",
                }
            ],
        },
        "customer_response": "Your refund was approved.",
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload)


def test_happy_path_with_model_chosen_ordering(kit):
    """Model picks user-profile before policy; runtime hardcodes no ordering."""
    script = [
        tool_call_response(tc("c1", "get_order_details", ORDER_1001)),
        tool_call_response(tc("c2", "get_user_profile", {"user_id": "USR-101"})),
        tool_call_response(tc("c3", "check_return_policy", POLICY_1001)),
        tool_call_response(tc("c4", "process_refund", REFUND_1001)),
        final_response(approved_final({"sentiment": "frustrated", "urgency": "high"})),
    ]
    agent, provider = make_agent(script, kit)
    run = agent.handle_customer_message("Order ORD-1001 arrived damaged, please refund me.")

    assert run.result.status is FinalStatus.COMPLETED
    case = run.result.action_taken.cases[0]
    assert case.decision is Decision.AUTO_REFUND_APPROVED
    assert case.refund_amount == 35.0
    assert case.refund_id == "RF-1001-3500"
    assert all(
        interaction.outcome is ToolInteractionOutcome.EXECUTED
        for interaction in run.tool_interactions
    )

    # Assessment is internal-only: state yes, AgentResult no.
    assert run.state.sentiment == "frustrated"
    assert run.state.urgency == "high"
    assert set(run.result.model_dump()) == {
        "status",
        "reasoning_chain",
        "action_taken",
        "customer_response",
    }
    assert validate_result(run.result, run.state) == []


def test_refund_before_policy_is_blocked_then_model_recovers(kit):
    script = [
        tool_call_response(tc("c1", "process_refund", REFUND_1001)),
        tool_call_response(tc("c2", "get_order_details", ORDER_1001)),
        tool_call_response(tc("c3", "check_return_policy", POLICY_1001)),
        tool_call_response(tc("c4", "process_refund", REFUND_1001)),
        final_response(approved_final()),
    ]
    agent, _ = make_agent(script, kit)
    run = agent.handle_customer_message("Refund ORD-1001, damaged on arrival.")

    assert len(run.blocked_events) == 1
    assert run.blocked_events[0].reason == "MISSING_ELIGIBLE_POLICY_PRECONDITION"
    blocked = [
        interaction
        for interaction in run.state.tool_history
        if interaction.outcome is ToolInteractionOutcome.BLOCKED
    ]
    assert len(blocked) == 1
    assert run.result.status is FinalStatus.COMPLETED
    assert run.result.action_taken.cases[0].refund_id == "RF-1001-3500"


def test_ineligible_policy_rejected_without_executing_refund(kit):
    script = [
        tool_call_response(tc("c1", "get_order_details", {"order_id": "ORD-1003"})),
        tool_call_response(
            tc("c2", "check_return_policy", {"order_id": "ORD-1003", "reason": "changed_mind"})
        ),
        tool_call_response(
            tc("c3", "process_refund", {"order_id": "ORD-1003", "amount": 40.0, "reason": "changed_mind"})
        ),
        final_response(
            json.dumps(
                {
                    "status": "COMPLETED",
                    "reasoning_chain": [
                        "Verified ORD-1003",
                        "Policy verdict OUTSIDE_RETURN_WINDOW (eligible=false)",
                        "Rejected from the trusted policy result without calling process_refund",
                    ],
                    "action_taken": {
                        "tools_called": ["get_order_details", "check_return_policy"],
                        "cases": [
                            {
                                "order_id": "ORD-1003",
                                "decision": "REJECTED",
                                "policy_verdict": "OUTSIDE_RETURN_WINDOW",
                            }
                        ],
                    },
                    "customer_response": (
                        "Unfortunately this request is outside the return window, "
                        "so we cannot approve the refund."
                    ),
                }
            )
        ),
    ]
    agent, _ = make_agent(script, kit)
    run = agent.handle_customer_message("I changed my mind about ORD-1003, refund please.")

    assert run.result.status is FinalStatus.COMPLETED
    case_result = run.result.action_taken.cases[0]
    assert case_result.decision is Decision.REJECTED

    case = run.state.cases["ORD-1003"]
    assert case.refund_result is None, "process_refund must never execute when ineligible"
    refund_executions = [
        interaction
        for interaction in run.state.tool_history
        if interaction.tool_name == "process_refund"
        and interaction.outcome in (ToolInteractionOutcome.EXECUTED, ToolInteractionOutcome.CACHED)
    ]
    assert refund_executions == []
    assert len(run.blocked_events) == 1


def test_terminal_error_stops_case_and_blocks_further_order_scoped_tools(kit):
    script = [
        tool_call_response(tc("c1", "get_order_details", {"order_id": "ORD-2222"})),
        tool_call_response(
            tc("c2", "check_return_policy", {"order_id": "ORD-2222", "reason": "wrong_item"})
        ),
        final_response(
            json.dumps(
                {
                    "status": "COMPLETED",
                    "reasoning_chain": [
                        "get_order_details returned ORDER_NOT_FOUND for ORD-2222",
                        "Case is terminally resolved; no further checks executed",
                    ],
                    "action_taken": {
                        "tools_called": ["get_order_details"],
                        "cases": [
                            {
                                "order_id": "ORD-2222",
                                "decision": "NO_ACTION",
                                "error_code": "ORDER_NOT_FOUND",
                            }
                        ],
                    },
                    "customer_response": (
                        "We could not find order ORD-2222. Please confirm the order number."
                    ),
                }
            )
        ),
    ]
    agent, _ = make_agent(script, kit)
    run = agent.handle_customer_message("Refund order ORD-2222, wrong item sent.")

    assert run.result.status is FinalStatus.COMPLETED
    case_result = run.result.action_taken.cases[0]
    assert case_result.decision is Decision.NO_ACTION
    assert case_result.error_code == "ORDER_NOT_FOUND"

    case = run.state.cases["ORD-2222"]
    assert case.terminal_error == "ORDER_NOT_FOUND"
    assert case.verified_order is None
    assert case.policy_result is None
    assert case.refund_result is None

    second = run.tool_interactions[1]
    assert second.outcome is ToolInteractionOutcome.BLOCKED
    assert second.reason_code == "CASE_TERMINAL_ERROR"


def test_multi_order_run_isolates_cases_and_consolidates_response(kit):
    script = [
        tool_call_response(tc("c1", "get_order_details", ORDER_1001)),
        tool_call_response(tc("c2", "get_order_details", {"order_id": "ORD-2222"})),
        tool_call_response(tc("c3", "get_user_profile", {"user_id": "USR-101"})),
        tool_call_response(tc("c4", "check_return_policy", POLICY_1001)),
        tool_call_response(tc("c5", "process_refund", REFUND_1001)),
        final_response(
            json.dumps(
                {
                    "status": "COMPLETED",
                    "reasoning_chain": [
                        "Verified ORD-1001 and ORD-2222 independently",
                        "ORD-1001 refund APPROVED",
                        "ORD-2222 ORDER_NOT_FOUND",
                    ],
                    "action_taken": {
                        "tools_called": [
                            "get_order_details",
                            "get_user_profile",
                            "check_return_policy",
                            "process_refund",
                        ],
                        "cases": [
                            {
                                "order_id": "ORD-1001",
                                "decision": "AUTO_REFUND_APPROVED",
                                "refund_amount": 35.0,
                                "refund_id": "RF-1001-3500",
                                "policy_verdict": "ELIGIBLE",
                            },
                            {
                                "order_id": "ORD-2222",
                                "decision": "NO_ACTION",
                                "error_code": "ORDER_NOT_FOUND",
                            },
                        ],
                    },
                    "customer_response": (
                        "ORD-1001 refund was approved. We could not find ORD-2222; "
                        "please confirm that order number."
                    ),
                }
            )
        ),
    ]
    agent, _ = make_agent(script, kit)
    run = agent.handle_customer_message("Refund ORD-1001 (damaged) and ORD-2222 (wrong item).")

    assert run.result.status is FinalStatus.COMPLETED
    decisions = {c.order_id: c.decision for c in run.result.action_taken.cases}
    assert decisions["ORD-1001"] is Decision.AUTO_REFUND_APPROVED
    assert decisions["ORD-2222"] is Decision.NO_ACTION

    # Profile attaches only to the matching customer's case.
    assert run.state.cases["ORD-1001"].verified_user is not None
    assert run.state.cases["ORD-2222"].verified_user is None
    assert validate_result(run.result, run.state) == []


def test_needs_clarification_then_continuation_resolves(kit):
    clarification = json.dumps(
        {
            "status": "NEEDS_CLARIFICATION",
            "reasoning_chain": ["Customer reports damage but provided no order id"],
            "action_taken": {"tools_called": [], "cases": []},
            "customer_response": "Could you please provide the order number?",
        }
    )
    agent, _ = make_agent([final_response(clarification)], kit)
    run1 = agent.handle_customer_message("My order arrived broken, I want a refund!")
    assert run1.result.status is FinalStatus.NEEDS_CLARIFICATION
    assert run1.state.status is RuntimeStatus.NEEDS_CLARIFICATION

    script2 = [
        tool_call_response(tc("c1", "get_order_details", ORDER_1001)),
        tool_call_response(tc("c2", "check_return_policy", POLICY_1001)),
        tool_call_response(tc("c3", "process_refund", REFUND_1001)),
        final_response(approved_final()),
    ]
    provider2 = FakeProvider(script2)
    agent2 = OperationsResolverAgent(Settings(), provider2, kit)
    run2 = agent2.handle_customer_message("It's ORD-1001.", previous=run1)

    assert run2.result.status is FinalStatus.COMPLETED
    # Short-term conversation state continued across turns.
    user_texts = [m.content for m in run2.state.messages if getattr(m, "role", None) == "user"]
    assert user_texts == ["My order arrived broken, I want a refund!", "It's ORD-1001."]
    # Per-turn reset: counters start fresh on the new turn.
    assert run2.model_calls[0].step == 1
    assert run2.state.failure_reason is None


def test_continuation_reseeds_cache_and_resets_per_turn_fields(kit):
    """Turn 1 exhausts its budget after one read; turn 2 continues with cache hit."""
    script1 = [tool_call_response(tc("c1", "get_order_details", ORDER_1001))]
    agent1, _ = make_agent(script1, kit, agent_max_steps=1)
    run1 = agent1.handle_customer_message("Refund ORD-1001, damaged.")

    assert run1.result.status is FinalStatus.FAILED_SAFE
    assert run1.state.failure_reason is not None
    assert run1.state.cases["ORD-1001"].verified_order is not None

    script2 = [
        tool_call_response(tc("c1", "get_order_details", ORDER_1001)),
        tool_call_response(tc("c2", "check_return_policy", POLICY_1001)),
        tool_call_response(tc("c3", "process_refund", REFUND_1001)),
        final_response(approved_final()),
    ]
    provider2 = FakeProvider(script2)
    agent2 = OperationsResolverAgent(Settings(), provider2, kit)
    run2 = agent2.handle_customer_message("Please continue with the refund.", previous=run1)

    assert run2.result.status is FinalStatus.COMPLETED
    # Identical repeated read served from the reseeded session cache.
    assert run2.tool_interactions[0].outcome is ToolInteractionOutcome.CACHED
    assert run2.tool_interactions[0].result == run1.state.cases["ORD-1001"].verified_order
    # Per-turn runtime fields were reset before processing the new message.
    assert run2.state.step_count > 0
    assert run2.model_calls[0].step == 1
    assert run2.state.failure_reason is None


def test_malformed_final_output_uses_single_ephemeral_repair(kit):
    script = [
        tool_call_response(tc("c1", "get_order_details", ORDER_1001)),
        tool_call_response(tc("c2", "check_return_policy", POLICY_1001)),
        tool_call_response(tc("c3", "process_refund", REFUND_1001)),
        final_response("Sorry, I forgot the JSON format. Refund approved!"),
        final_response(approved_final({"sentiment": "calm", "urgency": "low"})),
    ]
    agent, provider = make_agent(script, kit)
    run = agent.handle_customer_message("Refund ORD-1001, damaged on arrival.")

    assert run.result.status is FinalStatus.COMPLETED
    assert run.result.action_taken.cases[0].refund_id == "RF-1001-3500"
    # Repair call used tools=None and assessment was captured from the repair.
    repair_calls = [call for call in provider.calls if call[1] is None]
    assert len(repair_calls) == 1
    assert run.state.sentiment == "calm"
    assert run.state.urgency == "low"
    # Repair exchange never contaminates conversation state.
    assert not any(
        "Problems found" in (getattr(message, "content", "") or "")
        for message in run.state.messages
    )
    assert any(call.kind == "repair" for call in run.model_calls)


def test_max_steps_exhaustion_fails_safe_preserving_evidence(kit):
    script = [
        tool_call_response(tc("c1", "get_order_details", ORDER_1001)),
        tool_call_response(tc("c2", "get_user_profile", {"user_id": "USR-101"})),
    ]
    agent, _ = make_agent(script, kit, agent_max_steps=2)
    run = agent.handle_customer_message("Refund ORD-1001.")

    assert run.result.status is FinalStatus.FAILED_SAFE
    assert run.state.status is RuntimeStatus.FAILED_SAFE
    assert run.state.failure_reason == "MAX_STEPS_EXCEEDED"
    case_result = run.result.action_taken.cases[0]
    # Verified-but-unresolved case is escalated, not invented or dropped.
    assert case_result.order_id == "ORD-1001"
    assert case_result.decision is Decision.HUMAN_ESCALATION
    assert validate_result(run.result, run.state) == []


def test_no_progress_repeated_calls_fail_safe(kit):
    identical = tool_call_response(tc("c1", "get_order_details", ORDER_1001))
    agent, provider = make_agent([identical] * 4, kit)
    run = agent.handle_customer_message("Refund ORD-1001.")

    assert run.result.status is FinalStatus.FAILED_SAFE
    assert "NO_PROGRESS" in run.state.failure_reason
    assert len(provider.calls) == 4, "loop stops as soon as no-progress is detected"


def test_transient_llm_failure_retried_then_succeeds(kit):
    clarification = json.dumps(
        {
            "status": "NEEDS_CLARIFICATION",
            "reasoning_chain": ["No order id provided"],
            "action_taken": {"tools_called": [], "cases": []},
            "customer_response": "Please share your order number.",
        }
    )
    agent, provider = make_agent([TransientLLMFailure("503"), final_response(clarification)], kit)
    run = agent.handle_customer_message("I need a refund!")

    assert run.result.status is FinalStatus.NEEDS_CLARIFICATION
    assert run.model_calls[0].retries == 1


def test_non_transient_provider_failure_preserves_resolved_outcomes(kit):
    script = [
        tool_call_response(tc("c1", "get_order_details", ORDER_1001)),
        tool_call_response(tc("c2", "check_return_policy", POLICY_1001)),
        tool_call_response(tc("c3", "process_refund", REFUND_1001)),
        ValueError("provider configuration error"),
    ]
    agent, _ = make_agent(script, kit)
    run = agent.handle_customer_message("Refund ORD-1001, damaged.")

    assert run.result.status is FinalStatus.FAILED_SAFE
    assert "LLM_FAILURE" in run.state.failure_reason
    case_result = run.result.action_taken.cases[0]
    # Already-resolved trusted outcome survives the system failure.
    assert case_result.decision is Decision.AUTO_REFUND_APPROVED
    assert case_result.refund_id == "RF-1001-3500"
    assert validate_result(run.result, run.state) == []


def test_tool_system_failure_fails_safe_without_retry(kit):
    """A genuine tool exception is traced and fails safe, never retried (clarification 4)."""

    def exploding_tool(**_kwargs):
        raise RuntimeError("deterministic local tool crashed")

    broken_module = SimpleNamespace(
        TOOL_SCHEMAS=[schema for schema in kit.schemas if schema["name"] == "get_order_details"],
        TOOL_REGISTRY={"get_order_details": exploding_tool},
    )
    broken_kit = ToolKit(broken_module)

    agent, provider = make_agent(
        [tool_call_response(tc("c1", "get_order_details", ORDER_1001))], broken_kit
    )
    run = agent.handle_customer_message("Refund ORD-1001.")

    assert run.result.status is FinalStatus.FAILED_SAFE
    assert "TOOL_SYSTEM_FAILURE" in run.state.failure_reason
    failure_interactions = [
        interaction
        for interaction in run.state.tool_history
        if interaction.outcome is ToolInteractionOutcome.SYSTEM_FAILURE
    ]
    assert len(failure_interactions) == 1, "system failure is recorded once, never retried"
    assert len(provider.calls) == 1
    # No tool observation was fabricated for the crashed call.
    last_message = run.state.messages[-1]
    assert type(last_message).__name__ == "AssistantToolCallMessage"
    assert validate_result(run.result, run.state) == []
