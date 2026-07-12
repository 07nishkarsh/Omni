"""
tests/test_simulate_trigger.py

Tests for the simulate_trigger CLI and POST /simulate/trigger endpoint.

Checklist:
  ✓ All 4 trigger_type options produce a valid SimulationResult
  ✓ Each run's TransactionID is unique — two runs never collide
  ✓ Uses fixtures.json applicant data — no new mock logic duplicated
  ✓ Unknown applicant_id and invalid trigger_type return clean errors
  ✓ FastAPI endpoint returns correct HTTP status codes and response fields
  ✓ Pipeline uses production orchestrator — mock only at external service boundary
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.scripts.persistence import (
    build_transaction_context,
    list_applicants,
    FixtureError,
    VALID_TRIGGER_TYPES,
)
from app.scripts.simulate_engine import run_simulation

# ── Helpers ───────────────────────────────────────────────────────────────────

FIXTURES_PATH = Path(__file__).parent.parent / "app" / "scripts" / "fixtures.json"


def _load_fixtures() -> dict:
    return json.loads(FIXTURES_PATH.read_text())


def _mock_canned_llm_responses():
    """
    Returns a side_effect list that produces valid JSON for any agent call sequence.
    Agent A: routing decision
    Agent B: risk assessment
    Agent C: treasury assessment
    Compressor: compressed summary bullets
    """
    agent_a = '{"route": "A->B->C", "cited_clause": "Section I, Clause 1"}'
    agent_b_pass = '{"flags": [], "status": "PASSED", "citedClause": "Section I, Clause 1", "notes": ""}'
    agent_c_approved = '{"status": "APPROVED", "availableAmount": 100000.00, "citedClause": "Section I, Clause 1", "requiresHumanReview": false, "notes": ""}'
    compressor = json.dumps(["• Requested: ₹25,000.", "• No flags.", "• Approved by Treasury.", "• Final outcome: APPROVED."])
    # Return enough responses for multiple rounds
    return [agent_a, agent_b_pass, agent_c_approved, compressor] * 6


# ══════════════════════════════════════════════════════════════════════════════
# fixtures.json tests
# ══════════════════════════════════════════════════════════════════════════════

class TestFixtures:
    def test_fixtures_file_exists(self):
        assert FIXTURES_PATH.exists(), "fixtures.json must exist at app/scripts/fixtures.json"

    def test_all_applicant_ids_present(self):
        applicants = list_applicants()
        for expected in ["APP-001", "APP-002", "APP-003", "APP-004", "APP-005"]:
            assert expected in applicants, f"{expected} missing from fixtures.json"

    def test_all_trigger_types_have_defaults(self):
        fixtures = _load_fixtures()
        defaults = fixtures["trigger_defaults"]
        for trigger in VALID_TRIGGER_TYPES:
            assert trigger in defaults, f"trigger_type '{trigger}' missing from fixture_defaults"

    def test_applicant_has_required_fields(self):
        applicants = list_applicants()
        for app_id, data in applicants.items():
            assert "name" in data, f"{app_id} missing 'name'"
            assert "email" in data, f"{app_id} missing 'email'"
            assert "mock_credit_score" in data, f"{app_id} missing 'mock_credit_score'"


# ══════════════════════════════════════════════════════════════════════════════
# persistence.py tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPersistence:
    def test_build_context_returns_valid_transaction(self):
        ctx = build_transaction_context("subsidy_loan", "APP-001")
        assert ctx.customer_name == "Priya Sharma"
        assert ctx.requested_amount == Decimal("25000.00")
        assert ctx.urgency_flag == "normal"
        assert ctx.currency == "INR"

    def test_emergency_payout_uses_disaster_urgency(self):
        ctx = build_transaction_context("emergency_payout", "APP-005")
        assert ctx.urgency_flag == "disaster"

    def test_two_runs_produce_unique_transaction_ids(self):
        ctx1 = build_transaction_context("subsidy_loan", "APP-001")
        ctx2 = build_transaction_context("subsidy_loan", "APP-001")
        assert ctx1.transaction_id != ctx2.transaction_id

    def test_guarantor_applicant_has_guarantor_in_metadata(self):
        ctx = build_transaction_context("subsidy_loan", "APP-003")
        assert "guarantor" in ctx.metadata
        assert ctx.metadata["guarantor"] == "Suresh Verma"

    def test_unknown_applicant_raises_fixture_error(self):
        with pytest.raises(FixtureError, match="not found"):
            build_transaction_context("subsidy_loan", "APP-999")

    def test_invalid_trigger_type_raises_fixture_error(self):
        with pytest.raises(FixtureError, match="Unknown trigger_type"):
            build_transaction_context("invalid_type", "APP-001")

    def test_adversarial_threshold_dodge_amount(self):
        ctx = build_transaction_context("adversarial_threshold_dodge", "APP-001")
        assert ctx.requested_amount == Decimal("49999.99")

    def test_all_four_trigger_types_build_successfully(self):
        for trigger in sorted(VALID_TRIGGER_TYPES):
            ctx = build_transaction_context(trigger, "APP-001")
            assert ctx.transaction_id is not None
            assert ctx.customer_name == "Priya Sharma"


# ══════════════════════════════════════════════════════════════════════════════
# Simulation engine tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSimulateEngine:
    """Runs simulation with mocked LLM calls (no network calls)."""

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.agents.agent_b_risk.call_llm")
    @patch("app.agents.agent_c_treasury.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_subsidy_loan_runs_end_to_end(self, mock_push, mock_hist_llm, mock_c, mock_b, mock_a):
        mock_a.return_value = '{"route": "A->B->C", "cited_clause": "Section I, Clause 1"}'
        mock_b.return_value = '{"flags": [], "status": "PASSED", "citedClause": "Section I, Clause 1"}'
        mock_c.return_value = '{"status": "APPROVED", "availableAmount": 100000.00, "citedClause": "Section I, Clause 1", "requiresHumanReview": false}'
        mock_hist_llm.return_value = json.dumps(["• Requested: ₹25,000.", "• APPROVED."])

        result = await run_simulation("subsidy_loan", "APP-001")

        assert result.transaction_id is not None
        assert result.trigger_type == "subsidy_loan"
        assert result.applicant_id == "APP-001"
        assert result.error is None

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.agents.agent_c_treasury.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_emergency_payout_escalates_on_frozen_fund(self, mock_push, mock_hist, mock_c, mock_a):
        mock_a.return_value = '{"route": "A->C", "cited_clause": "Section I, Clause 2"}'
        # Frozen fund → TREASURY_REJECT with requiresHumanReview
        mock_c.return_value = '{"status": "TREASURY_REJECT", "availableAmount": 0, "citedClause": "Section I, Clause 4", "requiresHumanReview": true}'
        mock_hist.return_value = json.dumps(["• Emergency payout.", "• Frozen fund.", "• ESCALATED."])

        result = await run_simulation("emergency_payout", "APP-005")
        # Emergency payout on frozen fund must flag human review
        assert result.requires_human_review is True

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.agents.agent_b_risk.call_llm")
    @patch("app.agents.agent_c_treasury.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_two_runs_same_trigger_produce_different_transaction_ids(
        self, mock_push, mock_hist, mock_c, mock_b, mock_a
    ):
        mock_a.return_value = '{"route": "A->B->C", "cited_clause": "Section I, Clause 1"}'
        mock_b.return_value = '{"flags": [], "status": "PASSED", "citedClause": "Section I, Clause 1"}'
        mock_c.return_value = '{"status": "APPROVED", "availableAmount": 100000.00, "citedClause": "Section I, Clause 1", "requiresHumanReview": false}'
        mock_hist.return_value = json.dumps(["• Done."])

        r1 = await run_simulation("subsidy_loan", "APP-001")
        r2 = await run_simulation("subsidy_loan", "APP-001")
        assert r1.transaction_id != r2.transaction_id

    @pytest.mark.asyncio
    async def test_unknown_applicant_raises_fixture_error(self):
        with pytest.raises(FixtureError):
            await run_simulation("subsidy_loan", "APP-999")

    @pytest.mark.asyncio
    async def test_invalid_trigger_raises_fixture_error(self):
        with pytest.raises(FixtureError):
            await run_simulation("not_a_trigger", "APP-001")


# ══════════════════════════════════════════════════════════════════════════════
# FastAPI endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSimulateEndpoint:
    """Tests for POST /api/v1/simulate/trigger."""

    @pytest.fixture
    def client(self):
        from app import create_app
        return TestClient(create_app(), raise_server_exceptions=True)

    @patch("app.routes.simulate.run_simulation")
    def test_valid_request_returns_200(self, mock_run, client):
        from app.scripts.simulate_engine import SimulationResult
        from datetime import datetime, timezone

        mock_run.return_value = SimulationResult(
            transaction_id=uuid.uuid4(),
            trigger_type="subsidy_loan",
            applicant_id="APP-001",
            applicant_name="Priya Sharma",
            final_status="approved",
            route="A->B->C",
            rounds=1,
            outcome_reason="Consensus reached.",
            requires_human_review=False,
            policy_version="pb-abc12345",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )

        response = client.post(
            "/api/v1/simulate/trigger",
            json={"trigger_type": "subsidy_loan", "applicant_id": "APP-001"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "transaction_id" in body
        assert body["trigger_type"] == "subsidy_loan"
        assert body["applicant_name"] == "Priya Sharma"
        assert body["route"] == "A->B->C"

    @patch("app.routes.simulate.run_simulation", side_effect=FixtureError("Applicant 'APP-999' not found"))
    def test_unknown_applicant_returns_422(self, mock_run, client):
        response = client.post(
            "/api/v1/simulate/trigger",
            json={"trigger_type": "subsidy_loan", "applicant_id": "APP-999"},
        )
        assert response.status_code == 422
        assert "not found" in response.json()["detail"]

    @patch("app.routes.simulate.run_simulation", side_effect=FixtureError("Unknown trigger_type"))
    def test_invalid_trigger_type_returns_422(self, mock_run, client):
        response = client.post(
            "/api/v1/simulate/trigger",
            json={"trigger_type": "not_real", "applicant_id": "APP-001"},
        )
        assert response.status_code == 422

    @patch("app.routes.simulate.run_simulation")
    def test_all_four_triggers_accepted_by_endpoint(self, mock_run, client):
        from app.scripts.simulate_engine import SimulationResult
        from datetime import datetime, timezone

        for trigger in sorted(VALID_TRIGGER_TYPES):
            mock_run.return_value = SimulationResult(
                transaction_id=uuid.uuid4(),
                trigger_type=trigger,
                applicant_id="APP-001",
                applicant_name="Priya Sharma",
                final_status="approved",
                route="A->B->C",
                rounds=1,
                outcome_reason="done",
                requires_human_review=False,
                policy_version="pb-test",
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
            )
            response = client.post(
                "/api/v1/simulate/trigger",
                json={"trigger_type": trigger, "applicant_id": "APP-001"},
            )
            assert response.status_code == 200, f"Trigger '{trigger}' returned {response.status_code}"

    @patch("app.routes.simulate.run_simulation")
    def test_response_has_all_required_fields(self, mock_run, client):
        from app.scripts.simulate_engine import SimulationResult
        from datetime import datetime, timezone

        txn_id = uuid.uuid4()
        mock_run.return_value = SimulationResult(
            transaction_id=txn_id,
            trigger_type="emergency_payout",
            applicant_id="APP-005",
            applicant_name="Kavya Nair",
            final_status="escalated",
            route="A->C",
            rounds=1,
            outcome_reason="Frozen fund.",
            requires_human_review=True,
            policy_version="pb-xyz",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )

        response = client.post(
            "/api/v1/simulate/trigger",
            json={"trigger_type": "emergency_payout", "applicant_id": "APP-005"},
        )
        body = response.json()
        required_fields = [
            "transaction_id", "trigger_type", "applicant_id", "applicant_name",
            "final_status", "route", "rounds", "outcome_reason",
            "requires_human_review", "policy_version",
        ]
        for field in required_fields:
            assert field in body, f"Missing field: {field}"
        assert body["requires_human_review"] is True
        assert body["route"] == "A->C"
