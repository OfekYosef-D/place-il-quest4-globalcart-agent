"""Shared Stage 2 crew construction for CLI and web entry points."""

from __future__ import annotations

from app.config import load_settings
from app.crew.config import load_crew_settings
from app.crew.orchestrator import GlobalCartCrew
from app.crew.tools import load_crew_toolkits
from app.llm.factory import (
    build_provider,
    has_provider_api_key,
    required_api_key_name,
    supported_provider_names,
)


def build_crew() -> GlobalCartCrew:
    """Build a configured Stage 2 crew or raise a clear startup error."""
    settings = load_settings()
    provider_name = settings.llm_provider.strip().lower()
    if provider_name not in supported_provider_names():
        raise RuntimeError(f"Unsupported LLM_PROVIDER {settings.llm_provider!r}.")
    if not has_provider_api_key(settings):
        raise RuntimeError(f"Missing {required_api_key_name(provider_name)}.")
    if not settings.llm_model:
        raise RuntimeError("Missing LLM_MODEL.")

    crew_settings = load_crew_settings()
    provider = build_provider(settings)
    toolkits = load_crew_toolkits(crew_settings.starter_kit_path)
    return GlobalCartCrew(settings, crew_settings, provider, toolkits)
