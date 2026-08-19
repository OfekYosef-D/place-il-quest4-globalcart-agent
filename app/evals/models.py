"""External candidate-model configuration for Milestone 3 comparisons."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.config import ReasoningEffort


class CandidateModel(BaseModel):
    """One runtime candidate; pricing remains external to agent logic."""

    model_config = ConfigDict(extra="forbid")

    id: str
    provider: str = "groq"
    model: str
    reasoning_effort: ReasoningEffort | None = None
    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)


class CandidateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked_at: str | None = None
    pricing_source: str | None = None
    candidates: list[CandidateModel] = Field(min_length=1)


def load_candidate_config(path: str | Path) -> CandidateConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return CandidateConfig.model_validate(raw)
