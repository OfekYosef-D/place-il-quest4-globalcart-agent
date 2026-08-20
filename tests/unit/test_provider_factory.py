"""Offline tests for provider selection and schema binding."""

import pytest

from app.config import Settings
from app.llm.factory import (
    build_provider,
    has_provider_api_key,
    required_api_key_name,
    supported_provider_names,
)
from app.llm.schema_bound import SchemaBoundProvider


def test_supported_provider_names_are_explicit():
    assert supported_provider_names() == frozenset({"groq", "openrouter"})


def test_factory_wraps_selected_provider_with_final_schema_binding():
    groq = build_provider(Settings(groq_api_key="g", llm_model="openai/gpt-oss-20b"))
    assert isinstance(groq, SchemaBoundProvider)
    assert groq.supports_response_schema is True
    assert groq.supports_response_schema_with_tools is False

    openrouter = build_provider(
        Settings(
            llm_provider="openrouter",
            openrouter_api_key="o",
            llm_model="qwen/qwen3.5-397b-a17b",
        )
    )
    assert isinstance(openrouter, SchemaBoundProvider)
    assert openrouter.supports_response_schema is True
    assert openrouter.supports_response_schema_with_tools is True


def test_provider_key_helpers_are_provider_specific():
    assert required_api_key_name("groq") == "GROQ_API_KEY"
    assert required_api_key_name("openrouter") == "OPENROUTER_API_KEY"
    assert has_provider_api_key(Settings(groq_api_key="g")) is True
    assert has_provider_api_key(
        Settings(llm_provider="openrouter", openrouter_api_key="o")
    ) is True


def test_unknown_provider_fails_fast():
    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        build_provider(Settings(llm_provider="unknown", llm_model="m"))
