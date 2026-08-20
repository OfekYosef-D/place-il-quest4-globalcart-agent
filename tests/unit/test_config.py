"""Tests for environment/config loading."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import ENV_VAR_NAMES, Settings, load_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VAR_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults_apply():
    settings = load_settings(env_file=None)
    assert settings.llm_provider == "groq"
    assert settings.llm_model is None
    assert settings.groq_api_key is None
    assert settings.openrouter_api_key is None
    assert settings.llm_timeout_seconds == 30.0
    assert settings.llm_max_retries == 2
    assert settings.llm_temperature == 0.0
    assert settings.agent_max_steps == 12
    assert settings.llm_retry_backoff_seconds == 0.25
    assert settings.llm_input_cost_per_million is None
    assert settings.llm_output_cost_per_million is None


def test_default_starter_kit_path_points_at_vendor_checkout():
    settings = load_settings(env_file=None)
    assert isinstance(settings.quest4_starter_kit_path, Path)
    assert settings.quest4_starter_kit_path == Path(
        ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit"
    )


def test_env_vars_override_defaults(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("AGENT_MAX_STEPS", "12")
    monkeypatch.setenv("QUEST4_STARTER_KIT_PATH", "some/other/path")
    monkeypatch.setenv("LLM_INPUT_COST_PER_MILLION", "0.05")

    settings = load_settings(env_file=None)
    assert settings.llm_provider == "openrouter"
    assert settings.llm_model == "some-model"
    assert settings.openrouter_api_key == "secret"
    assert settings.llm_timeout_seconds == 10.0
    assert settings.llm_max_retries == 3
    assert settings.agent_max_steps == 12
    assert settings.quest4_starter_kit_path == Path("some/other/path")
    assert settings.llm_input_cost_per_million == 0.05


def test_invalid_int_fails_validation(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "not-a-number")
    with pytest.raises(ValidationError):
        load_settings(env_file=None)


@pytest.mark.parametrize(
    ("env_name", "bad_value"),
    [
        ("LLM_TIMEOUT_SECONDS", "0"),
        ("LLM_TIMEOUT_SECONDS", "-5"),
        ("LLM_MAX_RETRIES", "-1"),
        ("LLM_TEMPERATURE", "-0.1"),
        ("AGENT_MAX_STEPS", "0"),
        ("AGENT_MAX_STEPS", "-3"),
        ("LLM_RETRY_BACKOFF_SECONDS", "-0.5"),
        ("LLM_INPUT_COST_PER_MILLION", "-0.01"),
        ("LLM_OUTPUT_COST_PER_MILLION", "-1"),
    ],
)
def test_out_of_range_values_fail_validation(monkeypatch, env_name, bad_value):
    monkeypatch.setenv(env_name, bad_value)
    with pytest.raises(ValidationError):
        load_settings(env_file=None)


def test_boundary_values_are_allowed(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")
    monkeypatch.setenv("LLM_TEMPERATURE", "0")
    monkeypatch.setenv("AGENT_MAX_STEPS", "1")
    monkeypatch.setenv("LLM_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("LLM_INPUT_COST_PER_MILLION", "0")
    monkeypatch.setenv("LLM_OUTPUT_COST_PER_MILLION", "0")

    settings = load_settings(env_file=None)
    assert settings.llm_max_retries == 0
    assert settings.llm_temperature == 0.0
    assert settings.agent_max_steps == 1
    assert settings.llm_retry_backoff_seconds == 0.0
    assert settings.llm_input_cost_per_million == 0.0
    assert settings.llm_output_cost_per_million == 0.0


def test_agent_max_steps_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_STEPS", "20")
    assert load_settings(env_file=None).agent_max_steps == 20


def test_settings_direct_construction_keeps_defaults():
    settings = Settings()
    assert settings.llm_provider == "groq"
    assert settings.llm_temperature == 0.0
