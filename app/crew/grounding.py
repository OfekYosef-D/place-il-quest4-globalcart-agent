"""Deterministic grounding of customer-supplied identifiers and money.

Semantic interpretation may be probabilistic. Identity and monetary facts are
not. This module extracts only literal facts present in customer text so an LLM
cannot manufacture an order/user id or requested amount and then bootstrap a
real tool flow from that hallucination.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_ORDER_RE = re.compile(r"(?<![A-Z0-9])ORD-\d+(?!\d)", re.IGNORECASE)
_USER_RE = re.compile(r"(?<![A-Z0-9])USR-\d+(?!\d)", re.IGNORECASE)
_NUMBER = r"-?\d[\d,]*(?:\.\d{1,2})?"
_MONEY_PATTERNS = (
    re.compile(rf"\$\s*({_NUMBER})", re.IGNORECASE),
    re.compile(rf"\bUSD\s*\$?\s*({_NUMBER})", re.IGNORECASE),
    re.compile(rf"(?<![\d.])({_NUMBER})\s*(?:USD|dollars?)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class GroundedCustomerFacts:
    order_ids: tuple[str, ...]
    user_ids: tuple[str, ...]
    explicit_amounts: tuple[float, ...]

    @property
    def order_id(self) -> str | None:
        return self.order_ids[0] if len(self.order_ids) == 1 else None

    @property
    def user_id(self) -> str | None:
        return self.user_ids[0] if len(self.user_ids) == 1 else None

    @property
    def explicit_amount(self) -> float | None:
        return self.explicit_amounts[0] if len(self.explicit_amounts) == 1 else None


def ground_customer_text(text: str) -> GroundedCustomerFacts:
    """Extract unique literal identifiers/monetary amounts from customer text."""
    order_ids = _unique(match.group(0).upper() for match in _ORDER_RE.finditer(text))
    user_ids = _unique(match.group(0).upper() for match in _USER_RE.finditer(text))

    amounts: list[float] = []
    for pattern in _MONEY_PATTERNS:
        for match in pattern.finditer(text):
            try:
                value = round(float(match.group(1).replace(",", "")), 2)
            except (TypeError, ValueError):
                continue
            if value not in amounts:
                amounts.append(value)

    return GroundedCustomerFacts(
        order_ids=tuple(order_ids),
        user_ids=tuple(user_ids),
        explicit_amounts=tuple(amounts),
    )


def _unique(values) -> list:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
