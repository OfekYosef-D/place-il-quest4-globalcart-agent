"""Execution layer for model-requested tool calls (spec sections 4 and 15).

Guards, caches, executes through the supplied registry, and applies trusted
results to runtime state. Design notes:

- `state.tool_history` receives one authoritative `ToolInteraction` per
  model-requested interaction, including cache hits and blocked attempts.
- Cached results and freshly executed results share one small
  result-application path (`_apply_trusted_result`) so a cache hit can never
  skip trusted facts the model received.
- Business errors are data. Genuine programmer/system exceptions are raised
  as `ToolSystemFailure` for fail-safe handling; they are never handed to the
  model as ordinary observations to work around.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.guardrail import blocked_tool_observation, check_refund_precondition
from app.llm.base import ToolCallRequest
from app.state import AgentState, CaseState, ToolInteraction, ToolInteractionOutcome
from app.tool_cache import ToolCache
from app.tools_adapter import ToolKit

#: Tools whose arguments carry an order_id and whose trusted terminal error
#: stops the case (deterministic stop guardrail, not a fixed workflow).
ORDER_SCOPED_TOOLS = frozenset({"get_order_details", "check_return_policy", "process_refund"})

#: Business error codes that terminally resolve an order case.
TERMINAL_ERROR_CODES = frozenset({"ORDER_NOT_FOUND"})

#: Reason code for interactions blocked by the terminal-case stop guardrail.
TERMINAL_CASE_REASON = "CASE_TERMINAL_ERROR"


class ToolSystemFailure(RuntimeError):
    """A genuine programmer/system exception from a local deterministic tool.

    Not a recoverable business error: the runtime must trace it and fail
    safely (preserving already-resolved trusted outcomes) instead of letting
    the model retry or work around it.
    """

    def __init__(self, message: str, interaction: ToolInteraction) -> None:
        super().__init__(message)
        self.interaction = interaction


class ToolExecutor:
    """Guarded, cached dispatcher over the supplied tool registry."""

    def __init__(self, kit: ToolKit, cache: ToolCache) -> None:
        self._kit = kit
        self._cache = cache
        self._schemas = {schema["name"]: schema for schema in kit.schemas}

    def execute(
        self, step: int, call: ToolCallRequest, state: AgentState
    ) -> tuple[str, ToolInteraction]:
        """Handle one model-requested call; returns (observation, interaction).

        The interaction is always appended to `state.tool_history` here.
        """
        name = call.name
        arguments = dict(call.arguments or {})

        if name not in self._kit.tool_names:
            interaction = self._record(
                state, step, name, arguments, ToolInteractionOutcome.UNKNOWN_TOOL
            )
            return self._error_observation(
                "UNKNOWN_TOOL",
                f"Tool {name!r} does not exist. Allowed tools: {self._kit.tool_names}.",
            ), interaction

        if call.raw_arguments is not None:
            interaction = self._record(
                state, step, name, arguments, ToolInteractionOutcome.INVALID_ARGUMENTS
            )
            return self._error_observation(
                "INVALID_ARGUMENTS",
                "Your tool-call arguments could not be parsed as a JSON object. "
                "Send the call again with valid structured arguments.",
            ), interaction

        terminal = self._terminal_case_block(step, name, arguments, state)
        if terminal is not None:
            return terminal

        structural = self._structural_argument_errors(name, arguments)
        if structural:
            interaction = self._record(
                state, step, name, arguments, ToolInteractionOutcome.INVALID_ARGUMENTS
            )
            return self._error_observation(
                "INVALID_ARGUMENTS",
                "Tool arguments do not match the supplied schema.",
                **structural,
            ), interaction

        if name == "process_refund":
            event = check_refund_precondition(state, arguments)
            if event is not None:
                event.step = step
                interaction = self._record(
                    state,
                    step,
                    name,
                    arguments,
                    ToolInteractionOutcome.BLOCKED,
                    reason_code=event.reason,
                )
                return blocked_tool_observation(event), interaction

        cache_start = time.perf_counter()
        cached = self._cache.get(name, arguments)
        if cached is not None:
            interaction = self._record(
                state, step, name, arguments, ToolInteractionOutcome.CACHED, result=cached
            )
            interaction.duration_ms = (time.perf_counter() - cache_start) * 1000.0
            # Same deterministic state application as a fresh execution.
            self._apply_trusted_result(state, name, arguments, cached)
            return json.dumps(cached, ensure_ascii=False), interaction

        exec_start = time.perf_counter()
        try:
            result = self._kit.call(name, **arguments)
        except Exception as exc:  # programmer/system failure: fail safe, never retry
            interaction = self._record(
                state, step, name, arguments, ToolInteractionOutcome.SYSTEM_FAILURE
            )
            interaction.duration_ms = (time.perf_counter() - exec_start) * 1000.0
            raise ToolSystemFailure(
                f"System failure while executing tool {name!r}: {exc}", interaction
            ) from exc

        if isinstance(result, dict) and "error" in result:
            outcome = ToolInteractionOutcome.BUSINESS_ERROR
        else:
            outcome = ToolInteractionOutcome.EXECUTED
        interaction = self._record(
            state,
            step,
            name,
            arguments,
            outcome,
            result=result,
            reason_code=result.get("error") if outcome is ToolInteractionOutcome.BUSINESS_ERROR else None,
        )
        interaction.duration_ms = (time.perf_counter() - exec_start) * 1000.0
        self._cache.put(name, arguments, result)
        self._apply_trusted_result(state, name, arguments, result)
        return json.dumps(result, ensure_ascii=False), interaction

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _terminal_case_block(
        self, step: int, name: str, arguments: dict[str, Any], state: AgentState
    ) -> tuple[str, ToolInteraction] | None:
        """Stop further order-scoped work once a trusted terminal error exists."""
        if name not in ORDER_SCOPED_TOOLS:
            return None
        order_id = arguments.get("order_id")
        if not isinstance(order_id, str):
            return None
        case = state.cases.get(order_id)
        if case is None or not case.terminal_error:
            return None
        interaction = self._record(
            state,
            step,
            name,
            arguments,
            ToolInteractionOutcome.BLOCKED,
            reason_code=TERMINAL_CASE_REASON,
        )
        observation = json.dumps(
            {
                "blocked": True,
                "error": "CASE_TERMINAL",
                "order_id": order_id,
                "terminal_error": case.terminal_error,
                "message": (
                    f"Order {order_id} already reached trusted terminal error "
                    f"{case.terminal_error}. No further tool calls will be executed "
                    "for this order. Conclude the case honestly from that result."
                ),
            },
            ensure_ascii=False,
        )
        return observation, interaction

    def _structural_argument_errors(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Minimal structural check against the supplied input_schema.

        Required keys present, no unexpected keys. Business validation stays
        inside the supplied tools.
        """
        schema = self._schemas.get(name)
        if schema is None:
            return None
        input_schema = schema.get("input_schema", {})
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])
        missing = [key for key in required if key not in arguments]
        unexpected = [key for key in arguments if key not in properties]
        if not missing and not unexpected:
            return None
        details: dict[str, Any] = {}
        if missing:
            details["missing_required"] = missing
        if unexpected:
            details["unexpected_arguments"] = unexpected
        return details

    def _record(
        self,
        state: AgentState,
        step: int,
        tool_name: str,
        arguments: dict[str, Any],
        outcome: ToolInteractionOutcome,
        *,
        result: dict[str, Any] | None = None,
        reason_code: str | None = None,
    ) -> ToolInteraction:
        interaction = ToolInteraction(
            step=step,
            tool_name=tool_name,
            arguments=dict(arguments),
            outcome=outcome,
            result=result,
            reason_code=reason_code,
        )
        state.tool_history.append(interaction)
        return interaction

    @staticmethod
    def _apply_trusted_result(
        state: AgentState, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """One shared path that stores trusted facts for executed and cached results."""
        error = result.get("error") if isinstance(result, dict) else None
        order_id = arguments.get("order_id")

        def order_case() -> CaseState:
            return state.cases.setdefault(order_id, CaseState(order_id=order_id))

        if tool_name == "get_order_details":
            if error is None:
                order_case().verified_order = result
            elif error in TERMINAL_ERROR_CODES and isinstance(order_id, str):
                order_case().terminal_error = error
        elif tool_name == "get_user_profile":
            if error is None:
                user_id = result.get("user_id")
                for case in state.cases.values():
                    if case.verified_order and case.verified_order.get("user_id") == user_id:
                        case.verified_user = result
        elif tool_name == "check_return_policy":
            if error is None:
                case = order_case()
                case.policy_result = result
                case.reason = result.get("reason") or arguments.get("reason")
            elif error in TERMINAL_ERROR_CODES and isinstance(order_id, str):
                order_case().terminal_error = error
        elif tool_name == "process_refund":
            if error is None:
                order_case().refund_result = result
            elif error in TERMINAL_ERROR_CODES and isinstance(order_id, str):
                order_case().terminal_error = error

    @staticmethod
    def _error_observation(code: str, message: str, **extra: Any) -> str:
        payload: dict[str, Any] = {"error": code, "message": message}
        payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)
