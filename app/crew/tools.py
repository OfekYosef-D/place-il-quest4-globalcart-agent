"""Read-only adapter over the supplied Stage 2 multi-agent starter kit."""

from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from app.crew.effects import RefundExecutionLedger

Role = Literal["researcher", "decision", "comms"]


class CrewToolKitError(RuntimeError):
    pass


class RoleToolKit:
    """One specialist's capability-scoped view of the shared registry."""

    def __init__(
        self,
        module: ModuleType,
        role: Role,
        schemas: list[dict[str, Any]],
        *,
        refund_ledger: RefundExecutionLedger | None = None,
    ) -> None:
        self._module = module
        self.role = role
        self._schemas = copy.deepcopy(schemas)
        self._tool_names = frozenset(schema["name"] for schema in schemas)
        self._refund_ledger = refund_ledger

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

        if name != "process_refund" or self._refund_ledger is None:
            return registry[name](**arguments)

        # Atomic process-local idempotency around the irreversible side effect.
        # The official starter tool remains untouched; this adapter only decides
        # whether the side effect may be invoked at all.
        order_id = str(arguments.get("order_id") or "")
        reason = str(arguments.get("reason") or "")
        try:
            amount = float(arguments.get("amount"))
        except (TypeError, ValueError):
            # Let the supplied tool own ordinary argument/business validation.
            return registry[name](**arguments)

        reserved, block_reason = self._refund_ledger.reserve(order_id, amount, reason)
        if not reserved:
            existing = self._refund_ledger.get(order_id)
            return {
                "error": "DUPLICATE_REFUND_BLOCKED",
                "message": "A refund for this order is already approved or currently being processed.",
                "reason": block_reason,
                "order_id": order_id,
                "existing_refund_id": existing.refund_id if existing else None,
            }

        try:
            result = registry[name](**arguments)
        except Exception:
            self._refund_ledger.finalize(order_id, None)
            raise
        self._refund_ledger.finalize(order_id, result)
        return result


class CrewToolKits:
    def __init__(self, module: ModuleType) -> None:
        self.module = module
        self.refund_ledger = RefundExecutionLedger()
        self.researcher = RoleToolKit(module, "researcher", module.RESEARCHER_TOOLS)
        self.decision = RoleToolKit(
            module,
            "decision",
            module.DECISION_TOOLS,
            refund_ledger=self.refund_ledger,
        )
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
