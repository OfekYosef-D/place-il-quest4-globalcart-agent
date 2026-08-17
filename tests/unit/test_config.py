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
    assert settings.llm_timeout_seconds == 30.0
    assert settings.llm_max_retries == 2
    assert settings.llm_temperature == 0.0
    assert settings.agent_max_steps is None
    assert settings.llm_input_cost_per_million is None
    assert settings.llm_output_cost_per_million is None


def test_default_starter_kit_path_points_at_vendor_checkout():
    settings = load_settings(env_file=None)
    assert isinstance(settings.quest4_starter_kit_path, Path)
    assert settings.quest4_starter_kit_path == Path(
        ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit"
    )


def test_env_vars_override_defaults(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("AGENT_MAX_STEPS", "12")
    monkeypatch.setenv("QUEST4_STARTER_KIT_PATH", "some/other/path")
    monkeypatch.setenv("LLM_INPUT_COST_PER_MILLION", "0.05")

    settings = load_settings(env_file=None)
    assert settings.llm_model == "some-model"
    assert settings.llm_timeout_seconds == 10.0
    assert settings.llm_max_retries == 3
    assert settings.agent_max_steps == 12
    assert settings.quest4_starter_kit_path == Path("some/other/path")
    assert settings.llm_input_cost_per_million == 0.05


def test_invalid_int_fails_validation(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "not-a-number")
    with pytest.raises(ValidationError):
        load_settings(env_file=None)


def test_settings_direct_construction_keeps_defaults():
    settings = Settings()
    assert settings.llm_provider == "groq"
    assert settings.llm_temperature == 0.0
