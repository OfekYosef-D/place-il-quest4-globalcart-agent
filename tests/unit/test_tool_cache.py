"""Tests for the deterministic tool-call cache."""

from app.tool_cache import ToolCache, normalize_args


def test_identical_args_hit_the_cache():
    cache = ToolCache()
    args = {"order_id": "ORD-1001"}
    assert cache.get("get_order_details", args) is None

    cache.put("get_order_details", args, {"status": "delivered"})
    assert cache.get("get_order_details", args) == {"status": "delivered"}
    assert cache.hits == 1
    assert cache.misses == 1


def test_different_args_or_tools_miss():
    cache = ToolCache()
    cache.put("get_order_details", {"order_id": "ORD-1001"}, {"status": "delivered"})

    assert cache.get("get_order_details", {"order_id": "ORD-1002"}) is None
    assert cache.get("get_user_profile", {"order_id": "ORD-1001"}) is None
    assert cache.misses == 2


def test_key_is_independent_of_argument_order():
    assert normalize_args({"a": 1, "b": 2}) == normalize_args({"b": 2, "a": 1})

    cache = ToolCache()
    cache.put("check_return_policy", {"order_id": "ORD-1003", "reason": "changed_mind"}, "result")
    assert cache.get("check_return_policy", {"reason": "changed_mind", "order_id": "ORD-1003"}) == "result"


def test_int_and_float_amounts_are_distinct_keys():
    # Pinned behavior of JSON canonicalization; callers pass floats per schemas.
    cache = ToolCache()
    cache.put("process_refund", {"order_id": "ORD-1001", "amount": 35}, "int-form")
    assert cache.get("process_refund", {"order_id": "ORD-1001", "amount": 35.0}) is None


def test_error_dicts_are_cacheable_values():
    cache = ToolCache()
    error = {"error": "ORDER_NOT_FOUND", "message": "..."}
    cache.put("get_order_details", {"order_id": "ORD-9999"}, error)
    assert cache.get("get_order_details", {"order_id": "ORD-9999"}) == error
