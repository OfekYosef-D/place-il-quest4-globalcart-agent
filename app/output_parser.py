"""Structured final-output parsing (spec section 16).

Extracts the model's final JSON into the approved `AgentResult` contract.
Optional internal audit keys `sentiment`/`urgency` are captured into
`ModelAssessment` before validation; they are runtime metadata only and never
become part of the external output contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.schemas import AgentResult

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class OutputParseError(RuntimeError):
    """The model's final content could not be parsed into AgentResult."""


@dataclass
class ModelAssessment:
    """Internal-only sentiment/urgency assessment reported by the model.

    Stored in runtime/audit state; never serialized into AgentResult and
    never allowed to influence business outcomes.
    """

    sentiment: str | None = None
    urgency: str | None = None


def parse_final_output(content: str | None) -> tuple[AgentResult, ModelAssessment]:
    """Parse final model content into (AgentResult, ModelAssessment).

    Tolerates a bare JSON object, a fenced JSON block, or a JSON object
    embedded in surrounding prose. Raises OutputParseError with the collected
    problems when parsing or validation fails.
    """
    if content is None or not content.strip():
        raise OutputParseError("Final output is empty.")

    payload = _extract_json_object(content)
    if payload is None:
        raise OutputParseError("Final output does not contain a JSON object.")

    assessment = ModelAssessment(
        sentiment=_optional_str(payload.pop("sentiment", None)),
        urgency=_optional_str(payload.pop("urgency", None)),
    )

    try:
        return AgentResult.model_validate(payload), assessment
    except Exception as exc:  # pydantic ValidationError and friends
        raise OutputParseError(f"Final output failed schema validation: {exc}") from exc


def _extract_json_object(content: str) -> dict | None:
    """Return the first plausible JSON object dict found in the content."""
    candidates: list[str] = [content.strip()]
    candidates.extend(_FENCED_JSON_RE.findall(content))
    balanced = _first_balanced_object(content)
    if balanced is not None:
        candidates.append(balanced)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _first_balanced_object(content: str) -> str | None:
    """Return the first balanced {...} substring, honoring strings."""
    start = content.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(content)):
            char = content[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return content[start : index + 1]
        start = content.find("{", start + 1)
    return None


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
