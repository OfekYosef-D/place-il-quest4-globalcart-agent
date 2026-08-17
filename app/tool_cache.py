"""Deterministic tool-call cache (spec section 15).

Identical read calls are served from the cache instead of re-executing the
tool. The key is (tool_name, JSON-canonicalized arguments); hit/miss counters
feed the developer trace. No expiry or larger subsystem.
"""

from __future__ import annotations

import json
from typing import Any

_MISSING = object()


def normalize_args(arguments: dict[str, Any]) -> str:
    """Canonical JSON form of tool arguments, independent of insertion order.

    Note: JSON canonicalization distinguishes `1` from `1.0`; callers pass
    amounts consistently (floats, per the supplied schemas).
    """
    return json.dumps(arguments, sort_keys=True, ensure_ascii=False)


class ToolCache:
    """Cache of deterministic tool results keyed by tool name + normalized args."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], Any] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(tool_name: str, arguments: dict[str, Any]) -> tuple[str, str]:
        return (tool_name, normalize_args(arguments))

    def get(self, tool_name: str, arguments: dict[str, Any]) -> Any | None:
        """Return the cached result, or None on a miss."""
        value = self._store.get(self._key(tool_name, arguments), _MISSING)
        if value is _MISSING:
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put(self, tool_name: str, arguments: dict[str, Any], result: Any) -> None:
        self._store[self._key(tool_name, arguments)] = result
