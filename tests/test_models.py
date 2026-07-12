"""
Tests for Pydantic models — TransactionContext, Proposal, Policy.
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import (
    TransactionContext,
    TransactionStatus,
    TransactionType,
    Proposal,
    ProposalStatus,
    Policy,
    PolicyRule,
    PolicyAction,
)


# ── TransactionContext ────────────────────────────────────────────────────────

def _make_ctx(**kwargs) -> TransactionContext:
    defaults = dict(
        transaction_type=TransactionType.LOAN_APPLICATION,
        customer_name="Alice Mock",
        customer_email="alice@mock.example.com",
        requested_amount=Decimal("10000.00"),
    )
    defaults.update(kwargs)
    return TransactionContext(**defaults)


def test_transaction_context_defaults():
    ctx = _make_ctx()
    assert ctx.status == TransactionStatus.PENDING
    assert ctx.currency == "USD"
    assert ctx.mock_credit_score == 700
    assert ctx.mock_annual_income == Decimal("50000.00")


def test_transaction_context_frozen():
    ctx = _make_ctx()
    with pytest.raises(Exception):  # Pydantic frozen model raises ValidationError or AttributeError
        ctx.status = TransactionStatus.APPROVED  # type: ignore[misc]


def test_transaction_context_invalid_amount():
    with pytest.raises(Exception):
        _make_ctx(requested_amount=Decimal("-1.00"))


# ── Proposal ──────────────────────────────────────────────────────────────────

def test_proposal_defaults():
    ctx = _make_ctx()
    p = Proposal(
        transaction_id=ctx.transaction_id,
        originated_by="underwriter_agent",
        proposed_amount=Decimal("9000.00"),
    )
    assert p.status == ProposalStatus.DRAFT
    assert p.is_expired is False


def test_proposal_rate_bounds():
    ctx = _make_ctx()
    with pytest.raises(Exception):
        Proposal(
            transaction_id=ctx.transaction_id,
            originated_by="test",
            proposed_amount=Decimal("1000.00"),
            proposed_rate=Decimal("101.00"),  # exceeds max 100
        )


# ── Policy ────────────────────────────────────────────────────────────────────

def test_policy_sorted_rules():
    rule_lo = PolicyRule(
        name="low_priority", field="mock_credit_score",
        operator="gte", threshold="600", priority=10,
    )
    rule_hi = PolicyRule(
        name="high_priority", field="mock_credit_score",
        operator="gte", threshold="700", priority=90,
    )
    policy = Policy(name="test_policy", rules=[rule_lo, rule_hi])
    assert policy.sorted_rules[0].name == "high_priority"
    assert policy.sorted_rules[1].name == "low_priority"


def test_default_policy_action():
    policy = Policy(name="empty_policy")
    assert policy.default_action == PolicyAction.APPROVE
    assert policy.sorted_rules == []
