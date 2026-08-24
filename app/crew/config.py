"""Stage 2 configuration layered on top of the Stage 1 provider settings."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_STAGE2_STARTER_KIT_PATH = ".vendor/place-il-quests-stage2/Quest 4/Stage 2/starter-kit"


class CrewSettings(BaseModel):
    starter_kit_path: Path = Path(DEFAULT_STAGE2_STARTER_KIT_PATH)
    specialist_max_steps: int = Field(default=6, ge=1, le=20)


def load_crew_settings() -> CrewSettings:
    raw: dict[str, object] = {}
    path = os.environ.get("QUEST4_STAGE2_STARTER_KIT_PATH")
    max_steps = os.environ.get("CREW_AGENT_MAX_STEPS")
    if path:
        raw["starter_kit_path"] = Path(path)
    if max_steps:
        raw["specialist_max_steps"] = int(max_steps)
    return CrewSettings(**raw)
