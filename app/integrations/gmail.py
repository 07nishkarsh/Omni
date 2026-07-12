"""
app/integrations/gmail.py

Sends loan-agreement notification emails.

Safety guarantees (dev/test):
  - USE_MOCK_GMAIL=true  → No network call. Mock response returned.
  - GMAIL_SANDBOX_TO overrides the 'to' address so live calls only ever
    hit a sandbox/test inbox, never a real customer address.
  - Idempotency: the caller passes a TransactionID; if that ID already
    exists in the executed_tools set (tracked by TransactionStore), this
    function is a no-op and returns the cached result.

Error policy:
  - All exceptions are caught, logged with structlog, and re-raised as
    GmailError so the caller can handle them explicitly (never silent).
"""

from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from uuid import UUID

import httpx
import structlog

from app.config import get_settings
from app.integrations.mock_services import mock_gmail_send

log = structlog.get_logger(__name__)

_GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

TOOL_KEY = "gmail:loan_agreement"


class GmailError(Exception):
    """Raised when the Gmail adapter cannot send an email."""


class GmailClient:
    """
    Sends transactional notification emails via the Gmail API.

    All calls are idempotent per TransactionID via the executed_tools set
    stored on the transaction record.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._access_token: str | None = None

    # ── Public send helpers ───────────────────────────────────────────────────

    async def send_loan_agreement(
        self,
        to: str,
        transaction_id: UUID,
        amount: str,
        currency: str,
        executed_tools: set[str],
    ) -> dict:
        """
        Send a loan agreement email.

        Args:
            to: Customer email (overridden by GMAIL_SANDBOX_TO in dev).
            transaction_id: Parent transaction UUID for idempotency key.
            amount: Loan amount string.
            currency: Currency code.
            executed_tools: Mutable set tracking which tools have already
                            fired for this transaction. Updated in place.

        Returns:
            Gmail API response dict (or mock equivalent).

        Raises:
            GmailError: on any send failure.
        """
        # ── Idempotency guard ────────────────────────────────────────────────
        key = f"{TOOL_KEY}:{transaction_id}"
        if key in executed_tools:
            log.info("gmail.idempotency_skip", key=key, transaction_id=str(transaction_id))
            return {"skipped": True, "reason": "already_sent", "key": key}

        # ── Sandbox override: never send to real addresses in dev ─────────────
        safe_to = self._safe_recipient(to)
        subject = "[SIMULATION] Your Loan Agreement — Action Required"
        body = self._build_loan_agreement_body(str(transaction_id), amount, currency, safe_to)

        result = await self.send_notification(safe_to, subject, body)

        # Mark as executed — caller must persist this set.
        executed_tools.add(key)
        log.info("gmail.loan_agreement_sent", transaction_id=str(transaction_id), to=safe_to)
        return result

    async def send_notification(self, to: str, subject: str, body: str) -> dict:
        """Low-level send. Raises GmailError on failure."""
        try:
            if self._settings.use_mock_gmail:
                log.info("gmail.mock.send", to=to, subject=subject)
                return mock_gmail_send(to, subject, body)

            token = await self._get_access_token()
            msg = MIMEMultipart("alternative")
            msg["to"] = to
            msg["from"] = self._settings.gmail_sender_address
            msg["subject"] = subject
            msg.attach(MIMEText(body, "plain"))
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    _GMAIL_SEND_URL,
                    json={"raw": raw},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                return resp.json()
        except GmailError:
            raise
        except Exception as exc:
            log.error("gmail.send_failed", to=to, subject=subject, error=str(exc))
            raise GmailError(f"Failed to send email to {to}: {exc}") from exc

    # ── Backward-compat convenience helpers (kept for existing callers) ────────

    async def send_approval_notice(self, to: str, transaction_id: str) -> dict:
        return await self.send_notification(
            to=self._safe_recipient(to),
            subject="[SIMULATION] Your application has been approved",
            body=(
                f"Transaction {transaction_id} has been approved in the simulated workflow.\n\n"
                "This is an automated message from a banking workflow simulation. "
                "No real financial decision has been made."
            ),
        )

    async def send_rejection_notice(self, to: str, transaction_id: str) -> dict:
        return await self.send_notification(
            to=self._safe_recipient(to),
            subject="[SIMULATION] Your application could not be approved",
            body=(
                f"Transaction {transaction_id} was not approved in the simulated workflow.\n\n"
                "This is an automated message from a banking workflow simulation. "
                "No real financial decision has been made."
            ),
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _safe_recipient(self, original_to: str) -> str:
        """
        In dev/staging, redirect all outgoing mail to the configured sandbox
        address so we never accidentally email real customers.
        Reads GMAIL_SANDBOX_TO from settings; falls back to the sender address
        which is always a mock address in dev.
        """
        sandbox = getattr(self._settings, "gmail_sandbox_to", "").strip()
        if sandbox:
            return sandbox
        if self._settings.app_env in ("development", "staging"):
            # Never deliver to real addresses in dev — use the mock sender.
            return self._settings.gmail_sender_address
        return original_to

    @staticmethod
    def _build_loan_agreement_body(txn_id: str, amount: str, currency: str, to: str) -> str:
        return (
            f"Dear Applicant,\n\n"
            f"Your loan application (Transaction ID: {txn_id}) has been approved.\n"
            f"Approved Amount: {amount} {currency}\n\n"
            f"This is an automated message from a SIMULATED banking workflow.\n"
            f"No real financial decision has been made. No real funds have been disbursed.\n\n"
            f"[Sent to sandbox address: {to}]"
        )

    async def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    _GMAIL_TOKEN_URL,
                    data={
                        "client_id": self._settings.gmail_client_id,
                        "client_secret": self._settings.gmail_client_secret,
                        "refresh_token": self._settings.gmail_refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                resp.raise_for_status()
                self._access_token = resp.json()["access_token"]
        except Exception as exc:
            log.error("gmail.token_refresh_failed", error=str(exc))
            raise GmailError(f"Could not refresh Gmail token: {exc}") from exc
        return self._access_token  # type: ignore[return-value]
