"""Environment validation for optional reasoning-effort configuration."""

import pytest
from pydantic import ValidationError

from app.config import ENV_VAR_NAMES, load_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VAR_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_reasoning_effort_defaults_to_unset():
    assert load_settings(env_file=None).llm_reasoning_effort is None


def test_reasoning_effort_env_is_loaded(monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "medium")
    assert load_settings(env_file=None).llm_reasoning_effort == "medium"


def test_invalid_reasoning_effort_fails_validation(monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "maximum")
    with pytest.raises(ValidationError):
        load_settings(env_file=None)
