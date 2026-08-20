"""Agent-level coverage for runtime-owned terminal business outcomes."""

import json

import pytest

from app.agent import OperationsResolverAgent
from app.config import Settings
from app.schemas import Decision, FinalStatus
from app.tools_adapter import load_toolkit
from tests.unit.fakes import FakeProvider, final_response, tool_call_response


@pytest.fixture(scope="module")
def kit():
    return load_toolkit(Settings().quest4_starter_kit_path)


def tc(call_id, name, arguments):
    return (call_id, name, arguments)


def test_escalation_business_fields_are_canonicalized_without_scenario_specific_logic(kit):
    wrong_model_final = json.dumps(
        {
            "status": "COMPLETED",
            "reasoning_chain": ["The refund tool required additional review."],
            "action_taken": {
                "tools_called": [
                    "get_order_details",
                    "check_return_policy",
                    "process_refund",
                ],
                "cases": [
                    {
                        "order_id": "ORD-1011",
                        "decision": "AUTO_REFUND_APPROVED",
                        "refund_amount": 52.0,
                        "refund_id": "RF-INVENTED",
                        "policy_verdict": "ELIGIBLE",
                    }
                ],
            },
            "customer_response": (
                "Your request for ORD-1011 requires additional review by our support team. "
                "No refund has been issued."
            ),
            "sentiment": "neutral",
            "urgency": "normal",
        }
    )
    provider = FakeProvider(
        [
            tool_call_response(tc("c1", "get_order_details", {"order_id": "ORD-1011"})),
            tool_call_response(
                tc(
                    "c2",
                    "check_return_policy",
                    {"order_id": "ORD-1011", "reason": "damaged_on_arrival"},
                )
            ),
            tool_call_response(
                tc(
                    "c3",
                    "process_refund",
                    {
                        "order_id": "ORD-1011",
                        "amount": 52.0,
                        "reason": "damaged_on_arrival",
                    },
                )
            ),
            final_response(wrong_model_final),
        ]
    )
    agent = OperationsResolverAgent(Settings(), provider, kit)

    run = agent.handle_customer_message(
        "The item in ORD-1011 arrived damaged. I need a refund for that order."
    )

    assert run.result.status is FinalStatus.COMPLETED
    case = run.result.action_taken.cases[0]
    assert case.decision is Decision.HUMAN_ESCALATION
    assert case.refund_amount is None
    assert case.refund_id is None
    assert not any(call.kind == "repair" for call in run.model_calls)


def test_terminal_order_not_found_is_completed_no_action_even_if_model_labels_clarification(kit):
    model_final = json.dumps(
        {
            "status": "NEEDS_CLARIFICATION",
            "reasoning_chain": ["ORD-2222 was not found."],
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
                "I could not find ORD-2222. Please confirm the order number."
            ),
            "sentiment": "neutral",
            "urgency": "normal",
        }
    )
    provider = FakeProvider(
        [
            tool_call_response(tc("c1", "get_order_details", {"order_id": "ORD-2222"})),
            final_response(model_final),
        ]
    )
    agent = OperationsResolverAgent(Settings(), provider, kit)

    run = agent.handle_customer_message(
        "My order ORD-2222 never arrived and I want the $300 back."
    )

    assert run.result.status is FinalStatus.COMPLETED
    case = run.result.action_taken.cases[0]
    assert case.decision is Decision.NO_ACTION
    assert case.error_code == "ORDER_NOT_FOUND"
    assert not any(call.kind == "repair" for call in run.model_calls)
