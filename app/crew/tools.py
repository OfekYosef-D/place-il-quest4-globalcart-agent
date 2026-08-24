"""Read-only adapter over the supplied Stage 2 multi-agent starter kit."""

from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from app.crew.effects import AlertExecutionLedger, RefundExecutionLedger

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
        alert_ledger: AlertExecutionLedger | None = None,
    ) -> None:
        self._module = module
        self.role = role
        self._schemas = copy.deepcopy(schemas)
        self._tool_names = frozenset(schema["name"] for schema in schemas)
        self._refund_ledger = refund_ledger
        self._alert_ledger = alert_ledger

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

        if name == "send_slack_alert" and self._alert_ledger is not None:
            signature = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
            reserved, replay = self._alert_ledger.reserve(signature)
            if not reserved:
                if replay is not None:
                    replay["idempotent_replay"] = True
                    return replay
                return {
                    "error": "DUPLICATE_ALERT_BLOCKED",
                    "message": "An identical alert is already being delivered.",
                }
            try:
                result = registry[name](**arguments)
            except Exception:
                self._alert_ledger.finalize(signature, None)
                raise
            self._alert_ledger.finalize(signature, result)
            return result

        if name != "process_refund" or self._refund_ledger is None:
            return registry[name](**arguments)

        order_id = str(arguments.get("order_id") or "")
        reason = str(arguments.get("reason") or "")
        try:
            amount = round(float(arguments.get("amount")), 2)
        except (TypeError, ValueError):
            return registry[name](**arguments)

        reserved, block_reason = self._refund_ledger.reserve(order_id, amount, reason)
        if not reserved:
            existing = self._refund_ledger.get(order_id)
            if existing is not None and existing.status == "APPROVED":
                same_request = (
                    abs(existing.amount - amount) <= 0.001
                    and existing.reason == reason
                )
                if same_request:
                    return {
                        "status": "APPROVED",
                        "order_id": order_id,
                        "requested_amount": existing.amount,
                        "approved_amount": existing.amount,
                        "refund_id": existing.refund_id,
                        "reasons": ["idempotent replay of an already approved refund"],
                        "idempotent_replay": True,
                    }
                return {
                    "error": "REFUND_ALREADY_APPROVED_DIFFERENT_REQUEST",
                    "message": (
                        "This order already has an approved refund with different "
                        "refund parameters. No new refund was executed."
                    ),
                    "order_id": order_id,
                    "existing_refund_id": existing.refund_id,
                    "existing_amount": existing.amount,
                    "existing_reason": existing.reason,
                }
            return {
                "error": "DUPLICATE_REFUND_BLOCKED",
                "message": "A refund for this order is currently being processed.",
                "reason": block_reason,
                "order_id": order_id,
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
        self.alert_ledger = AlertExecutionLedger()
        self.researcher = RoleToolKit(module, "researcher", module.RESEARCHER_TOOLS)
        self.decision = RoleToolKit(
            module,
            "decision",
            module.DECISION_TOOLS,
            refund_ledger=self.refund_ledger,
        )
        self.comms = RoleToolKit(
            module,
            "comms",
            module.COMMS_TOOLS,
            alert_ledger=self.alert_ledger,
        )


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
