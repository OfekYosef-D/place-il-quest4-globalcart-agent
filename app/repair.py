"""One targeted, ephemeral output-repair pass (spec section 17).

If the final model output is malformed, the runtime allows at most one
correction pass. The correction exchange lives entirely in an ephemeral copy
of the conversation: it is never appended to `state.messages` or any
conversation memory, it calls the provider with `tools=None`, and only an
accepted corrected `AgentResult` enters the run outcome.

Per the approved execution clarifications, the internal `ModelAssessment`
(sentiment/urgency) is captured from a repaired output exactly as it is for
a directly valid final output.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from app.llm.base import LLMProvider, ModelResponse
from app.llm.retry import generate_with_retry
from app.messages import CanonicalMessage, Message
from app.output_parser import ModelAssessment, OutputParseError, parse_final_output
from app.schemas import AgentResult


class RepairFailed(RuntimeError):
    """The single repair pass did not produce a valid final output."""


@dataclass
class RepairOutcome:
    """Accepted repair result plus the real model-call metadata.

    The repair call is a real LLM call: its provider/model/latency/token/
    retry metadata must reach the run trace and summary exactly like any
    other model call.
    """

    result: AgentResult
    assessment: ModelAssessment
    response: ModelResponse
    attempts: int


def repair_final_output(
    provider: LLMProvider,
    run_messages: list[Message],
    failed_content: str | None,
    errors: list[str],
    *,
    max_retries: int,
    backoff_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> RepairOutcome:
    """Run exactly one no-new-tools correction pass.

    Builds an ephemeral copy of `run_messages` plus one correction
    instruction; the caller's list is never mutated. Transient provider
    failures are retried per policy inside this single attempt; anything
    still invalid raises `RepairFailed`.
    """
    repair_messages: list[Message] = list(run_messages)
    repair_messages.append(
        CanonicalMessage(role="user", content=_correction_instruction(failed_content, errors))
    )

    try:
        response, attempts = generate_with_retry(
            provider,
            repair_messages,
            None,  # repair never allows tool calls
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
        )
    except Exception as exc:  # exhausted transients and non-transient both fail closed
        raise RepairFailed(f"Repair call failed: {exc}") from exc

    if response.tool_calls:
        raise RepairFailed("Repair response requested tool calls; the repair pass allows none.")

    try:
        result, assessment = parse_final_output(response.content)
    except OutputParseError as exc:
        raise RepairFailed(f"Repaired output is still invalid: {exc}") from exc
    return RepairOutcome(result=result, assessment=assessment, response=response, attempts=attempts)


def _correction_instruction(failed_content: str | None, errors: list[str]) -> str:
    lines = [
        "Your previous final output was invalid and was not delivered to the customer.",
        "Problems found:",
    ]
    lines.extend(f"- {error}" for error in errors)
    if failed_content:
        lines.append(f"Your previous output was: {failed_content}")
    lines.append(
        "Return ONLY the corrected final JSON object with exactly the required fields "
        "(status, reasoning_chain, action_taken, customer_response), plus the optional "
        "internal sentiment/urgency audit keys if applicable. Do not call any tools. "
        "Base the correction strictly on the trusted tool results already shown; never "
        "invent facts or contradict trusted evidence."
    )
    return "\n".join(lines)
