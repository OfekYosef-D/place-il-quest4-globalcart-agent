"""Deterministic `process_refund` precondition (docs/GUARDRAILS.md section 3.3).

A safety precondition, not a workflow: the model stays free to choose its
other tools and their order. This module only answers one question - does
trusted state already hold a `check_return_policy` result for the requested
order with `eligible == true`? It never inspects caps, fraud rules, or any
other business policy; supplied tools remain the business authority.
"""

from __future__ import annotations

import json
from typing import Any

from app.state import AgentState
from app.tracing import BlockedToolEvent

#: Fixed reason code recorded in the developer trace for blocked refunds.
GUARDRAIL_REASON = "MISSING_ELIGIBLE_POLICY_PRECONDITION"


def check_refund_precondition(
    state: AgentState, arguments: dict[str, Any]
) -> BlockedToolEvent | None:
    """Return a `BlockedToolEvent` unless the precondition is satisfied.

    Blocking cases: no order id in the arguments, no known case, no trusted
    policy result, a policy result that is a business error, or a policy
    result whose `eligible` is not `True`.
    """
    order_id = arguments.get("order_id")
    if not isinstance(order_id, str) or not order_id:
        return _blocked(0, arguments)

    case = state.cases.get(order_id)
    policy = case.policy_result if case else None
    if policy is None or "error" in policy or policy.get("eligible") is not True:
        return _blocked(state.step_count, arguments)
    return None


def blocked_tool_observation(event: BlockedToolEvent) -> str:
    """Structured JSON observation returned to the model instead of execution.

    Tells the model what is missing and that it may call other tools or
    conclude the case; it never exposes internal risk signals.
    """
    return json.dumps(
        {
            "blocked": True,
            "tool": event.tool_name,
            "reason_code": event.reason,
            "message": (
                "process_refund was not executed. A trusted check_return_policy "
                "result for this order with eligible=true is required first. "
                "Call check_return_policy, or conclude the case from the trusted "
                "evidence you already hold."
            ),
        },
        ensure_ascii=False,
    )


def _blocked(step: int, arguments: dict[str, Any]) -> BlockedToolEvent:
    return BlockedToolEvent(
        step=step,
        tool_name="process_refund",
        arguments=dict(arguments),
        reason=GUARDRAIL_REASON,
    )
