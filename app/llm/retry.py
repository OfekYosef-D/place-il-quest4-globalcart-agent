"""Transient-only LLM retry policy.

Retries only `TransientLLMFailure` with a short bounded backoff. Business
errors are data and never reach this layer; configuration/auth/programmer
failures propagate immediately and fail closed. The runtime owns the policy
parameters; this module just executes it. See docs/IMPLEMENTATION_SPEC.md.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from app.llm.base import LLMProvider, ModelResponse, TransientLLMFailure

#: Per-attempt backoff ceiling in seconds; keeps retries short and bounded.
_MAX_BACKOFF_SECONDS = 2.0


def generate_with_retry(
    provider: LLMProvider,
    messages: list[Any],
    tools: list[dict[str, Any]] | None,
    *,
    response_schema: dict[str, Any] | None = None,
    max_retries: int,
    backoff_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[ModelResponse, int]:
    """Call the provider, retrying only transient failures.

    `response_schema` is passed through unchanged on every retry so a
    structured final-response contract cannot disappear after a transient
    provider failure.
    """
    attempts_allowed = max(0, max_retries) + 1
    last_failure: TransientLLMFailure | None = None
    for attempt in range(1, attempts_allowed + 1):
        try:
            return provider.generate(
                messages, tools, response_schema=response_schema
            ), attempt
        except TransientLLMFailure as exc:
            last_failure = exc
            if attempt < attempts_allowed:
                backoff = min((2 ** (attempt - 1)) * backoff_seconds, _MAX_BACKOFF_SECONDS)
                sleep(backoff)
    assert last_failure is not None
    raise last_failure
