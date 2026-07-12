"""
app/scripts/persistence.py

Fixture loader — reads fixtures.json and builds TransactionContext objects.

This is the single source of truth for all simulate_trigger runs.
No new mock logic is introduced here — the TransactionContext it builds
is identical to what production code would receive from the real API.

The target_fund value from trigger_defaults is injected into the
transaction_id so that mock_services (simulate_treasury_ledger) can
deterministically simulate the correct fund state:
  - "frozen"       → Frozen fund → TREASURY_REJECT + human review
  - "insufficient" → Active fund with ₹5,000 balance → PARTIAL
  - "general"      → Active fund with ₹100,000 balance → APPROVED
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

from app.models.transaction import TransactionContext, TransactionType

_FIXTURES_PATH = Path(__file__).parent / "fixtures.json"

VALID_TRIGGER_TYPES = frozenset({
    "subsidy_loan",
    "emergency_payout",
    "adversarial_threshold_dodge",
    "adversarial_round_cap",
})


class FixtureError(Exception):
    """Raised when fixtures.json is missing, malformed, or the applicant_id is unknown."""


def load_fixtures() -> dict:
    """Load and return the raw fixtures dict."""
    if not _FIXTURES_PATH.exists():
        raise FixtureError(f"fixtures.json not found at {_FIXTURES_PATH}")
    try:
        return json.loads(_FIXTURES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureError(f"fixtures.json is not valid JSON: {exc}") from exc


def list_applicants() -> dict[str, dict]:
    """Return the applicants dict keyed by applicant_id."""
    return load_fixtures()["applicants"]


def build_transaction_context(
    trigger_type: str,
    applicant_id: str,
) -> TransactionContext:
    """
    Construct a TransactionContext from fixture data.

    The transaction_id UUID is generated fresh each call so every run is unique.
    The target_fund keyword from trigger_defaults is encoded in the UUID hex
    so mock_services.simulate_treasury_ledger picks the correct fund state
    deterministically — this is the same encoding already used in the test suite.

    Args:
        trigger_type: One of VALID_TRIGGER_TYPES.
        applicant_id: Must exist in fixtures.json["applicants"].

    Returns:
        A fully-populated TransactionContext ready for StateMachine.run_pipeline().

    Raises:
        FixtureError: if trigger_type or applicant_id are unknown.
    """
    if trigger_type not in VALID_TRIGGER_TYPES:
        raise FixtureError(
            f"Unknown trigger_type '{trigger_type}'. "
            f"Must be one of: {sorted(VALID_TRIGGER_TYPES)}"
        )

    fixtures = load_fixtures()
    applicants = fixtures["applicants"]

    if applicant_id not in applicants:
        raise FixtureError(
            f"Applicant '{applicant_id}' not found in fixtures.json. "
            f"Available: {sorted(applicants)}"
        )

    applicant = applicants[applicant_id]
    defaults = fixtures["trigger_defaults"][trigger_type]

    # ── Build a deterministic-ish UUID that encodes the target_fund keyword ──
    # This lets simulate_treasury_ledger in mock_services.py return the correct
    # fund state without any extra wiring. We use the same UTF-8 hex encoding
    # that the test suite uses (see test_agent_c.py).
    target_fund: str = defaults.get("target_fund", "general")
    fund_hex = target_fund.encode("utf-8").hex().ljust(32, "0")[:32]
    # XOR with a fresh random UUID to ensure uniqueness across runs.
    fresh_hex = uuid.uuid4().hex
    mixed_hex = format(int(fund_hex, 16) ^ int(fresh_hex, 16), "032x")
    transaction_id = uuid.UUID(mixed_hex)

    # ── Build metadata dict ───────────────────────────────────────────────────
    metadata: dict[str, str] = {
        "trigger_type": trigger_type,
        "applicant_id": applicant_id,
        "target_fund": target_fund,
    }
    if "guarantor" in applicant:
        metadata["guarantor"] = applicant["guarantor"]

    # ── Compose notes: applicant notes + trigger description ─────────────────
    notes = (
        f"{applicant.get('notes', '')} | "
        f"Trigger: {defaults.get('description', trigger_type)}"
    )
    if "guarantor" in applicant:
        notes += f" | Guarantor: {applicant['guarantor']}"

    return TransactionContext(
        transaction_id=transaction_id,
        transaction_type=TransactionType(defaults["transaction_type"]),
        customer_name=applicant["name"],
        customer_email=applicant["email"],
        requested_amount=Decimal(defaults["requested_amount"]),
        currency=defaults.get("currency", "INR"),
        mock_credit_score=applicant.get("mock_credit_score", 700),
        mock_annual_income=Decimal(applicant.get("mock_annual_income", "50000.00")),
        urgency_flag=defaults["urgency_flag"],
        requested_subsidy_pct=float(defaults.get("requested_subsidy_pct", 0.0)),
        notes=notes[:2000],
        metadata=metadata,
    )
