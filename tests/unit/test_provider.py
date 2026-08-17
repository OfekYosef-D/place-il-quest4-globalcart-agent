"""Tests for provider contracts and normalization (no network access)."""

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm.base import LLMProvider, ModelResponse
from app.llm.groq_provider import GroqProvider, normalize_response


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
