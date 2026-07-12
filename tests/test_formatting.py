"""
tests/test_formatting.py

Unit tests for build_conflict_summary() in app/orchestrator/formatting.py.

Uses the real Proposal schema from app/models/proposal.py.
No network calls — all pure Python.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.proposal import Proposal
from app.orchestrator.formatting import build_conflict_summary


def _proposal(**kwargs) -> Proposal:
    """Helper: build a minimal valid Proposal, overriding any field via kwargs."""
    defaults = dict(
        transaction_id=uuid4(),
        originated_by="Agent A",
        proposed_amount=Decimal("5000"),
        metadata={},
    )
    defaults.update(kwargs)
    return Proposal(**defaults)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_requires_human_review_icon():
    """A proposal with metadata requires_human_review=true -> 🔴."""
    p = _proposal(
        originated_by="Agent A",
        proposed_amount=Decimal("5000"),
        rationale="Flagged high risk",
        metadata={"cited_clause": "Section I, Clause 2", "requires_human_review": "true"},
    )
    summary = build_conflict_summary([p])
    assert summary.startswith("🔴")
    assert "Agent A" in summary
    assert "Section I, Clause 2" in summary


def test_negotiation_change_icon():
    """When the amount changes between two proposals the second gets 🟡."""
    p1 = _proposal(
        originated_by="Agent A",
        proposed_amount=Decimal("5000"),
        rationale="Initial proposal",
        metadata={"cited_clause": "Section I, Clause 1"},
    )
    p2 = _proposal(
        originated_by="Agent B",
        proposed_amount=Decimal("4000"),
        rationale="Counter offer",
        metadata={"cited_clause": "Section II, Clause 1"},
    )
    summary = build_conflict_summary([p1, p2])
    lines = summary.split("\n")
    assert lines[0].startswith("ℹ️")
    assert lines[1].startswith("🟡")


def test_agent_c_treasury_icon():
    """A proposal from Agent C with amount > 0 -> 💰."""
    p = _proposal(
        originated_by="Agent C",
        proposed_amount=Decimal("5000"),
        rationale="Disbursed from treasury",
        metadata={"cited_clause": "Section I, Clause 3"},
    )
    summary = build_conflict_summary([p])
    assert summary.startswith("💰")
    assert "Agent C" in summary
