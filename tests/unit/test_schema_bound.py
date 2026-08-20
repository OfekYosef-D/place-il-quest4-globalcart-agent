"""Tests for provider-neutral final-response schema binding."""

from app.llm.schema_bound import SchemaBoundProvider
from tests.unit.fakes import FakeProvider, final_response

DEFAULT_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
}
TOOL = {
    "name": "get_order_details",
    "description": "Look up an order.",
    "input_schema": {"type": "object", "properties": {}},
}


def test_capable_provider_gets_default_schema_on_tool_enabled_calls():
    inner = FakeProvider(
        [final_response('{"status":"COMPLETED"}')],
        supports_response_schema=True,
        supports_response_schema_with_tools=True,
    )
    provider = SchemaBoundProvider(inner, DEFAULT_SCHEMA)

    provider.generate([], [TOOL])

    assert inner.response_schemas == [DEFAULT_SCHEMA]
    assert inner.calls[0][1] == [TOOL]


def test_provider_that_cannot_mix_schema_and_tools_keeps_normal_tool_call():
    inner = FakeProvider(
        [final_response("done")],
        supports_response_schema=True,
        supports_response_schema_with_tools=False,
    )
    provider = SchemaBoundProvider(inner, DEFAULT_SCHEMA)

    provider.generate([], [TOOL])

    assert inner.response_schemas == [None]


def test_explicit_no_tools_schema_passes_through_for_repair_calls():
    inner = FakeProvider(
        [final_response('{"status":"COMPLETED"}')],
        supports_response_schema=True,
    )
    provider = SchemaBoundProvider(inner, DEFAULT_SCHEMA)
    repair_schema = {"type": "object", "properties": {"fixed": {"type": "boolean"}}}

    provider.generate([], None, response_schema=repair_schema)

    assert inner.response_schemas == [repair_schema]
