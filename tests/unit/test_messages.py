"""Tests for the canonical conversation message contract."""

import pytest
from pydantic import ValidationError

from app.llm.base import ToolCallRequest
from app.messages import (
    AssistantToolCallMessage,
    CanonicalMessage,
    ToolObservationMessage,
)


def test_canonical_message_accepts_only_text_roles():
    for role in ("system", "user", "assistant"):
        assert CanonicalMessage(role=role, content="hi").role == role
    with pytest.raises(ValidationError):
        CanonicalMessage(role="tool", content="hi")


def test_assistant_tool_call_message_requires_at_least_one_call():
    with pytest.raises(ValidationError):
        AssistantToolCallMessage(tool_calls=[])

    message = AssistantToolCallMessage(
        tool_calls=[ToolCallRequest(id="call_1", name="get_order_details")]
    )
    assert message.tool_calls[0].name == "get_order_details"
    assert message.content is None


def test_tool_observation_preserves_call_id_correlation():
    observation = ToolObservationMessage(
        tool_call_id="call_42", tool_name="get_order_details", content='{"status": "delivered"}'
    )
    assert observation.tool_call_id == "call_42"
    assert observation.tool_name == "get_order_details"


def test_messages_round_trip_through_model_validate():
    originals = [
        CanonicalMessage(role="system", content="You are the resolver."),
        AssistantToolCallMessage(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1", name="get_order_details", arguments={"order_id": "ORD-1001"}
                )
            ],
        ),
        ToolObservationMessage(tool_call_id="call_1", tool_name="get_order_details", content="{}"),
    ]
    for original in originals:
        restored = type(original).model_validate(original.model_dump())
        assert restored == original
