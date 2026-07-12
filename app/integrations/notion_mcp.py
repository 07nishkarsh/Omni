"""
Notion MCP integration adapter.

When ``USE_MOCK_NOTION=true`` (the default) all calls return deterministic
mock data from ``mock_services`` — no Notion API calls are made.

When ``USE_MOCK_NOTION=false`` real HTTP calls are sent to the Notion API
using the ``NOTION_TOKEN`` from config.

⚠️  This project is a simulation.  Even in live mode the data written to
    Notion is synthetic workflow metadata, not real financial data.
"""

from __future__ import annotations

from uuid import UUID

import httpx
import structlog

from app.config import get_settings
from app.integrations.mock_services import mock_notion_create_page, mock_notion_query_database

log = structlog.get_logger(__name__)

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


class NotionClient:
    """Thin wrapper around the Notion REST API with mock-mode support."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.notion_token}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def create_transaction_page(
        self,
        transaction_id: UUID,
        status: str,
        notes: str = "",
    ) -> dict:
        """Create a Notion page representing a transaction record."""
        if self._settings.use_mock_notion:
            log.info("notion.mock.create_page", transaction_id=str(transaction_id))
            return mock_notion_create_page(
                self._settings.notion_database_id, transaction_id, status, notes
            )

        payload = {
            "parent": {"database_id": self._settings.notion_database_id},
            "properties": {
                "TransactionID": {
                    "title": [{"text": {"content": str(transaction_id)}}]
                },
                "Status": {"select": {"name": status}},
                "Notes": {"rich_text": [{"text": {"content": notes}}]},
            },
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_NOTION_API}/pages", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()

    async def query_transactions(self, filter_status: str | None = None) -> dict:
        """Query the Notion database for transactions, optionally filtered by status."""
        if self._settings.use_mock_notion:
            log.info("notion.mock.query_database", filter_status=filter_status)
            return mock_notion_query_database(
                self._settings.notion_database_id, filter_status
            )

        payload: dict = {"page_size": 50}
        if filter_status:
            payload["filter"] = {
                "property": "Status",
                "select": {"equals": filter_status},
            }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_NOTION_API}/databases/{self._settings.notion_database_id}/query",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()
