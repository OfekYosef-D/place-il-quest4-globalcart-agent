"""Offline tests for candidate reasoning and structured-output configuration."""

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm.groq_provider import GroqProvider
from app.messages import CanonicalMessage


class _FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content="done", tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], model=kwargs["model"], usage=None)


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def test_reasoning_effort_is_sent_as_top_level_extra_body_field():
    provider = GroqProvider(Settings(groq_api_key="test-key", llm_model="openai/gpt-oss-20b", llm_reasoning_effort="medium"))
    fake = _FakeClient()
    provider._client = fake
    provider.generate([CanonicalMessage(role="user", content="hello")])
    assert fake.chat.completions.kwargs["extra_body"] == {"reasoning_effort": "medium"}


def test_unset_reasoning_effort_does_not_modify_request():
    provider = GroqProvider(Settings(groq_api_key="test-key", llm_model="some-model"))
    fake = _FakeClient()
    provider._client = fake
    provider.generate([CanonicalMessage(role="user", content="hello")])
    assert "extra_body" not in fake.chat.completions.kwargs


def test_response_schema_uses_native_json_schema_mode_on_no_tools_call():
    provider = GroqProvider(Settings(groq_api_key="test-key", llm_model="some-model"))
    fake = _FakeClient()
    provider._client = fake
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
        "additionalProperties": False,
    }

    provider.generate(
        [CanonicalMessage(role="user", content="finalize")],
        response_schema=schema,
    )

    response_format = fake.chat.completions.kwargs["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == schema
    assert "tools" not in fake.chat.completions.kwargs


def test_response_schema_is_not_combined_with_tool_calling():
    provider = GroqProvider(Settings(groq_api_key="test-key", llm_model="some-model"))
    with pytest.raises(ValueError, match="requires tools=None"):
        provider.generate(
            [CanonicalMessage(role="user", content="hello")],
            tools=[
                {
                    "name": "get_order_details",
                    "description": "lookup",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
            response_schema={"type": "object"},
        )
