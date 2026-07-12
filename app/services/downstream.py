"""
app/services/downstream.py

Stub downstream execution layer triggered only for APPROVED transactions.

Represents the actions taken after human approval:
  - Gmail notification to the customer
  - Slack alert to the ops channel
  - Core-banking / ledger write

All integrations are mocked by default (USE_MOCK_GMAIL/SLACK=true in .env).
The Rejected path NEVER calls any of these functions.
"""

from __future__ import annotations

import structlog

from app.config import get_settings
from app.models.transaction import TransactionContext

log = structlog.get_logger(__name__)


async def execute_approved_transaction(ctx: TransactionContext) -> dict[str, str]:
    """
    Run all downstream side-effects for a manager-approved transaction.

    Returns a summary dict of what was executed / mocked.

    This is intentionally a stub — replace each section with the real
    Gmail/Slack/ledger client when USE_MOCK_* flags are disabled.
    """
    settings = get_settings()
    results: dict[str, str] = {}

    # ── Gmail notification ────────────────────────────────────────────────────
    if settings.use_mock_gmail:
        log.info(
            "downstream.gmail.mock",
            to=ctx.customer_email,
            transaction_id=str(ctx.transaction_id),
        )
        results["gmail"] = f"MOCK: approval email sent to {ctx.customer_email}"
    else:
        # TODO: integrate real Gmail client here
        log.warning("downstream.gmail.live_not_implemented")
        results["gmail"] = "SKIPPED: live Gmail not wired up yet"

    # ── Slack alert ───────────────────────────────────────────────────────────
    if settings.use_mock_slack:
        log.info(
            "downstream.slack.mock",
            channel=settings.slack_channel_id,
            transaction_id=str(ctx.transaction_id),
        )
        results["slack"] = f"MOCK: Slack alert sent to #{settings.slack_channel_id}"
    else:
        # TODO: integrate real Slack client here
        log.warning("downstream.slack.live_not_implemented")
        results["slack"] = "SKIPPED: live Slack not wired up yet"

    # ── Ledger write ──────────────────────────────────────────────────────────
    if settings.use_mock_notion:
        log.info(
            "downstream.ledger.mock",
            transaction_id=str(ctx.transaction_id),
            amount=str(ctx.requested_amount),
        )
        results["ledger"] = f"MOCK: ledger entry written for {ctx.requested_amount} {ctx.currency}"
    else:
        # TODO: write real Notion ledger row here
        log.warning("downstream.ledger.live_not_implemented")
        results["ledger"] = "SKIPPED: live Notion ledger not wired up yet"

    log.info(
        "downstream.complete",
        transaction_id=str(ctx.transaction_id),
        results=results,
    )
    return results
