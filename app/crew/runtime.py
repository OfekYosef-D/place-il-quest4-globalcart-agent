"""Small provider-neutral tool loop used by each Stage 2 specialist.

The model chooses among only the tools assigned to its role. Python owns tool
execution, observations, stop conditions, and the authoritative interaction log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from app.crew.tools import RoleToolKit
from app.llm.base import LLMProvider, TransientLLMFailure, ensure_unique_tool_call_ids
from app.llm.retry import generate_with_retry
from app.messages import AssistantToolCallMessage, CanonicalMessage, Message, ToolObservationMessage


@dataclass
class CrewToolInteraction:
    step: int
    role: str
    tool_name: str
    arguments: dict
    result: dict | None
    outcome: str
    reason: str | None = None


@dataclass
class SpecialistRun:
    role: str
    messages: list[Message] = field(default_factory=list)
    interactions: list[CrewToolInteraction] = field(default_factory=list)
    final_content: str | None = None
    failure_reason: str | None = None
    steps: int = 0


ToolGuard = Callable[[str, dict, SpecialistRun], tuple[bool, str | None]]
StopCondition = Callable[[SpecialistRun], bool]


class SpecialistAgent:
    """A real bounded agent with its own prompt, context and capability set."""

    def __init__(
        self,
        *,
        role: str,
        system_prompt: str,
        provider: LLMProvider,
        toolkit: RoleToolKit,
        max_steps: int,
        llm_max_retries: int,
        retry_backoff_seconds: float,
    ) -> None:
        self.role = role
        self.system_prompt = system_prompt
        self.provider = provider
        self.toolkit = toolkit
        self.max_steps = max_steps
        self.llm_max_retries = llm_max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def run(
        self,
        task: str,
        *,
        guard: ToolGuard | None = None,
        stop_when: StopCondition | None = None,
    ) -> SpecialistRun:
        run = SpecialistRun(
            role=self.role,
            messages=[
                CanonicalMessage(role="system", content=self.system_prompt),
                CanonicalMessage(role="user", content=task),
            ],
        )
        repeated_signature: tuple | None = None
        repeated_count = 0

        while run.steps < self.max_steps:
            run.steps += 1
            try:
                response, _ = generate_with_retry(
                    self.provider,
                    run.messages,
                    self.toolkit.schemas,
                    max_retries=self.llm_max_retries,
                    backoff_seconds=self.retry_backoff_seconds,
                )
            except TransientLLMFailure as exc:
                run.failure_reason = f"LLM_FAILURE: {exc}"
                return run
            except Exception as exc:
                run.failure_reason = f"LLM_FAILURE: {exc}"
                return run

            response = ensure_unique_tool_call_ids(response)
            if not response.tool_calls:
                run.final_content = response.content
                return run

            signature = tuple(
                (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
                for call in response.tool_calls
            )
            if signature == repeated_signature:
                repeated_count += 1
            else:
                repeated_signature = signature
                repeated_count = 1
            if repeated_count >= 3:
                run.failure_reason = "NO_PROGRESS: repeated identical tool requests"
                return run

            run.messages.append(
                AssistantToolCallMessage(content=response.content, tool_calls=list(response.tool_calls))
            )
            for call in response.tool_calls:
                arguments = dict(call.arguments or {})
                if call.raw_arguments is not None:
                    result = {
                        "error": "INVALID_ARGUMENTS",
                        "message": "Tool arguments were not valid JSON object arguments.",
                    }
                    run.interactions.append(
                        CrewToolInteraction(
                            step=run.steps,
                            role=self.role,
                            tool_name=call.name,
                            arguments=arguments,
                            result=result,
                            outcome="BLOCKED",
                            reason="INVALID_ARGUMENTS",
                        )
                    )
                elif call.name not in self.toolkit.tool_names:
                    result = {
                        "error": "UNAUTHORIZED_TOOL",
                        "message": f"{self.role} is not authorized to call {call.name}.",
                    }
                    run.interactions.append(
                        CrewToolInteraction(
                            step=run.steps,
                            role=self.role,
                            tool_name=call.name,
                            arguments=arguments,
                            result=result,
                            outcome="BLOCKED",
                            reason="UNAUTHORIZED_TOOL",
                        )
                    )
                elif guard is not None:
                    allowed, reason = guard(call.name, arguments, run)
                    if not allowed:
                        result = {
                            "blocked": True,
                            "error": "GUARDRAIL_BLOCKED",
                            "reason": reason,
                        }
                        run.interactions.append(
                            CrewToolInteraction(
                                step=run.steps,
                                role=self.role,
                                tool_name=call.name,
                                arguments=arguments,
                                result=result,
                                outcome="BLOCKED",
                                reason=reason,
                            )
                        )
                    else:
                        result = self._execute(call.name, arguments, run)
                else:
                    result = self._execute(call.name, arguments, run)

                run.messages.append(
                    ToolObservationMessage(
                        tool_call_id=call.id,
                        tool_name=call.name,
                        content=json.dumps(result, ensure_ascii=False),
                    )
                )
                if run.failure_reason is not None:
                    return run

            if stop_when is not None and stop_when(run):
                return run

        run.failure_reason = "MAX_STEPS_EXCEEDED"
        return run

    def _execute(self, name: str, arguments: dict, run: SpecialistRun) -> dict:
        try:
            result = self.toolkit.call(name, **arguments)
        except Exception as exc:
            run.failure_reason = f"TOOL_SYSTEM_FAILURE: {name}: {exc}"
            result = {"error": "TOOL_SYSTEM_FAILURE", "message": str(exc)}
            outcome = "SYSTEM_FAILURE"
        else:
            outcome = "BUSINESS_ERROR" if isinstance(result, dict) and "error" in result else "EXECUTED"
        run.interactions.append(
            CrewToolInteraction(
                step=run.steps,
                role=self.role,
                tool_name=name,
                arguments=dict(arguments),
                result=result,
                outcome=outcome,
                reason=result.get("error") if isinstance(result, dict) else None,
            )
        )
        return result
