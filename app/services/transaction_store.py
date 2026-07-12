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

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get(self, transaction_id: UUID) -> TransactionContext | None:
        with self._lock:
            return self._data.get(transaction_id)

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
