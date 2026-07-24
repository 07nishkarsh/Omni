"""
app/routes/webhooks.py

POST /webhooks/notion-status-change
————————————————————————————————————
Receives status-change events from the Manager Approval Desk Notion database.
Notion does not natively push webhooks; callers can either:
  (a) Configure an external automation (Zapier / Make / custom) to POST here, or
  (b) Use the built-in polling fallback (see app/services/notion_poller.py).

Security
--------
When NOTION_WEBHOOK_SECRET is set in .env, every inbound request must include:
    Authorization: Bearer <NOTION_WEBHOOK_SECRET>
Requests with a wrong or missing secret receive HTTP 403.
When the secret is blank (dev/test), verification is skipped.

Business rules
--------------
  Status == "Approved"  →  resume paused orchestrator transaction, then
                            execute downstream side-effects (Gmail/Slack/ledger).
  Status == "Rejected"  →  mark transaction REJECTED, write audit event.
                            NO downstream execution.
  Already-resolved txn  →  return 200 immediately, no action (idempotency guard).
  Unknown transaction   →  return 404.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status as http_status
from pydantic import BaseModel
from uuid import UUID

from app.config import Settings, get_settings
from app.models.transaction import TransactionStatus
from app.services.audit_feed import audit_feed, AuditEventType
from app.services.downstream import execute_approved_transaction
from app.services.transaction_store import transaction_store

log = structlog.get_logger(__name__)
router = APIRouter()


# ── Request / Response schemas ────────────────────────────────────────────────

class NotionStatusChangePayload(BaseModel):
    """
    Body sent by the Notion automation or the polling fallback.

    transaction_id  — the UUID stored in the Notion page's TransactionID property.
    new_status      — the new value of the Notion page's Status property
                      ("Approved" or "Rejected").
    notion_page_id  — optional; the Notion page ID for traceability.
    actor           — who triggered the change ("notion_webhook" | "notion_poller").
    """
    transaction_id: UUID
    new_status: str          # "Approved" | "Rejected"
    notion_page_id: str = ""
    manager_note: str | None = None
    actor: str = "notion_webhook"


class WebhookResponse(BaseModel):
    transaction_id: UUID
    action_taken: str
    message: str


# ── Security dependency ───────────────────────────────────────────────────────

def _verify_webhook_secret(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """
    Validates the Authorization: Bearer <secret> header.
    No-ops when NOTION_WEBHOOK_SECRET is blank (local dev).
    """
    expected = settings.notion_webhook_secret
    if not expected:
        return  # Verification disabled; dev/test mode.

    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Missing or malformed Authorization header.",
        )
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook secret.",
        )


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post(
    "/webhooks/notion-status-change",
    response_model=WebhookResponse,
    summary="Notion Manager Approval Desk status-change webhook",
    description=(
        "Receives status changes from the Manager Approval Desk. "
        "'Approved' resumes the paused transaction and triggers downstream "
        "execution. 'Rejected' closes the transaction with an audit log entry "
        "and zero downstream side-effects."
    ),
    dependencies=[Depends(_verify_webhook_secret)],
)
async def notion_status_change(
    payload: NotionStatusChangePayload,
) -> WebhookResponse:
    txn_id = payload.transaction_id
    actor = payload.actor
    new_status = payload.new_status.strip()

    log.info(
        "webhook.received",
        transaction_id=str(txn_id),
        new_status=new_status,
        actor=actor,
        notion_page_id=payload.notion_page_id,
    )

    # ── 1. Look up the transaction ───────────────────────────────────────────
    ctx = transaction_store.get(txn_id)
    if ctx is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {txn_id} not found in the orchestrator store.",
        )

    # ── 2. Idempotency guard: already resolved → no-op ───────────────────────
    if transaction_store.is_resolved(txn_id):
        log.info("webhook.already_resolved", transaction_id=str(txn_id), current_status=ctx.status)
        return WebhookResponse(
            transaction_id=txn_id,
            action_taken="no_op",
            message=f"Transaction already resolved (status={ctx.status}). No action taken.",
        )

    # ── 3. Route on new_status ────────────────────────────────────────────────
    if new_status == "Approved":
        return await _handle_approved(payload, ctx)

    if new_status == "Rejected":
        return _handle_rejected(payload)

    # Unknown status value — log it but don't crash.
    log.warning("webhook.unknown_status", new_status=new_status, transaction_id=str(txn_id))
    return WebhookResponse(
        transaction_id=txn_id,
        action_taken="ignored",
        message=f"Status '{new_status}' is not handled. Expected 'Approved' or 'Rejected'.",
    )


# ── Path handlers ─────────────────────────────────────────────────────────────

async def _handle_approved(payload: NotionStatusChangePayload, ctx) -> WebhookResponse:
    """Resume paused transaction and execute downstream side-effects."""
    txn_id = payload.transaction_id
    actor = payload.actor
    log.info("webhook.approved", transaction_id=str(txn_id))

    # Update decision properties
    transaction_store.update_decision(
        transaction_id=txn_id,
        decision_type="MANAGER_DECISION",
        decision_maker="Manager",
        decision_reason=payload.manager_note
    )

    # Mark as APPROVED first so any concurrent re-delivery is a no-op.
    updated_ctx = transaction_store.set_status(txn_id, TransactionStatus.APPROVED)

    # Regenerate verdict text and update step 8 progress
    from app.orchestrator.verdict import generate_verdict_text
    verdict_text = generate_verdict_text(updated_ctx)
    transaction_store.add_progress(txn_id, 8, "Verdict issued", verdict_text)

    audit_feed.record(
        transaction_id=txn_id,
        event_type=AuditEventType.APPROVED,
        actor=actor,
        reason=f"Manager approved via Notion. Note: {payload.manager_note}" if payload.manager_note else "Manager approved via Notion.",
    )

    # Execute downstream tools (Gmail/Slack/ledger).
    downstream_results = await execute_approved_transaction(updated_ctx)

    log.info(
        "webhook.approved.complete",
        transaction_id=str(txn_id),
        downstream=downstream_results,
    )
    return WebhookResponse(
        transaction_id=txn_id,
        action_taken="approved_and_executed",
        message="Transaction approved. Downstream execution complete.",
    )


def _handle_rejected(payload: NotionStatusChangePayload) -> WebhookResponse:
    """
    Close the transaction with no downstream execution.
    ONLY writes an audit event — no Gmail, no Slack, no ledger.
    """
    txn_id = payload.transaction_id
    actor = payload.actor
    log.info("webhook.rejected", transaction_id=str(txn_id))

    # Rejecting requires a note
    if not payload.manager_note:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="A rejection must include a manager_note."
        )

    # Update decision properties
    transaction_store.update_decision(
        transaction_id=txn_id,
        decision_type="MANAGER_DECISION",
        decision_maker="Manager",
        decision_reason=payload.manager_note
    )

    updated_ctx = transaction_store.set_status(txn_id, TransactionStatus.REJECTED)

    # Regenerate verdict text and update step 8 progress
    from app.orchestrator.verdict import generate_verdict_text
    verdict_text = generate_verdict_text(updated_ctx)
    transaction_store.add_progress(txn_id, 8, "Verdict issued", verdict_text)

    audit_feed.record(
        transaction_id=txn_id,
        event_type=AuditEventType.REJECTED,
        actor=actor,
        reason=f"Manager rejected via Notion. Reason: {payload.manager_note}",
    )

    # ⚠️  Intentionally no downstream calls here.
    return WebhookResponse(
        transaction_id=txn_id,
        action_taken="rejected",
        message="Transaction rejected. Audit entry recorded. No downstream execution.",
    )
