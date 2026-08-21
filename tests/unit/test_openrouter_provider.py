"""Offline tests for the OpenRouter adapter; no network calls."""

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.openrouter_provider import OpenRouterProvider
from app.messages import CanonicalMessage

TOOL_SCHEMA = {
    "name": "get_order_details",
    "description": "Look up an order.",
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    },
}


class _FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content='{"status":"ok"}', tool_calls=[])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            model=kwargs["model"],
            usage=None,
        )


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def _provider() -> OpenRouterProvider:
    provider = OpenRouterProvider(
        Settings(
            llm_provider="openrouter",
            openrouter_api_key="test-key",
            llm_model="qwen/qwen3.5-397b-a17b",
        )
    )
    provider._client = _FakeClient()
    return provider


def test_openrouter_requires_api_key_and_model():
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenRouterProvider(Settings(llm_provider="openrouter", llm_model="model"))
    with pytest.raises(ValueError, match="LLM_MODEL"):
        OpenRouterProvider(Settings(llm_provider="openrouter", openrouter_api_key="key"))


def test_openrouter_satisfies_provider_protocol_and_capabilities():
    provider = _provider()
    assert isinstance(provider, LLMProvider)
    assert provider.supports_response_schema is True


def test_tool_call_request_uses_openai_shape_and_requires_parameter_support():
    provider = _provider()
    provider.generate(
        [CanonicalMessage(role="user", content="check ORD-1001")],
        [TOOL_SCHEMA],
    )

    kwargs = provider._client.chat.completions.kwargs
    assert kwargs["model"] == "qwen/qwen3.5-397b-a17b"
    assert kwargs["tools"][0]["type"] == "function"
    assert kwargs["tools"][0]["function"]["name"] == "get_order_details"
    assert kwargs["extra_body"] == {"provider": {"require_parameters": True}}
    assert "response_format" not in kwargs


def test_structured_no_tools_request_uses_strict_native_json_schema():
    provider = _provider()
    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        },
        "required": ["status"],
    }
    provider.generate(
        [CanonicalMessage(role="user", content="finalize")],
        None,
        response_schema=schema,
    )

    kwargs = provider._client.chat.completions.kwargs
    assert "tools" not in kwargs
    strict_schema = kwargs["response_format"]["json_schema"]["schema"]
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
    assert strict_schema["required"] == ["status", "note"]
    assert strict_schema["additionalProperties"] is False
    assert "default" not in strict_schema["properties"]["note"]
    assert kwargs["extra_body"] == {"provider": {"require_parameters": True}}


def test_schema_and_tools_are_not_mixed_without_live_verification():
    provider = _provider()
    with pytest.raises(ValueError, match="tools=None"):
        provider.generate(
            [CanonicalMessage(role="user", content="check then resolve")],
            [TOOL_SCHEMA],
            response_schema={"type": "object"},
        )


def test_unverified_model_rejects_strict_response_schema():
    provider = OpenRouterProvider(
        Settings(
            llm_provider="openrouter",
            openrouter_api_key="test-key",
            llm_model="some/other-model",
        )
    )
    provider._client = _FakeClient()
    with pytest.raises(ValueError, match="not verified"):
        provider.generate(
            [CanonicalMessage(role="user", content="finalize")],
            None,
            response_schema={"type": "object"},
        )
