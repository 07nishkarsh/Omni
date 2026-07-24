"""
app/services/notion_poller.py

Background polling fallback for the Manager Approval Desk.

Activates when:
  - NOTION_POLLING_ENABLED=true   (explicitly forced), OR
  - NOTION_WEBHOOK_SECRET is blank (no push webhook configured).

Every NOTION_POLLING_INTERVAL_SECONDS it:
  1. Fetches the Manager Approval Desk Notion database (mocked if USE_MOCK_NOTION=true).
  2. Finds rows whose Status changed to "Approved" or "Rejected".
  3. For each, POSTs internally to the webhook handler exactly as a push webhook would.

This means the exact same business logic runs whether events arrive via push or poll,
and the poller is completely transparent to the webhook handler.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog

from app.config import get_settings
from app.models.transaction import TransactionStatus
from app.services.audit_feed import audit_feed, AuditEventType
from app.services.downstream import execute_approved_transaction
from app.services.transaction_store import transaction_store

log = structlog.get_logger(__name__)

_poller_task: asyncio.Task | None = None


# ── Mock Notion fetch (USE_MOCK_NOTION=true) ─────────────────────────────────

def _mock_fetch_approval_desk_changes() -> list[dict]:
    """
    Returns a deterministic list of status-change records.
    In a real integration this would call the Notion API and diff state.

    Returns only transactions that:
      - Exist in our store
      - Are in ESCALATED state (awaiting human review)
      - (In mock mode we return an empty list so the poller is a no-op.)
    """
    return []  # No simulated Notion changes by default. Tests override this.


def _live_fetch_approval_desk_changes(settings) -> list[dict]:
    """
    Real implementation: query Notion for pages in the Manager Approval Desk
    that have Status == "Approved" or "Rejected" and whose TransactionID
    matches a record we're tracking.

    NOTE: Notion does not push webhooks natively. This is the primary
    alternative for detecting changes.
    """
    try:
        import httpx  # already a project dependency
        headers = {
            "Authorization": f"Bearer {settings.notion_token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        body = {
            "filter": {
                "or": [
                    {"property": "Status", "select": {"equals": "Approved"}},
                    {"property": "Status", "select": {"equals": "Rejected"}},
                ]
            }
        }
        db_id = settings.notion_approval_desk_id
        response = httpx.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=headers,
            json=body,
            timeout=10,
        )
        response.raise_for_status()
        pages = response.json().get("results", [])

        changes = []
        for page in pages:
            props = page.get("properties", {})
            # Extract TransactionID (rich_text or title property)
            txn_id_prop = props.get("TransactionID", {})
            rich_text = txn_id_prop.get("rich_text", [])
            if not rich_text:
                continue
            txn_id_str = rich_text[0].get("plain_text", "").strip()
            # Extract Status
            status_prop = props.get("Status", {})
            status_val = (status_prop.get("select") or {}).get("name", "")

            # Extract ManagerNote
            note_prop = props.get("ManagerNote", {})
            note_rich_text = note_prop.get("rich_text", [])
            manager_note = note_rich_text[0].get("plain_text", "").strip() if note_rich_text else None

            if txn_id_str and status_val in ("Approved", "Rejected"):
                changes.append({
                    "transaction_id": txn_id_str,
                    "new_status": status_val,
                    "notion_page_id": page.get("id", ""),
                    "manager_note": manager_note,
                })
        return changes

    except Exception as exc:
        log.error("notion_poller.fetch_error", error=str(exc))
        return []


# ── Main polling loop ─────────────────────────────────────────────────────────

async def _poll_once(settings) -> None:
    """Run one poll cycle: fetch changes and process each."""
    audit_feed.record(
        transaction_id=UUID(int=0),  # sentinel UUID for system events
        event_type=AuditEventType.POLLER_TICK,
        actor="notion_poller",
        reason="Polling Manager Approval Desk",
    )

    if settings.use_mock_notion:
        changes = _mock_fetch_approval_desk_changes()
    else:
        changes = _live_fetch_approval_desk_changes(settings)

    for change in changes:
        try:
            txn_id = UUID(change["transaction_id"])
        except ValueError:
            log.warning("notion_poller.invalid_uuid", raw=change["transaction_id"])
            continue

        new_status = change["new_status"]
        notion_page_id = change.get("notion_page_id", "")
        manager_note = change.get("manager_note")

        # Delegate to the same handler used by the push webhook.
        from app.routes.webhooks import _handle_approved, _handle_rejected, NotionStatusChangePayload  # local import to avoid circular

        ctx = transaction_store.get(txn_id)
        if ctx is None:
            log.warning("notion_poller.unknown_transaction", transaction_id=str(txn_id))
            continue

        if transaction_store.is_resolved(txn_id):
            log.debug("notion_poller.already_resolved", transaction_id=str(txn_id))
            continue

        payload = NotionStatusChangePayload(
            transaction_id=txn_id,
            new_status=new_status,
            notion_page_id=notion_page_id,
            manager_note=manager_note,
            actor="notion_poller"
        )

        log.info("notion_poller.processing", transaction_id=str(txn_id), new_status=new_status)
        if new_status == "Approved":
            await _handle_approved(payload, ctx)
        elif new_status == "Rejected":
            _handle_rejected(payload)


async def _polling_loop(interval: int, settings) -> None:
    log.info("notion_poller.started", interval_seconds=interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await _poll_once(settings)
        except Exception as exc:
            log.error("notion_poller.error", error=str(exc))


# ── Public start/stop API (called from app lifespan) ─────────────────────────

def should_poll(settings=None) -> bool:
    """
    Return True when polling should be active.

    Logic:
      - Polling is ON  if NOTION_POLLING_ENABLED=true explicitly.
      - Polling is ON  if no webhook secret is configured (auto-fallback).
      - Polling is OFF if a push webhook secret IS configured AND
        NOTION_POLLING_ENABLED is not explicitly set to true.
    """
    if settings is None:
        settings = get_settings()
    if settings.notion_polling_enabled:
        return True
    # Auto-enable when push webhooks aren't configured.
    return not bool(settings.notion_webhook_secret)


def start_poller(app_settings=None) -> None:
    """Start the background polling task. Idempotent."""
    global _poller_task
    if _poller_task is not None and not _poller_task.done():
        return  # Already running.

    settings = app_settings or get_settings()
    if not should_poll(settings):
        log.info("notion_poller.disabled", reason="Push webhook secret is configured.")
        return

    interval = settings.notion_polling_interval_seconds
    _poller_task = asyncio.create_task(_polling_loop(interval, settings))
    log.info("notion_poller.task_created", interval_seconds=interval)


def stop_poller() -> None:
    """Cancel the background polling task gracefully."""
    global _poller_task
    if _poller_task and not _poller_task.done():
        _poller_task.cancel()
        log.info("notion_poller.stopped")
    _poller_task = None
