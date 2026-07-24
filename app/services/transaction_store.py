"""
app/services/transaction_store.py

Shared in-memory transaction store.

Centralises all transaction state so that:
 - The orchestration route (POST /api/v1/transactions) can write new records.
 - The webhook/poller route can look up and update existing records.
 - Duplicate-execution guards can be enforced by checking current status.

NOTE: This is intentionally in-memory for the simulated banking demo.
      A production system would back this with a database.
"""

from __future__ import annotations

import threading
from uuid import UUID

import structlog

from app.models.transaction import TransactionContext, TransactionStatus

log = structlog.get_logger(__name__)


class TransactionStore:
    """Thread-safe in-memory store for TransactionContext objects."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[UUID, TransactionContext] = {}
        self._progress: dict[UUID, list[dict]] = {}

    # ── Writes ────────────────────────────────────────────────────────────────

    def upsert(self, ctx: TransactionContext) -> None:
        with self._lock:
            self._data[ctx.transaction_id] = ctx

    def set_status(self, transaction_id: UUID, new_status: TransactionStatus) -> TransactionContext | None:
        """
        Update the status of an existing transaction.
        Returns the updated context, or None if the ID is unknown.
        """
        with self._lock:
            ctx = self._data.get(transaction_id)
            if ctx is None:
                return None
            updated = ctx.model_copy(update={"status": new_status})
            self._data[transaction_id] = updated
            log.info("transaction_store.status_changed", transaction_id=str(transaction_id), new_status=new_status)
            return updated

    def update_decision(self, transaction_id: UUID, decision_type: str, decision_maker: str | None = None, decision_reason: str | None = None) -> TransactionContext | None:
        """
        Update the decision_type, decision_maker, and decision_reason fields of a transaction.
        """
        with self._lock:
            ctx = self._data.get(transaction_id)
            if ctx is None:
                return None
            updated = ctx.model_copy(update={
                "decision_type": decision_type,
                "decision_maker": decision_maker,
                "decision_reason": decision_reason
            })
            self._data[transaction_id] = updated
            log.info("transaction_store.decision_updated", transaction_id=str(transaction_id), decision_type=decision_type)
            return updated

    def add_progress(self, transaction_id: UUID, step_num: int, name: str, detail: str) -> None:
        """Record a step in the transaction's execution progress."""
        with self._lock:
            if transaction_id not in self._progress:
                self._progress[transaction_id] = []
            
            # Prevent duplicate step numbers by filtering old ones out
            self._progress[transaction_id] = [
                s for s in self._progress[transaction_id] if s["step_num"] != step_num
            ]
            self._progress[transaction_id].append({
                "step_num": step_num,
                "name": name,
                "detail": detail
            })
            
            # Sort just in case they arrive out of order
            self._progress[transaction_id].sort(key=lambda x: x["step_num"])

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get(self, transaction_id: UUID) -> TransactionContext | None:
        with self._lock:
            return self._data.get(transaction_id)
            
    def get_progress(self, transaction_id: UUID) -> list[dict]:
        with self._lock:
            # Return a copy to avoid mutation
            return list(self._progress.get(transaction_id, []))

    def all(self) -> list[TransactionContext]:
        with self._lock:
            return list(self._data.values())

    def awaiting_human_review(self) -> list[TransactionContext]:
        """Return all transactions currently paused for human approval."""
        with self._lock:
            return [
                ctx for ctx in self._data.values()
                if ctx.status == TransactionStatus.ESCALATED
            ]

    # ── Guards ────────────────────────────────────────────────────────────────

    def is_resolved(self, transaction_id: UUID) -> bool:
        """
        True when a transaction has already been fully resolved and
        further processing would be a duplicate execution.
        """
        ctx = self.get(transaction_id)
        if ctx is None:
            return False
        return ctx.status in {
            TransactionStatus.APPROVED,
            TransactionStatus.REJECTED,
            TransactionStatus.CANCELLED,
        }


# Module-level singleton shared across all FastAPI routes.
transaction_store = TransactionStore()
