"""
Mock services — synthetic data generators for all external integrations.

This module is the single source of truth for **mocked** external responses.
It is used by notion_mcp, gmail, and slack adapters when their ``use_mock_*``
config flags are True (the default).

⚠️  Nothing in this module makes real network calls or accesses real data.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4


# ── Notion mock ───────────────────────────────────────────────────────────────

def mock_notion_create_page(
    database_id: str, transaction_id: UUID, status: str, notes: str = ""
) -> dict:
    """Return a fake Notion page creation response."""
    return {
        "object": "page",
        "id": str(uuid4()),
        "created_time": datetime.now(timezone.utc).isoformat(),
        "url": f"https://www.notion.so/mock-page-{uuid4().hex[:8]}",
        "properties": {
            "TransactionID": {"title": [{"text": {"content": str(transaction_id)}}]},
            "Status": {"select": {"name": status}},
            "Notes": {"rich_text": [{"text": {"content": notes}}]},
        },
        "_mock": True,
    }


def mock_notion_query_database(database_id: str, filter_status: str | None = None) -> dict:
    """Return a fake Notion database query result."""
    entries = []
    for _ in range(random.randint(1, 3)):
        entries.append({
            "object": "page",
            "id": str(uuid4()),
            "properties": {
                "Status": {"select": {"name": filter_status or "pending"}},
            },
        })
    return {"object": "list", "results": entries, "_mock": True}


# ── Gmail mock ────────────────────────────────────────────────────────────────

def mock_gmail_send(to: str, subject: str, body: str) -> dict:
    """Return a fake Gmail send response."""
    return {
        "id": f"mock-msg-{uuid4().hex[:12]}",
        "threadId": f"mock-thread-{uuid4().hex[:12]}",
        "labelIds": ["SENT"],
        "to": to,
        "subject": subject,
        "snippet": body[:100],
        "_mock": True,
    }


# ── Slack mock ────────────────────────────────────────────────────────────────

def mock_slack_post_message(channel: str, text: str, blocks: list | None = None) -> dict:
    """Return a fake Slack message post response."""
    return {
        "ok": True,
        "channel": channel,
        "ts": f"{datetime.now(timezone.utc).timestamp():.6f}",
        "message": {
            "text": text,
            "bot_id": "B000MOCK",
        },
        "_mock": True,
    }


# ── Core-banking mock ─────────────────────────────────────────────────────────

def mock_get_account_balance(account_id: UUID) -> dict:
    """Return a synthetic account balance. No real bank account is queried."""
    return {
        "account_id": str(account_id),
        "balance": float(Decimal(str(random.uniform(500, 50000))).quantize(Decimal("0.01"))),
        "currency": "USD",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "_mock": True,
    }


def mock_get_credit_report(customer_id: UUID) -> dict:
    """Return a synthetic credit report. No real credit bureau is contacted."""
    return {
        "customer_id": str(customer_id),
        "credit_score": random.randint(580, 820),
        "report_date": datetime.now(timezone.utc).date().isoformat(),
        "bureau": "MockBureau (synthetic data only)",
        "open_accounts": random.randint(1, 8),
        "derogatory_marks": random.randint(0, 2),
        "_mock": True,
        "_disclaimer": (
            "This is entirely synthetic data generated for workflow simulation. "
            "No real credit information has been accessed."
        ),
    }


def simulate_tax_registry(applicant_id: UUID | str) -> dict:
    """
    Return a deterministic set of tax/compliance flags for testing.
    This mock service is seeded with fixtures for reproducible tests.
    """
    applicant_id_str = str(applicant_id)
    
    flags = []
    
    # Deterministic fixtures based on ID content (matching the utf-8 hex encoding from tests)
    # "flagged" -> 666c6167676564
    # "disputed" -> 6469737075746564
    hex_id = applicant_id_str.replace("-", "")
    if "666c6167676564" in hex_id:
        flags.append("2-year-old unresolved tax dispute")
        flags.append("Recent suspicious transaction pattern")
    elif "6469737075746564" in hex_id:
        flags.append("Minor tax discrepancy under review")
    
    return {
        "applicant_id": applicant_id_str,
        "flags": flags,
        "_mock": True,
        "_disclaimer": "This is entirely synthetic data generated for workflow simulation."
    }

def simulate_treasury_ledger(transaction_id: UUID | str) -> dict:
    """
    Return a deterministic treasury fund state for testing Agent C.
    This simulates reading from the Notion Treasury & Scheme Ledger.
    """
    txn_id_str = str(transaction_id)
    
    # Deterministic fixtures based on ID content (matching the utf-8 hex encoding from tests)
    # "frozen" -> 66726f7a656e
    # "insufficient" -> 696e73756666696369656e74
    hex_id = txn_id_str.replace("-", "")
    if "66726f7a656e" in hex_id:
        status = "Frozen"
        balance = Decimal("100000.00")
    elif "696e73756666696369656e74" in hex_id:
        status = "Active"
        balance = Decimal("5000.00")  # Less than requested in tests
    else:
        status = "Active"
        balance = Decimal("100000.00")
        
    return {
        "fund_name": "General Subsidy Fund",
        "fund_status": status,
        "available_balance": balance,
        "_mock": True
    }


# ── GPS / Location mock ───────────────────────────────────────────────────────
#
# Fixture encoding (UTF-8 hex of applicant_id substring):
#   "rural"   → 72757261 6c     → returns a rural coordinate pair
#   "urban"   → 7572 62616e    → returns a city-centre coordinate pair
#   "flagged" → 666c6167676564 → returns a flagged/restricted-zone coordinate pair
#
# All coordinates are fictitious and located in the middle of the ocean or
# unpopulated areas to avoid accidentally matching a real address.

_GPS_FIXTURES: dict[str, dict] = {
    # Keyword hex  → fixture
    "72757261": {           # "rura"  (prefix of "rural")
        "lat": 20.5937,
        "lon": 78.9629,
        "label": "Rural Zone — Central India (simulated)",
        "zone_type": "rural",
        "restricted": False,
    },
    "757262616e": {         # "urban"
        "lat": 28.6139,
        "lon": 77.2090,
        "label": "Urban Zone — New Delhi (simulated)",
        "zone_type": "urban",
        "restricted": False,
    },
    "666c616767": {         # "flagg" (prefix of "flagged")
        "lat": 0.0,
        "lon": 0.0,
        "label": "Restricted Zone — Null Island (simulated)",
        "zone_type": "restricted",
        "restricted": True,
    },
}

_DEFAULT_GPS = {
    "lat": 19.0760,
    "lon": 72.8777,
    "label": "Default Zone — Mumbai (simulated)",
    "zone_type": "standard",
    "restricted": False,
}


def simulate_location(applicant_id: UUID | str) -> dict:
    """
    Return deterministic mock GPS coordinates for a given applicant ID.

    Fixture selection is based on the applicant_id string content so tests
    are fully reproducible without any randomness or network calls.

    Returns:
        {
            "applicant_id": str,
            "lat": float,
            "lon": float,
            "label": str,
            "zone_type": "rural" | "urban" | "standard" | "restricted",
            "restricted": bool,
            "_mock": True,
            "_disclaimer": str,
        }
    """
    applicant_id_str = str(applicant_id)
    hex_id = applicant_id_str.encode("utf-8").hex()

    fixture = _DEFAULT_GPS
    for hex_key, coords in _GPS_FIXTURES.items():
        if hex_key in hex_id:
            fixture = coords
            break

    return {
        "applicant_id": applicant_id_str,
        **fixture,
        "_mock": True,
        "_disclaimer": (
            "Simulated GPS coordinates only. No real location data is used or stored."
        ),
    }


