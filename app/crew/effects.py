"""Process-local idempotency ledger for irreversible Stage 2 side effects.

The supplied Quest refund tool is a deterministic mock and does not persist
payment state. The runtime therefore keeps an authority ledger so two customer
turns cannot issue the same order refund twice inside one running service.
A production deployment should back the same contract with a durable payment
idempotency store / provider idempotency key.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class RefundRecord:
    order_id: str
    amount: float
    reason: str
    status: str
    refund_id: str | None = None


class RefundExecutionLedger:
    """Atomically reserve and finalize one refund side effect per order."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._records: dict[str, RefundRecord] = {}

    def reserve(self, order_id: str, amount: float, reason: str) -> tuple[bool, str | None]:
        with self._lock:
            existing = self._records.get(order_id)
            if existing is not None:
                if existing.status == "APPROVED":
                    return False, "REFUND_ALREADY_APPROVED"
                return False, "REFUND_ALREADY_IN_PROGRESS"
            self._records[order_id] = RefundRecord(
                order_id=order_id,
                amount=round(float(amount), 2),
                reason=reason,
                status="RESERVED",
            )
            return True, None

    def finalize(self, order_id: str, result: dict | None) -> None:
        """Keep approved effects permanently; release non-effects/failures."""
        with self._lock:
            reserved = self._records.get(order_id)
            if reserved is None or reserved.status != "RESERVED":
                return
            if isinstance(result, dict) and result.get("status") == "APPROVED":
                self._records[order_id] = RefundRecord(
                    order_id=reserved.order_id,
                    amount=reserved.amount,
                    reason=reserved.reason,
                    status="APPROVED",
                    refund_id=result.get("refund_id"),
                )
            else:
                self._records.pop(order_id, None)

    def get(self, order_id: str) -> RefundRecord | None:
        with self._lock:
            return self._records.get(order_id)
