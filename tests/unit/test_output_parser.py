"""Tests for structured final-output parsing and internal assessment capture."""

import json

import pytest

from app.output_parser import ModelAssessment, OutputParseError, parse_final_output

VALID_RESULT = {
    "status": "COMPLETED",
    "reasoning_chain": ["Verified ORD-1001", "Policy ELIGIBLE", "Refund APPROVED"],
    "action_taken": {
        "tools_called": ["get_order_details", "check_return_policy", "process_refund"],
        "cases": [
            {
                "order_id": "ORD-1001",
                "decision": "AUTO_REFUND_APPROVED",
                "refund_amount": 35.0,
                "refund_id": "RF-1001-3500",
            }
        ],
    },
    "customer_response": "Your refund was approved.",
}


def test_parses_clean_json():
    result, assessment = parse_final_output(json.dumps(VALID_RESULT))
    assert result.status.value == "COMPLETED"
    assert result.action_taken.cases[0].refund_id == "RF-1001-3500"
    assert assessment == ModelAssessment()


def test_parses_fenced_json_block():
    content = f"Here is the result:\n```json\n{json.dumps(VALID_RESULT)}\n```"
    result, _ = parse_final_output(content)
    assert result.customer_response == "Your refund was approved."


def test_parses_json_embedded_in_prose():
    content = f"I have concluded the case. {json.dumps(VALID_RESULT)} Thank you."
    result, _ = parse_final_output(content)
    assert result.status.value == "COMPLETED"


def test_invalid_json_raises():
    with pytest.raises(OutputParseError, match="JSON"):
        parse_final_output("{not valid json")


def test_non_object_json_raises():
    with pytest.raises(OutputParseError, match="JSON object"):
        parse_final_output("[1, 2, 3]")


def test_empty_or_none_content_raises():
    with pytest.raises(OutputParseError):
        parse_final_output(None)
    with pytest.raises(OutputParseError):
        parse_final_output("   ")


def test_missing_required_fields_raise():
    payload = dict(VALID_RESULT)
    del payload["reasoning_chain"]
    with pytest.raises(OutputParseError, match="schema validation"):
        parse_final_output(json.dumps(payload))


def test_empty_reasoning_chain_raises():
    payload = dict(VALID_RESULT, reasoning_chain=[])
    with pytest.raises(OutputParseError, match="schema validation"):
        parse_final_output(json.dumps(payload))


def test_assessment_keys_are_captured_and_kept_out_of_agent_result():
    payload = dict(VALID_RESULT, sentiment="frustrated", urgency="high")
    result, assessment = parse_final_output(json.dumps(payload))

    assert assessment.sentiment == "frustrated"
    assert assessment.urgency == "high"
    dumped = result.model_dump()
    assert set(dumped) == {"status", "reasoning_chain", "action_taken", "customer_response"}


def test_non_string_or_blank_assessment_becomes_none():
    payload = dict(VALID_RESULT, sentiment="", urgency=42)
    _, assessment = parse_final_output(json.dumps(payload))
    assert assessment.sentiment is None
    assert assessment.urgency is None


def test_unexpected_top_level_extra_fields_fail_parsing():
    """Fix 9: sentiment/urgency are the only tolerated internal keys."""
    payload = dict(VALID_RESULT, confidence=0.9, recommendation="approve")
    with pytest.raises(OutputParseError, match="schema validation"):
        parse_final_output(json.dumps(payload))


def test_unexpected_nested_extra_fields_fail_parsing():
    payload = dict(VALID_RESULT)
    payload["action_taken"] = dict(payload["action_taken"])
    payload["action_taken"]["cases"] = [
        dict(payload["action_taken"]["cases"][0], confidence=0.5)
    ]
    with pytest.raises(OutputParseError, match="schema validation"):
        parse_final_output(json.dumps(payload))
