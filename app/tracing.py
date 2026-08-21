"""Lightweight observability primitives for the agent runtime.

These are plain data records rather than a tracing framework. The runtime
appends them during a turn and builds a compact developer-facing summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ModelCallRecord:
    """One LLM call."""

    step: int
    provider: str
    model: str
    kind: str  # "tool_call" | "final" | "repair"
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    attempt: int = 1
    retries: int = 0


@dataclass
class ToolCallRecord:
    """One executed (or cache-served) tool call."""

    step: int
    tool_name: str
    arguments: dict[str, Any]
    duration_ms: float
    cache_hit: bool = False
    result_summary: str | None = None


@dataclass
class BlockedToolEvent:
    """Developer-trace record for a model-requested call blocked by runtime safety."""

    step: int
    tool_name: str
    arguments: dict[str, Any]
    #: Stable reason code, e.g. "MISSING_ELIGIBLE_POLICY_PRECONDITION".
    reason: str


@dataclass
class RunSummary:
    """Aggregated run metrics for the CLI/verbose trace."""

    llm_calls: int = 0
    tool_calls: int = 0
    total_tokens: int = 0
    total_duration_ms: float = 0.0
    estimated_cost_usd: float | None = None


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    input_cost_per_million: float | None,
    output_cost_per_million: float | None,
) -> float | None:
    """Estimate cost from provider-reported usage and configured pricing.

    Returns None unless both prices are configured; mutable pricing must never
    be hardcoded in agent logic.
    """
    if input_cost_per_million is None or output_cost_per_million is None:
        return None
    return (
        input_tokens * input_cost_per_million + output_tokens * output_cost_per_million
    ) / 1_000_000
