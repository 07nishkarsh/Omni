"""
app/integrations/slack.py

Posts branch-alert messages to a Slack channel.

Safety guarantees (dev/test):
  - USE_MOCK_SLACK=true  → No network call. Mock response returned.
  - SLACK_SANDBOX_CHANNEL overrides the destination so live calls only
    ever hit a dedicated test channel (e.g. #bank-sim-test), never a
    production ops channel.
  - Idempotency: the caller passes a TransactionID; if that ID already
    exists in the executed_tools set, this function is a no-op.

Error policy:
  - All exceptions (network timeouts, non-ok Slack responses, etc.) are
    caught, logged with structlog, and re-raised as SlackError.
    Nothing is ever silently swallowed.
"""

from __future__ import annotations

from uuid import UUID

import httpx
import structlog

from app.config import get_settings
from app.integrations.mock_services import mock_slack_post_message

log = structlog.get_logger(__name__)

_SLACK_POST_URL = "https://slack.com/api/chat.postMessage"

TOOL_KEY = "slack:branch_alert"


class SlackError(Exception):
    """Raised when the Slack adapter cannot post a message."""


class SlackClient:
    """
    Posts workflow-event messages to Slack.

    All calls are idempotent per TransactionID via the executed_tools set
    stored on the transaction record.
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    # ── Public alert helpers ──────────────────────────────────────────────────

    async def post_branch_alert(
        self,
        transaction_id: UUID,
        message: str,
        executed_tools: set[str],
    ) -> dict:
        """
        Post a branch-alert message for the given transaction.

        Args:
            transaction_id: Parent transaction UUID for idempotency key.
            message: Human-readable alert body.
            executed_tools: Mutable set tracking fired tools. Updated in place.

        Returns:
            Slack API response dict (or mock equivalent).

        Raises:
            SlackError: on any send failure.
        """
        # ── Idempotency guard ────────────────────────────────────────────────
        key = f"{TOOL_KEY}:{transaction_id}"
        if key in executed_tools:
            log.info("slack.idempotency_skip", key=key, transaction_id=str(transaction_id))
            return {"skipped": True, "reason": "already_posted", "key": key}

        channel = self._safe_channel()
        blocks = self._build_alert_blocks(str(transaction_id), message, channel)
        result = await self.post_message(channel=channel, text=message, blocks=blocks)

        # Mark as executed.
        executed_tools.add(key)
        log.info("slack.branch_alert_posted", transaction_id=str(transaction_id), channel=channel)
        return result

    async def post_message(self, text: str, channel: str | None = None, blocks: list | None = None) -> dict:
        """Low-level post. Raises SlackError on failure."""
        target_channel = channel or self._safe_channel()
        try:
            if self._settings.use_mock_slack:
                log.info("slack.mock.post_message", channel=target_channel, text=text[:80])
                return mock_slack_post_message(target_channel, text, blocks)

            payload: dict = {"channel": target_channel, "text": text}
            if blocks:
                payload["blocks"] = blocks

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    _SLACK_POST_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._settings.slack_bot_token}"},
                )
                resp.raise_for_status()
                data = resp.json()

            if not data.get("ok"):
                error_code = data.get("error", "unknown_error")
                log.error("slack.api_error", error=error_code, channel=target_channel)
                raise SlackError(f"Slack API returned ok=false: {error_code}")

            return data

        except SlackError:
            raise
        except Exception as exc:
            log.error("slack.post_failed", channel=target_channel, error=str(exc))
            raise SlackError(f"Failed to post to Slack channel {target_channel}: {exc}") from exc

    async def post_webhook(self, text: str) -> dict:
        """Post a simple message via the Incoming Webhook URL (no idempotency needed)."""
        try:
            if self._settings.use_mock_slack:
                log.info("slack.mock.post_webhook", text=text[:80])
                return mock_slack_post_message("webhook", text)

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self._settings.slack_webhook_url, json={"text": text}
                )
                resp.raise_for_status()
                return {"ok": True, "status_code": resp.status_code}
        except Exception as exc:
            log.error("slack.webhook_failed", error=str(exc))
            raise SlackError(f"Webhook post failed: {exc}") from exc

    # ── Backward-compat convenience helpers (kept for existing callers) ────────

    async def notify_transaction_approved(self, transaction_id: str) -> dict:
        return await self.post_message(
            text=f"✅ [SIMULATION] Transaction `{transaction_id}` approved in workflow."
        )

    async def notify_transaction_rejected(self, transaction_id: str) -> dict:
        return await self.post_message(
            text=f"❌ [SIMULATION] Transaction `{transaction_id}` rejected in workflow."
        )

    async def notify_escalation(self, transaction_id: str) -> dict:
        return await self.post_message(
            text=f"⚠️ [SIMULATION] Transaction `{transaction_id}` escalated for human review."
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _safe_channel(self) -> str:
        """
        In dev/staging, redirect all messages to the configured sandbox channel
        so we never accidentally alert a real production ops channel.
        Reads SLACK_SANDBOX_CHANNEL from settings; falls back to the configured
        channel ID (which is a mock ID in dev).
        """
        sandbox = getattr(self._settings, "slack_sandbox_channel", "").strip()
        if sandbox:
            return sandbox
        if self._settings.app_env in ("development", "staging"):
            return self._settings.slack_channel_id  # C00000000 in dev
        return self._settings.slack_channel_id

    @staticmethod
    def _build_alert_blocks(txn_id: str, message: str, channel: str) -> list:
        """Build Slack Block Kit payload for a structured branch alert."""
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🏦 Branch Alert [SIMULATION]"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Transaction ID:*\n`{txn_id}`"},
                    {"type": "mrkdwn", "text": f"*Channel:*\n#{channel}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "⚠️ Simulated banking workflow — no real data."},
                ],
            },
        ]
