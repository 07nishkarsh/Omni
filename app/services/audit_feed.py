"""
app/services/audit_feed.py

Appends structured audit events to an in-memory ledger.
In mock mode the events are logged to stdout only.
In live mode they would be written to the Notion Audit Feed database.

All events are immutable once recorded.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)


class AuditEventType(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    RESUMED = "RESUMED"
    POLLER_TICK = "POLLER_TICK"


class AuditEvent(BaseModel):
    """Immutable audit record."""
    transaction_id: UUID
    event_type: AuditEventType
    actor: str          # "notion_webhook" | "notion_poller" | "orchestrator"
    reason: str = ""
    timestamp: datetime = None  # type: ignore[assignment]

    def model_post_init(self, __context: object) -> None:  # noqa: D401
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc))

    model_config = {"frozen": True}


class AuditFeed:
    """Thread-safe, append-only list of AuditEvent records."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[AuditEvent] = []

    def record(
        self,
        transaction_id: UUID,
        event_type: AuditEventType,
        actor: str,
        reason: str = "",
    ) -> AuditEvent:
        event = AuditEvent(
            transaction_id=transaction_id,
            event_type=event_type,
            actor=actor,
            reason=reason,
        )
        with self._lock:
            self._events.append(event)

        log.info(
            "audit_feed.event",
            transaction_id=str(transaction_id),
            event_type=event_type,
            actor=actor,
            reason=reason,
        )
        return event

    def for_transaction(self, transaction_id: UUID) -> list[AuditEvent]:
        with self._lock:
            return [e for e in self._events if e.transaction_id == transaction_id]

    def all(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._events)


# Module-level singleton.
audit_feed = AuditFeed()
