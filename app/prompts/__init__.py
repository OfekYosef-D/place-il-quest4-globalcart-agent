"""Prompt assets for the operations resolver.

The system prompt is a plain Markdown file so it stays readable and editable
without code changes; the loader returns it verbatim.
"""

from __future__ import annotations

from pathlib import Path

_PROMPT_FILE = Path(__file__).parent / "system_prompt.md"


def load_system_prompt() -> str:
    """Return the concise English system prompt text."""
    return _PROMPT_FILE.read_text(encoding="utf-8")
