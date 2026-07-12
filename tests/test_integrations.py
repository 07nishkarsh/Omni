"""
tests/test_integrations.py

Verifies the full checklist for Gmail, Slack, and GPS integrations:

  ✓ Gmail/Slack only ever hit sandbox/test destinations in dev config
  ✓ Idempotency: calling the same execution twice sends only one message
  ✓ Mock GPS returns deterministic fixture coordinates for test applicant IDs
  ✓ All three failure modes are caught and raised, not silently swallowed
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.gmail import GmailClient, GmailError, TOOL_KEY as GMAIL_TOOL_KEY
from app.integrations.slack import SlackClient, SlackError, TOOL_KEY as SLACK_TOOL_KEY
from app.integrations.mock_services import simulate_location


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_settings(**overrides):
    s = MagicMock()
    s.use_mock_gmail = True
    s.use_mock_slack = True
    s.app_env = "development"
    s.gmail_sender_address = "no-reply@mockbank.example.com"
    s.gmail_sandbox_to = "sandbox@mockbank.example.com"
    s.slack_channel_id = "C00000000"
    s.slack_sandbox_channel = "bank-sim-test"
    s.slack_bot_token = "xoxb-mock"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ══════════════════════════════════════════════════════════════════════════════
# GMAIL TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestGmailSandbox:
    """Gmail calls only ever hit sandbox destinations in dev."""

    @pytest.mark.asyncio
    async def test_mock_mode_returns_mock_response(self):
        client = GmailClient()
        executed: set[str] = set()
        txn_id = uuid.uuid4()
        result = await client.send_loan_agreement(
            to="real-customer@bank.com",
            transaction_id=txn_id,
            amount="25000",
            currency="INR",
            executed_tools=executed,
        )
        assert result.get("_mock") is True

    @pytest.mark.asyncio
    async def test_live_mode_redirects_to_sandbox(self):
        """Even with use_mock_gmail=false, live calls must go to sandbox address, not customer."""
        settings = _mock_settings(use_mock_gmail=False, gmail_sandbox_to="sandbox@mockbank.example.com")

        sent_to_addresses = []

        async def _fake_send(self_inner, to, subject, body):
            sent_to_addresses.append(to)
            return {"id": "fake", "to": to, "_live": True}

        with patch("app.integrations.gmail.get_settings", return_value=settings), \
             patch.object(GmailClient, "_get_access_token", new_callable=AsyncMock, return_value="tok"), \
             patch.object(GmailClient, "send_notification", new_callable=AsyncMock, side_effect=_fake_send):

            client = GmailClient()
            client._settings = settings
            executed: set[str] = set()
            safe_to = client._safe_recipient("real-customer@bank.com")
            assert safe_to == "sandbox@mockbank.example.com"
            assert safe_to != "real-customer@bank.com"


class TestGmailIdempotency:
    """Calling send_loan_agreement twice with the same TransactionID sends only one email."""

    @pytest.mark.asyncio
    async def test_second_call_is_skipped(self):
        client = GmailClient()
        executed: set[str] = set()
        txn_id = uuid.uuid4()

        result1 = await client.send_loan_agreement("a@b.com", txn_id, "10000", "INR", executed)
        result2 = await client.send_loan_agreement("a@b.com", txn_id, "10000", "INR", executed)

        assert result1.get("_mock") is True
        assert result2.get("skipped") is True
        assert result2["reason"] == "already_sent"

    @pytest.mark.asyncio
    async def test_different_txn_ids_both_send(self):
        client = GmailClient()
        executed: set[str] = set()

        r1 = await client.send_loan_agreement("a@b.com", uuid.uuid4(), "10000", "INR", executed)
        r2 = await client.send_loan_agreement("a@b.com", uuid.uuid4(), "10000", "INR", executed)

        assert r1.get("_mock") is True
        assert r2.get("_mock") is True
        assert len(executed) == 2

    @pytest.mark.asyncio
    async def test_executed_tools_set_updated(self):
        client = GmailClient()
        executed: set[str] = set()
        txn_id = uuid.uuid4()

        await client.send_loan_agreement("a@b.com", txn_id, "10000", "INR", executed)
        assert f"{GMAIL_TOOL_KEY}:{txn_id}" in executed


class TestGmailErrorHandling:
    """Gmail failures are caught, logged, and re-raised as GmailError."""

    @pytest.mark.asyncio
    async def test_network_error_raises_gmail_error(self):
        import httpx
        settings = _mock_settings(use_mock_gmail=False, gmail_sandbox_to="sandbox@test.com")

        with patch("app.integrations.gmail.get_settings", return_value=settings), \
             patch.object(GmailClient, "_get_access_token", new_callable=AsyncMock, return_value="tok"), \
             patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("timeout")):

            client = GmailClient()
            client._settings = settings
            with pytest.raises(GmailError, match="Failed to send email"):
                await client.send_notification("x@y.com", "subj", "body")


# ══════════════════════════════════════════════════════════════════════════════
# SLACK TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSlackSandbox:
    """Slack messages only ever hit sandbox channels in dev."""

    @pytest.mark.asyncio
    async def test_mock_mode_returns_mock_response(self):
        client = SlackClient()
        executed: set[str] = set()
        txn_id = uuid.uuid4()
        result = await client.post_branch_alert(txn_id, "Test alert", executed)
        assert result.get("_mock") is True

    def test_live_mode_redirects_to_sandbox_channel(self):
        settings = _mock_settings(
            use_mock_slack=False,
            slack_sandbox_channel="bank-sim-test",
            app_env="development",
        )
        with patch("app.integrations.slack.get_settings", return_value=settings):
            client = SlackClient()
            client._settings = settings
            channel = client._safe_channel()
            assert channel == "bank-sim-test"
            assert channel != "C00000000"


class TestSlackIdempotency:
    """Calling post_branch_alert twice with the same TransactionID sends only one message."""

    @pytest.mark.asyncio
    async def test_second_call_is_skipped(self):
        client = SlackClient()
        executed: set[str] = set()
        txn_id = uuid.uuid4()

        result1 = await client.post_branch_alert(txn_id, "Alert 1", executed)
        result2 = await client.post_branch_alert(txn_id, "Alert 2", executed)

        assert result1.get("_mock") is True
        assert result2.get("skipped") is True
        assert result2["reason"] == "already_posted"

    @pytest.mark.asyncio
    async def test_different_txn_ids_both_post(self):
        client = SlackClient()
        executed: set[str] = set()

        r1 = await client.post_branch_alert(uuid.uuid4(), "Alert A", executed)
        r2 = await client.post_branch_alert(uuid.uuid4(), "Alert B", executed)

        assert r1.get("_mock") is True
        assert r2.get("_mock") is True
        assert len(executed) == 2

    @pytest.mark.asyncio
    async def test_executed_tools_set_updated(self):
        client = SlackClient()
        executed: set[str] = set()
        txn_id = uuid.uuid4()

        await client.post_branch_alert(txn_id, "Alert", executed)
        assert f"{SLACK_TOOL_KEY}:{txn_id}" in executed


class TestSlackErrorHandling:
    """Slack failures are caught, logged, and re-raised as SlackError."""

    @pytest.mark.asyncio
    async def test_network_error_raises_slack_error(self):
        import httpx
        settings = _mock_settings(use_mock_slack=False, slack_sandbox_channel="test")

        with patch("app.integrations.slack.get_settings", return_value=settings), \
             patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("timeout")):

            client = SlackClient()
            client._settings = settings
            with pytest.raises(SlackError, match="Failed to post to Slack"):
                await client.post_message("test message", channel="test-chan")

    @pytest.mark.asyncio
    async def test_slack_api_ok_false_raises_slack_error(self):
        settings = _mock_settings(use_mock_slack=False, slack_bot_token="xoxb-real")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"ok": False, "error": "channel_not_found"}

        with patch("app.integrations.slack.get_settings", return_value=settings), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):

            client = SlackClient()
            client._settings = settings
            with pytest.raises(SlackError, match="channel_not_found"):
                await client.post_message("test", channel="bad-channel")


# ══════════════════════════════════════════════════════════════════════════════
# GPS / LOCATION MOCK TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSimulateLocation:
    """GPS mock returns deterministic fixture coordinates for test applicant IDs."""

    def test_rural_applicant_returns_rural_coords(self):
        # "rural" in applicant_id → rural zone fixture
        result = simulate_location("rural-applicant-001")
        assert result["zone_type"] == "rural"
        assert result["lat"] == 20.5937
        assert result["lon"] == 78.9629
        assert result["restricted"] is False
        assert result["_mock"] is True

    def test_urban_applicant_returns_urban_coords(self):
        result = simulate_location("urban-zone-applicant")
        assert result["zone_type"] == "urban"
        assert result["lat"] == 28.6139
        assert result["lon"] == 77.2090
        assert result["restricted"] is False

    def test_flagged_applicant_returns_restricted_coords(self):
        result = simulate_location("flagged-zone-applicant")
        assert result["zone_type"] == "restricted"
        assert result["restricted"] is True
        assert result["lat"] == 0.0
        assert result["lon"] == 0.0

    def test_unknown_applicant_returns_default_coords(self):
        result = simulate_location("generic-applicant-xyz")
        assert result["zone_type"] == "standard"
        assert result["lat"] == 19.0760
        assert result["lon"] == 72.8777
        assert result["restricted"] is False

    def test_uuid_applicant_id_accepted(self):
        result = simulate_location(uuid.uuid4())
        assert "lat" in result
        assert "lon" in result
        assert result["_mock"] is True

    def test_same_id_always_returns_same_coords(self):
        """Determinism: calling twice returns identical results."""
        app_id = "rural-test-applicant"
        r1 = simulate_location(app_id)
        r2 = simulate_location(app_id)
        assert r1["lat"] == r2["lat"]
        assert r1["lon"] == r2["lon"]
        assert r1["zone_type"] == r2["zone_type"]

    def test_disclaimer_is_present(self):
        result = simulate_location("any-applicant")
        assert "_disclaimer" in result
        assert "Simulated" in result["_disclaimer"]
