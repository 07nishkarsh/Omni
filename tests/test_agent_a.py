"""
tests/test_agent_a.py

Unit tests for Agent A routing logic.

All tests mock app.agents.llm_client.call_llm — no network calls are made.
Every test must complete in well under 1 second.

To run the real-network smoke test, use:
    pytest tests/test_live_smoke.py -m live_api
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.models.transaction import TransactionContext, TransactionType
from app.agents.agent_a_loans import process_loan_application, AgentRoutingError
from app.agents.llm_client import LLMUnavailableError

# ── Canned LLM responses ──────────────────────────────────────────────────────

_SUBSIDY_RESPONSE = json.dumps({
    "route": "A->B->C",
    "cited_clause": "Section I, Clause 1",
    "payload": {}
})

_DISASTER_RESPONSE = json.dumps({
    "route": "A->C",
    "cited_clause": "Section I, Clause 2",
    "payload": {}
})

_GARBAGE_RESPONSE = "this is not json!!!"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ctx(**kwargs) -> TransactionContext:
    defaults = dict(
        transaction_type=TransactionType.LOAN_APPLICATION,
        customer_name="Test Applicant",
        customer_email="test@mockbank.example.com",
        requested_amount="20000",
        currency="INR",
    )
    defaults.update(kwargs)
    return TransactionContext(**defaults)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_subsidy_loan_routing():
    """Normal subsidy loan must be routed A->B->C (Section I, Clause 1)."""
    ctx = _make_ctx(requested_subsidy_pct=5.0, urgency_flag="normal")

    with patch("app.agents.agent_a_loans.call_llm", return_value=_SUBSIDY_RESPONSE):
        decision = process_loan_application(ctx)

    assert decision.route == "A->B->C"
    assert "Section I, Clause 1" in decision.cited_clause


def test_disaster_flag_routing():
    """Disaster-flagged request must be routed A->C (Section I, Clause 2)."""
    ctx = _make_ctx(urgency_flag="disaster bypass")

    with patch("app.agents.agent_a_loans.call_llm", return_value=_DISASTER_RESPONSE):
        decision = process_loan_application(ctx)

    assert decision.route == "A->C"
    assert "Section I, Clause 2" in decision.cited_clause


def test_malformed_llm_output_raises_graceful_error():
    """Garbage JSON from LLM must raise AgentRoutingError, not crash."""
    ctx = _make_ctx()

    with patch("app.agents.agent_a_loans.call_llm", return_value=_GARBAGE_RESPONSE):
        with pytest.raises(AgentRoutingError, match="LLM did not return valid JSON"):
            process_loan_application(ctx)


def test_llm_unavailable_propagates():
    """LLMUnavailableError from call_llm must propagate unmodified so the
    orchestrator can route the transaction to human review."""
    ctx = _make_ctx()

    with patch(
        "app.agents.agent_a_loans.call_llm",
        side_effect=LLMUnavailableError("API down"),
    ):
        with pytest.raises(LLMUnavailableError, match="API down"):
            process_loan_application(ctx)
