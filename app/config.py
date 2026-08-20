"""Runtime configuration loaded from environment variables / .env.

Every tunable from `.env.example` is mirrored here. Nothing mutable such as
model names or prices is hardcoded (spec section 18).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

DEFAULT_STARTER_KIT_PATH = ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit"
ReasoningEffort = Literal["none", "default", "low", "medium", "high"]

#: Environment variable names consumed by this project (also used by tests).
ENV_VAR_NAMES = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "LLM_TIMEOUT_SECONDS",
    "LLM_MAX_RETRIES",
    "LLM_RETRY_BACKOFF_SECONDS",
    "LLM_TEMPERATURE",
    "LLM_REASONING_EFFORT",
    "AGENT_MAX_STEPS",
    "QUEST4_STARTER_KIT_PATH",
    "LLM_INPUT_COST_PER_MILLION",
    "LLM_OUTPUT_COST_PER_MILLION",
)


class Settings(BaseModel):
    """Validated runtime settings with safe defaults."""

    llm_provider: str = "groq"
    llm_model: str | None = None
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None

    # LLM call discipline: initial attempt + llm_max_retries retries, applied
    # by the runtime only to known transient failures.
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)
    llm_retry_backoff_seconds: float = Field(default=0.25, ge=0)
    llm_temperature: float = Field(default=0.0, ge=0)
    # Optional provider reasoning control. Leave unset unless the selected
    # provider/model combination explicitly supports the configured values.
    llm_reasoning_effort: ReasoningEffort | None = None

    # Safety ceiling for the agent loop. Provisional default; calibrate from
    # passing live traces rather than from a guessed workflow length.
    agent_max_steps: int | None = Field(default=12, gt=0)

    quest4_starter_kit_path: Path = Path(DEFAULT_STARTER_KIT_PATH)

    # Optional pricing for estimated-cost reporting; keep mutable pricing out
    # of agent logic.
    llm_input_cost_per_million: float | None = Field(default=None, ge=0)
    llm_output_cost_per_million: float | None = Field(default=None, ge=0)


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Build Settings from environment variables, optionally loading `.env`."""
    if env_file is not None:
        load_dotenv(env_file, override=False)

    raw: dict[str, str] = {}
    for env_name in ENV_VAR_NAMES:
        value = os.environ.get(env_name)
        if value is not None and value != "":
            raw[env_name.lower()] = value

    return Settings(**raw)
