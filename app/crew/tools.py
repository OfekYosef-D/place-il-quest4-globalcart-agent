"""Read-only adapter over the supplied Stage 2 multi-agent starter kit."""

from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

Role = Literal["researcher", "decision", "comms"]


class CrewToolKitError(RuntimeError):
    pass


class RoleToolKit:
    """One specialist's capability-scoped view of the shared registry."""

    def __init__(self, module: ModuleType, role: Role, schemas: list[dict[str, Any]]) -> None:
        self._module = module
        self.role = role
        self._schemas = copy.deepcopy(schemas)
        self._tool_names = frozenset(schema["name"] for schema in schemas)

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._schemas)

    @property
    def tool_names(self) -> frozenset[str]:
        return self._tool_names

    def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        if name not in self._tool_names:
            raise CrewToolKitError(
                f"Agent role {self.role!r} is not authorized to call {name!r}. "
                f"Allowed: {sorted(self._tool_names)}"
            )
        registry = self._module.TOOL_REGISTRY
        if name not in registry:
            raise CrewToolKitError(f"Starter-kit registry has no tool {name!r}.")
        return registry[name](**arguments)


class CrewToolKits:
    def __init__(self, module: ModuleType) -> None:
        self.module = module
        self.researcher = RoleToolKit(module, "researcher", module.RESEARCHER_TOOLS)
        self.decision = RoleToolKit(module, "decision", module.DECISION_TOOLS)
        self.comms = RoleToolKit(module, "comms", module.COMMS_TOOLS)


def load_crew_toolkits(starter_kit_path: str | Path) -> CrewToolKits:
    path = Path(starter_kit_path)
    if not (path / "multi_agent_tools.py").exists():
        raise CrewToolKitError(
            f"Stage 2 starter kit not found at {path!s}. Run scripts/bootstrap_stage2.sh "
            "or configure QUEST4_STAGE2_STARTER_KIT_PATH."
        )
    resolved = str(path.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    module = importlib.import_module("multi_agent_tools")
    return CrewToolKits(module)
