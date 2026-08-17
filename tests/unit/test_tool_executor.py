"""Tests for the guarded, cached tool executor (real supplied tool layer)."""

import json

import pytest

from app.config import Settings
from app.guardrail import GUARDRAIL_REASON
from app.llm.base import ToolCallRequest
from app.state import AgentState, CaseState, ToolInteractionOutcome
from app.tool_cache import ToolCache
from app.tool_executor import TERMINAL_CASE_REASON, ToolExecutor, ToolSystemFailure
from app.tools_adapter import load_toolkit


@pytest.fixture(scope="module")
def kit():
    return load_toolkit(Settings().quest4_starter_kit_path)


@pytest.fixture()
def executor(kit):
    return ToolExecutor(kit, ToolCache())


def _call(name, arguments=None, call_id="call_1", raw=None):
    return ToolCallRequest(
        id=call_id,
        name=name,
        arguments=arguments if arguments is not None else {},
        raw_arguments=raw,
    )


def test_refund_before_eligible_policy_is_blocked_and_not_executed(executor):
    state = AgentState()
    observation, interaction = executor.execute(
        1, _call("process_refund", {"order_id": "ORD-1001", "amount": 35.0}), state
    )
    payload = json.loads(observation)
    assert payload["blocked"] is True
    assert payload["reason_code"] == GUARDRAIL_REASON
    assert interaction.outcome is ToolInteractionOutcome.BLOCKED
    assert interaction.reason_code == GUARDRAIL_REASON
    assert state.cases.get("ORD-1001") is None or state.cases["ORD-1001"].refund_result is None


def test_refund_after_eligible_policy_executes_and_stores_result(executor):
    state = AgentState()
    executor.execute(1, _call("get_order_details", {"order_id": "ORD-1001"}), state)
    executor.execute(
        2, _call("check_return_policy", {"order_id": "ORD-1001", "reason": "damaged_on_arrival"}), state
    )
    assert state.cases["ORD-1001"].policy_result["eligible"] is True

    observation, interaction = executor.execute(
        3,
        _call("process_refund", {"order_id": "ORD-1001", "amount": 35.0, "reason": "damaged_on_arrival"}),
        state,
    )
    result = json.loads(observation)
    assert interaction.outcome is ToolInteractionOutcome.EXECUTED
    assert result["status"] == "APPROVED"
    assert state.cases["ORD-1001"].refund_result == result


def test_identical_repeat_is_served_from_cache(executor):
    state = AgentState()
    first, _ = executor.execute(1, _call("get_order_details", {"order_id": "ORD-1001"}), state)
    second, interaction = executor.execute(
        2, _call("get_order_details", {"order_id": "ORD-1001"}), state
    )
    assert json.loads(second) == json.loads(first)
    assert interaction.outcome is ToolInteractionOutcome.CACHED


def test_cache_hit_applies_trusted_state_just_like_execution(executor):
    state = AgentState()
    executor.execute(1, _call("get_order_details", {"order_id": "ORD-1001"}), state)
    verified_first = state.cases["ORD-1001"].verified_order

    # New state, same executor/cache: the repeat is cache-served but must
    # still apply the trusted fact.
    fresh_state = AgentState()
    executor.execute(1, _call("get_order_details", {"order_id": "ORD-1001"}), fresh_state)
    history = fresh_state.tool_history[-1]
    assert history.outcome is ToolInteractionOutcome.CACHED
    assert fresh_state.cases["ORD-1001"].verified_order == verified_first


def test_unknown_tool_is_structured_data(executor):
    state = AgentState()
    observation, interaction = executor.execute(2, _call("delete_everything"), state)
    assert json.loads(observation)["error"] == "UNKNOWN_TOOL"
    assert interaction.outcome is ToolInteractionOutcome.UNKNOWN_TOOL


def test_unparseable_arguments_are_invalid_not_guessed(executor):
    state = AgentState()
    observation, interaction = executor.execute(
        1, _call("get_order_details", {}, raw="not json"), state
    )
    assert json.loads(observation)["error"] == "INVALID_ARGUMENTS"
    assert interaction.outcome is ToolInteractionOutcome.INVALID_ARGUMENTS


def test_missing_required_argument_is_structural_invalid(executor):
    state = AgentState()
    observation, interaction = executor.execute(1, _call("check_return_policy", {}), state)
    payload = json.loads(observation)
    assert payload["error"] == "INVALID_ARGUMENTS"
    assert payload["missing_required"] == ["order_id"]
    assert interaction.outcome is ToolInteractionOutcome.INVALID_ARGUMENTS
    # Tool was never invoked: no case side effects.
    assert state.cases == {}


def test_unexpected_argument_is_structural_invalid(executor):
    state = AgentState()
    observation, _ = executor.execute(
        1, _call("get_order_details", {"order_id": "ORD-1001", "vip_override": True}), state
    )
    payload = json.loads(observation)
    assert payload["error"] == "INVALID_ARGUMENTS"
    assert payload["unexpected_arguments"] == ["vip_override"]
    # Structural rejection must not leave fabricated facts.
    assert state.cases == {}


def test_tool_business_error_stays_authoritative_data(executor):
    state = AgentState()
    executor.execute(1, _call("get_order_details", {"order_id": "ORD-1001"}), state)
    executor.execute(
        2, _call("check_return_policy", {"order_id": "ORD-1001", "reason": "damaged_on_arrival"}), state
    )
    observation, interaction = executor.execute(
        3, _call("process_refund", {"order_id": "ORD-1001", "amount": -5.0}), state
    )
    payload = json.loads(observation)
    assert payload["error"] == "INVALID_AMOUNT"
    assert interaction.outcome is ToolInteractionOutcome.BUSINESS_ERROR
    assert interaction.reason_code == "INVALID_AMOUNT"


def test_order_not_found_leaves_minimal_case_and_terminal_marker(executor):
    state = AgentState()
    observation, interaction = executor.execute(
        1, _call("get_order_details", {"order_id": "ORD-2222"}), state
    )
    assert json.loads(observation)["error"] == "ORDER_NOT_FOUND"
    assert interaction.outcome is ToolInteractionOutcome.BUSINESS_ERROR
    case = state.cases["ORD-2222"]
    assert case.order_id == "ORD-2222"
    assert case.terminal_error == "ORDER_NOT_FOUND"
    assert case.verified_order is None
    assert case.policy_result is None
    assert case.refund_result is None


def test_terminal_case_blocks_further_order_scoped_calls_but_not_others(executor):
    state = AgentState()
    executor.execute(1, _call("get_order_details", {"order_id": "ORD-2222"}), state)

    observation, interaction = executor.execute(
        2, _call("check_return_policy", {"order_id": "ORD-2222"}), state
    )
    payload = json.loads(observation)
    assert payload["blocked"] is True
    assert payload["terminal_error"] == "ORDER_NOT_FOUND"
    assert interaction.outcome is ToolInteractionOutcome.BLOCKED
    assert interaction.reason_code == TERMINAL_CASE_REASON

    # Independent order continues normally in the same run.
    observation, interaction = executor.execute(
        3, _call("get_order_details", {"order_id": "ORD-1001"}), state
    )
    assert interaction.outcome is ToolInteractionOutcome.EXECUTED
    assert json.loads(observation)["order_id"] == "ORD-1001"


def test_profile_attaches_to_every_case_with_matching_user(executor):
    state = AgentState()
    executor.execute(1, _call("get_order_details", {"order_id": "ORD-1001"}), state)
    executor.execute(2, _call("get_order_details", {"order_id": "ORD-1006"}), state)
    executor.execute(3, _call("get_order_details", {"order_id": "ORD-1002"}), state)

    _, interaction = executor.execute(4, _call("get_user_profile", {"user_id": "USR-101"}), state)
    assert interaction.outcome is ToolInteractionOutcome.EXECUTED

    assert state.cases["ORD-1001"].verified_user is not None
    assert state.cases["ORD-1006"].verified_user is not None
    # Different customer keeps case isolation.
    assert state.cases["ORD-1002"].verified_user is None


class _StubKit:
    """Real schemas, but dispatch that explodes - for SYSTEM_FAILURE behavior."""

    def __init__(self, real_kit):
        self._real = real_kit

    @property
    def schemas(self):
        return self._real.schemas

    @property
    def tool_names(self):
        return self._real.tool_names

    def call(self, name, **arguments):
        raise TypeError("simulated programmer error")


def test_system_failure_is_raised_not_observed_and_not_cached(kit):
    executor = ToolExecutor(_StubKit(kit), ToolCache())
    state = AgentState()
    with pytest.raises(ToolSystemFailure):
        executor.execute(1, _call("get_order_details", {"order_id": "ORD-1001"}), state)

    interaction = state.tool_history[-1]
    assert interaction.outcome is ToolInteractionOutcome.SYSTEM_FAILURE
    # Nothing cached: a healthy executor would re-execute, not replay failure.
    assert executor._cache.misses == 1
    assert executor._cache.hits == 0
