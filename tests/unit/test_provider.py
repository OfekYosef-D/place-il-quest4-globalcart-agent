"""Tests for provider contracts and normalization (no network access)."""

import copy
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm.base import LLMProvider, ModelResponse, ToolCallRequest
from app.llm.groq_provider import (
    GroqProvider,
    normalize_response,
    to_openai_function_tools,
    to_wire_messages,
)
from app.messages import (
    AssistantToolCallMessage,
    CanonicalMessage,
    ToolObservationMessage,
)


CANONICAL_SCHEMA = {
    "name": "get_order_details",
    "description": "Look up a GlobalCart order by id.",
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    },
}


def _fake_completion(content=None, tool_calls=None, usage=None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        model="test-model",
        usage=usage,
    )


def _fake_tool_call(name: str, arguments: str, call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=arguments)
    )


def test_normalizes_final_text_response():
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    raw = _fake_completion(content="Done.", usage=usage)

    response = normalize_response(
        raw, provider="groq", fallback_model="fallback", latency_ms=12.5
    )

    assert isinstance(response, ModelResponse)
    assert response.content == "Done."
    assert response.tool_calls == []
    assert response.provider == "groq"
    assert response.model == "test-model"
    assert response.input_tokens == 10
    assert response.output_tokens == 5
    assert response.total_tokens == 15
    assert response.latency_ms == 12.5
    assert response.raw_usage is None  # SimpleNamespace has no model_dump


def test_normalizes_tool_call_arguments():
    raw = _fake_completion(
        tool_calls=[_fake_tool_call("get_order_details", '{"order_id": "ORD-1001"}')]
    )

    response = normalize_response(
        raw, provider="groq", fallback_model="fallback", latency_ms=1.0
    )

    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.name == "get_order_details"
    assert call.arguments == {"order_id": "ORD-1001"}
    assert call.raw_arguments is None
    assert call.id == "call_1"


def test_unparseable_tool_arguments_are_kept_raw_not_guessed():
    raw = _fake_completion(tool_calls=[_fake_tool_call("process_refund", "not json")])

    response = normalize_response(
        raw, provider="groq", fallback_model="fallback", latency_ms=1.0
    )

    call = response.tool_calls[0]
    assert call.arguments == {}
    assert call.raw_arguments == "not json"


def test_missing_usage_yields_none_tokens():
    raw = _fake_completion(content="hi", usage=None)
    response = normalize_response(
        raw, provider="groq", fallback_model="fallback", latency_ms=0.5
    )
    assert response.input_tokens is None
    assert response.output_tokens is None
    assert response.total_tokens is None


def test_missing_model_falls_back_to_configured_model():
    raw = _fake_completion(content="hi")
    raw.model = None
    response = normalize_response(
        raw, provider="groq", fallback_model="configured-model", latency_ms=0.5
    )
    assert response.model == "configured-model"


def test_provider_requires_api_key():
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqProvider(Settings(llm_model="some-model"))


def test_provider_requires_model():
    with pytest.raises(ValueError, match="LLM_MODEL"):
        GroqProvider(Settings(groq_api_key="test-key"))


def test_provider_constructs_offline_and_satisfies_protocol():
    provider = GroqProvider(Settings(groq_api_key="test-key", llm_model="some-model"))
    assert isinstance(provider, LLMProvider)


def test_canonical_schema_converts_to_openai_function_shape_without_mutation():
    before = copy.deepcopy(CANONICAL_SCHEMA)

    converted = to_openai_function_tools([CANONICAL_SCHEMA])

    assert len(converted) == 1
    spec = converted[0]
    assert spec["type"] == "function"
    assert set(spec["function"]) == {"name", "description", "parameters"}
    assert spec["function"]["name"] == "get_order_details"
    assert spec["function"]["parameters"] == CANONICAL_SCHEMA["input_schema"]

    # Canonical schema is never mutated by the provider-layer conversion.
    assert CANONICAL_SCHEMA == before
    assert "input_schema" in CANONICAL_SCHEMA


def test_converted_parameters_are_independent_of_canonical_schema():
    converted = to_openai_function_tools([CANONICAL_SCHEMA])
    converted[0]["function"]["parameters"]["properties"]["order_id"]["type"] = "number"
    assert CANONICAL_SCHEMA["input_schema"]["properties"]["order_id"]["type"] == "string"


def test_wire_conversion_of_text_messages():
    messages = [
        CanonicalMessage(role="system", content="You are the resolver."),
        CanonicalMessage(role="user", content="My order arrived damaged."),
    ]
    wire = to_wire_messages(messages)
    assert wire == [
        {"role": "system", "content": "You are the resolver."},
        {"role": "user", "content": "My order arrived damaged."},
    ]


def test_wire_conversion_of_tool_exchange_preserves_call_id():
    messages = [
        AssistantToolCallMessage(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_7", name="get_order_details", arguments={"order_id": "ORD-1001"}
                )
            ],
        ),
        ToolObservationMessage(
            tool_call_id="call_7", tool_name="get_order_details", content='{"status": "delivered"}'
        ),
    ]
    wire = to_wire_messages(messages)

    assistant = wire[0]
    assert assistant["role"] == "assistant"
    assert assistant["content"] is None
    assert assistant["tool_calls"] == [
        {
            "id": "call_7",
            "type": "function",
            "function": {"name": "get_order_details", "arguments": '{"order_id": "ORD-1001"}'},
        }
    ]

    tool = wire[1]
    assert tool["role"] == "tool"
    assert tool["tool_call_id"] == "call_7"
    assert tool["content"] == '{"status": "delivered"}'


def test_wire_conversion_requires_tool_call_ids():
    """Ambiguous placeholder fallbacks are gone: missing ids fail loudly."""
    with pytest.raises(ValueError, match="without.*ids"):
        to_wire_messages(
            [
                AssistantToolCallMessage(
                    tool_calls=[ToolCallRequest(id=None, name="get_user_profile", arguments={})]
                )
            ]
        )
    with pytest.raises(ValueError, match="tool_call_id"):
        to_wire_messages(
            [ToolObservationMessage(tool_call_id=None, tool_name="get_user_profile", content="{}")]
        )


def test_normalize_response_assigns_unique_ids_when_provider_omits_them():
    """Fix 8: multiple raw tool calls with missing ids get stable unique ids."""
    raw = _fake_completion(
        tool_calls=[
            _fake_tool_call("get_order_details", '{"order_id": "ORD-1001"}', call_id=None),
            _fake_tool_call("get_user_profile", '{"user_id": "USR-101"}', call_id=None),
            _fake_tool_call("check_return_policy", '{"order_id": "ORD-1001"}', call_id="call_keep"),
        ]
    )

    response = normalize_response(
        raw, provider="groq", fallback_model="fallback", latency_ms=1.0
    )

    ids = [call.id for call in response.tool_calls]
    assert all(ids), "every tool call must carry a non-empty id"
    assert len(set(ids)) == 3, "generated ids must be unique across the batch"
    assert ids[2] == "call_keep", "provider-supplied ids are preserved exactly"


def test_normalize_response_resolves_duplicate_and_empty_tool_call_ids():
    """Fix 3: duplicated non-empty ids and empty ids are regenerated while the
    first valid occurrence is preserved; uniqueness survives wire conversion."""
    raw = _fake_completion(
        tool_calls=[
            _fake_tool_call("get_order_details", '{"order_id": "ORD-1001"}', call_id="call_dup"),
            _fake_tool_call("get_order_details", '{"order_id": "ORD-1010"}', call_id="call_dup"),
            _fake_tool_call("get_user_profile", '{"user_id": "USR-101"}', call_id=""),
        ]
    )

    response = normalize_response(
        raw, provider="groq", fallback_model="fallback", latency_ms=1.0
    )

    ids = [call.id for call in response.tool_calls]
    assert all(ids), "every tool call must carry a non-empty id"
    assert len(set(ids)) == 3, "duplicated and empty ids must be made unique"
    assert ids[0] == "call_dup", "the first valid occurrence is preserved"

    # Correlation survives wire conversion: every assistant tool-call id is
    # matched by exactly one tool result carrying the same id.
    messages = [
        AssistantToolCallMessage(content=None, tool_calls=list(response.tool_calls)),
        *[
            ToolObservationMessage(tool_call_id=call.id, tool_name=call.name, content="{}")
            for call in response.tool_calls
        ],
    ]
    wire = to_wire_messages(messages)
    wire_request_ids = [wire_call["id"] for wire_call in wire[0]["tool_calls"]]
    wire_result_ids = [entry["tool_call_id"] for entry in wire[1:]]
    assert wire_request_ids == ids
    assert sorted(wire_result_ids) == sorted(ids)
    assert len(set(wire_result_ids)) == 3


def test_wire_conversion_rejects_unknown_message_types():
    with pytest.raises(TypeError, match="Unsupported canonical message"):
        to_wire_messages([{"role": "user", "content": "raw dict"}])


def test_wire_conversion_does_not_mutate_canonical_inputs():
    message = AssistantToolCallMessage(
        tool_calls=[ToolCallRequest(id="call_1", name="check_return_policy", arguments={"order_id": "ORD-1001"})]
    )
    before = message.model_dump()
    to_wire_messages([message])
    assert message.model_dump() == before
