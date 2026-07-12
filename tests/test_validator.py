"""
tests/test_validator.py

Tests for the strict proposal validator.
"""

from decimal import Decimal
from uuid import uuid4
import pytest

from app.models.proposal import Proposal
from app.orchestrator.validator import validate_proposal_history, ValidationError


def _make_prop(amount: str, cited_clause: str, requires_review: bool = False, agent: str = "Agent A") -> Proposal:
    return Proposal(
        transaction_id=uuid4(),
        originated_by=agent,
        proposed_amount=Decimal(amount),
        metadata={
            "cited_clause": cited_clause,
            "requires_human_review": str(requires_review).lower(),
        }
    )


def test_validator_clean_history_passes():
    """Valid clauses, under threshold, no human review required -> passes silently."""
    history = [
        _make_prop("10000", "Section I, Clause 1"),
        _make_prop("10000", "Section I, Clause 2", agent="Agent B"),
    ]
    # Should not raise
    validate_proposal_history(history)


def test_validator_invalid_clause_raises():
    """An invalid or missing clause citation raises ValidationError."""
    history = [
        _make_prop("10000", "Section X, Clause 99"),
    ]
    with pytest.raises(ValidationError, match="invalid clause"):
        validate_proposal_history(history)


def test_validator_missing_clause_raises():
    history = [
        _make_prop("10000", ""),
    ]
    with pytest.raises(ValidationError, match="missing a cited_clause"):
        validate_proposal_history(history)


def test_validator_exceeds_threshold_without_review_raises():
    """Amount > 50,000 without requires_human_review=true raises."""
    history = [
        _make_prop("60000", "Section I, Clause 1", requires_review=False),
    ]
    with pytest.raises(ValidationError, match="exceeds the 50,000 autonomous threshold"):
        validate_proposal_history(history)


def test_validator_exceeds_threshold_with_review_passes():
    """Amount > 50,000 WITH requires_human_review=true is valid."""
    history = [
        _make_prop("60000", "Section I, Clause 1", requires_review=True),
    ]
    # Should not raise
    validate_proposal_history(history)


def test_validator_adversarial_negotiation_caught():
    """
    Adversarial case: a negotiated outcome individually under threshold, 
    but an earlier step set requiresHumanReview=true.
    The validator MUST force human review (final proposal must have it).
    """
    history = [
        # Agent B flags high risk and forces review
        _make_prop("10000", "Section I, Clause 1", requires_review=True, agent="Agent B"),
        # Agent C (adversarial) tries to bypass the review by setting it to False
        _make_prop("10000", "Section I, Clause 1", requires_review=False, agent="Agent C"),
    ]
    with pytest.raises(ValidationError, match="attempted to bypass"):
        validate_proposal_history(history)


def test_validator_human_review_persisted_passes():
    """If earlier step requires review, and final step respects it, it passes."""
    history = [
        _make_prop("10000", "Section I, Clause 1", requires_review=True, agent="Agent B"),
        _make_prop("10000", "Section I, Clause 1", requires_review=True, agent="Agent C"),
    ]
    # Should not raise
    validate_proposal_history(history)
