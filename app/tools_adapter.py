"""Thin access layer over the supplied starter-kit tools.

The upstream `mock_services.py` is read-only: this adapter imports it from a
configurable path and exposes its `TOOL_SCHEMAS` / `TOOL_REGISTRY` without
modifying or rebuilding them (docs/UPSTREAM.md, MILESTONES M1). No business
rules and no precondition logic live here.

Provider-agnostic by design: schemas are exposed exactly as supplied
(Anthropic-shaped, with `input_schema`). Conversion to a provider wire
format belongs to the provider layer (see `app.llm.groq_provider`).
"""

from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


class ToolKitError(RuntimeError):
    """Configuration or dispatch error in the tool access layer."""


class ToolKit:
    """Read-only handle on the supplied tool layer."""

    def __init__(self, module: ModuleType) -> None:
        self._module = module

    @property
    def schemas(self) -> list[dict[str, Any]]:
        """Deep copy of the supplied TOOL_SCHEMAS; callers cannot mutate the originals."""
        return copy.deepcopy(self._module.TOOL_SCHEMAS)

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._module.TOOL_REGISTRY)

    def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        """Dispatch through the supplied TOOL_REGISTRY.

        Business errors come back as data (`{"error": ...}`); only unknown
        tool names are dispatch errors.
        """
        registry = self._module.TOOL_REGISTRY
        if name not in registry:
            raise ToolKitError(
                f"Unknown tool {name!r}. Allowed tools: {sorted(registry)}."
            )
        return registry[name](**arguments)


def load_toolkit(starter_kit_path: str | Path) -> ToolKit:
    """Import the supplied `mock_services` module from the configured path."""
    path = Path(starter_kit_path)
    if not (path / "mock_services.py").exists():
        raise ToolKitError(
            f"Starter kit not found at '{path}'. Run scripts/bootstrap_upstream.sh "
            "or set QUEST4_STARTER_KIT_PATH to an authorized local copy."
        )
    resolved = str(path.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    return ToolKit(importlib.import_module("mock_services"))
