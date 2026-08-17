"""Tests for the system prompt loader."""

from app.prompts import load_system_prompt


def test_prompt_loads_non_empty_text():
    prompt = load_system_prompt()
    assert isinstance(prompt, str)
    assert len(prompt.strip()) > 200


def test_prompt_contains_safety_and_business_anchors():
    prompt = load_system_prompt()
    for anchor in (
        "APPROVED",
        "eligible=false",
        "NEEDS_CLARIFICATION",
        "get_order_details",
        "sentiment",
        "urgency",
        "initial_fraud_score",
        "prior_fraud_flags",
    ):
        assert anchor in prompt


def test_prompt_marks_customer_text_as_untrusted():
    prompt = load_system_prompt()
    assert "untrusted" in prompt
