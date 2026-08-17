"""Tests for the supplied-tool access layer (harmless read calls only)."""

import pytest

from app.config import Settings
from app.tools_adapter import ToolKitError, load_toolkit, to_openai_tool_specs

EXPECTED_TOOLS = [
    "check_return_policy",
    "get_order_details",
    "get_user_profile",
    "process_refund",
]


@pytest.fixture(scope="module")
def kit():
    return load_toolkit(Settings().quest4_starter_kit_path)


def test_loads_from_configured_path(kit):
    assert kit.tool_names == EXPECTED_TOOLS


def test_schema_names_match_registry(kit):
    # Mirrors the supplied verify_scenarios.py "Tool schemas" checks.
    schema_names = sorted(schema["name"] for schema in kit.schemas)
    assert schema_names == EXPECTED_TOOLS
    assert schema_names == kit.tool_names


def test_schemas_are_defensive_copies(kit):
    schemas = kit.schemas
    schemas[0]["name"] = "tampered"
    assert all(schema["name"] != "tampered" for schema in kit.schemas)


def test_openai_conversion_shape_without_mutation(kit):
    before = kit.schemas
    converted = kit.openai_tool_specs()

    assert len(converted) == 4
    for spec in converted:
        assert spec["type"] == "function"
        function = spec["function"]
        assert set(function) == {"name", "description", "parameters"}
        assert function["parameters"]["type"] == "object"

    # Supplied schemas remain Anthropic-shaped and untouched.
    after = kit.schemas
    assert before == after
    assert all("input_schema" in schema for schema in after)


def test_read_call_returns_trusted_order_data(kit):
    order = kit.call("get_order_details", order_id="ORD-1001")
    assert order["status"] == "delivered"
    assert order["total_amount"] == 35.0


def test_business_error_is_data_not_exception(kit):
    result = kit.call("get_order_details", order_id="ORD-9999")
    assert result["error"] == "ORDER_NOT_FOUND"


def test_unknown_tool_name_is_rejected(kit):
    with pytest.raises(ToolKitError, match="Unknown tool"):
        kit.call("delete_everything", order_id="ORD-1001")


def test_missing_starter_kit_path_gives_actionable_error():
    with pytest.raises(ToolKitError, match="bootstrap_upstream"):
        load_toolkit("does/not/exist")
