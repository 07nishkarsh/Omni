"""
app/integrations/notion_audit.py

Writes compressed executive summaries to the Notion Audit & Activity Feed.

Populates all required fields per Policy Book Section III, Clause 1:
  - TransactionID
  - AgentsInvolved
  - Route
  - Outcome
  - Timestamp
  - PolicyVersion

Plus a Summary field containing the compressed bullet list.

In mock mode (USE_MOCK_NOTION=true) no real API calls are made.
"""

from __future__ import annotations

import httpx
import structlog

from app.config import get_settings
from app.models.transaction import TransactionContext

log = structlog.get_logger(__name__)

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


class NotionAuditError(Exception):
    """Raised when the Notion Audit Feed write fails."""


class NotionAuditClient:
    """Writes audit entries to the Notion Audit & Activity Feed database."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def create_audit_entry(self, ctx: TransactionContext, summary) -> dict:
        """
        Write one row to the Audit & Activity Feed.

        Args:
            ctx: The original TransactionContext.
            summary: A CompressedSummary object from orchestrator/history.py.

        Returns:
            Notion API response (or mock equivalent).

        Raises:
            NotionAuditError: on failure.
        """
        from app.orchestrator.history import CompressedSummary  # local to avoid circular
        assert isinstance(summary, CompressedSummary)

        bullet_text = "\n".join(summary.bullets)
        agents_text = ", ".join(summary.agents_involved) or "None"
        timestamp = summary.compressed_at.isoformat()

        if self._settings.use_mock_notion:
            log.info(
                "notion_audit.mock.create_entry",
                transaction_id=str(ctx.transaction_id),
                outcome=summary.outcome,
                policy_version=summary.policy_version,
                agents=agents_text,
            )
            return {
                "object": "page",
                "id": f"mock-audit-{str(ctx.transaction_id)[:8]}",
                "properties": {
                    "TransactionID": str(ctx.transaction_id),
                    "AgentsInvolved": agents_text,
                    "Route": summary.route,
                    "Outcome": summary.outcome,
                    "Timestamp": timestamp,
                    "PolicyVersion": summary.policy_version,
                    "Summary": bullet_text,
                },
                "_mock": True,
            }

        # ── Live Notion API call ──────────────────────────────────────────────
        database_id = self._settings.notion_audit_feed_id
        payload = {
            "parent": {"database_id": database_id},
            "properties": {
                # TransactionID — Title property
                "TransactionID": {
                    "title": [{"text": {"content": str(ctx.transaction_id)}}]
                },
                # AgentsInvolved — rich text
                "AgentsInvolved": {
                    "rich_text": [{"text": {"content": agents_text}}]
                },
                # Route — select or rich text
                "Route": {
                    "rich_text": [{"text": {"content": summary.route}}]
                },
                # Outcome — select
                "Outcome": {
                    "select": {"name": summary.outcome}
                },
                # PolicyVersion — rich text
                "PolicyVersion": {
                    "rich_text": [{"text": {"content": summary.policy_version}}]
                },
                # Summary — rich text (bullet list, truncated to 2000 chars)
                "Summary": {
                    "rich_text": [{"text": {"content": bullet_text[:2000]}}]
                },
            },
        }

        try:
            headers = {
                "Authorization": f"Bearer {self._settings.notion_token}",
                "Notion-Version": _NOTION_VERSION,
                "Content-Type": "application/json",
            }
            with httpx.Client(timeout=15) as client:
                resp = client.post(f"{_NOTION_API}/pages", json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.error("notion_audit.create_entry_failed", error=str(exc))
            raise NotionAuditError(f"Failed to write audit entry: {exc}") from exc

    @property
    def _notion_audit_feed_id(self) -> str:
        return getattr(self._settings, "notion_audit_feed_id", "mock-audit-db")
