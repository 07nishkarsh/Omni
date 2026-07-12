"""
tests/test_integration_negotiation.py

Integration tests for the Orchestrator negotiation loop, validating end-to-end
flows for Trigger 1 (Subsidy Loan) with both successful negotiation and 
max-round escalation.
"""

import uuid
from decimal import Decimal
from unittest.mock import patch
import pytest

from app.models.transaction import TransactionContext, TransactionType
from app.orchestrator.negotiation import NegotiationEngine

def _make_ctx(txn_id_str: str) -> TransactionContext:
    # Must be valid hex!
    hex_str = txn_id_str.encode('utf-8').hex().ljust(32, '0')[:32]
    _id = uuid.UUID(hex_str)
    
    return TransactionContext(
        transaction_id=_id,
        transaction_type=TransactionType.LOAN_APPLICATION,
        customer_name="Test Applicant",
        customer_email="test@example.com",
        requested_amount=Decimal("10000")
    )


@pytest.mark.asyncio
@patch("app.agents.agent_a_loans.call_llm")
@patch("app.agents.agent_b_risk.call_llm")
@patch("app.agents.agent_c_treasury.call_llm")
async def test_trigger_1_negotiation_consensus(mock_c, mock_b, mock_a):
    """
    Round 1: Agent C returns PARTIAL (disbursement split)
    Round 2: Agent C returns APPROVED
    """
    ctx = _make_ctx("insufficientflagged")  # "insufficient" forces 5000 balance in mock service, "flagged" forces flags
    # We add a guarantor to clear the Agent B veto
    ctx = ctx.model_copy(update={"notes": "Guarantor: Jane Doe"})

    # Agent A side-effects (Round 1, Round 2)
    mock_a.side_effect = [
        '{"route": "A->B->C", "cited_clause": "Section I, Clause 1"}',
        '{"route": "A->B->C", "cited_clause": "Section I, Clause 1"}'
    ]

    # Agent B side-effects (Round 1, Round 2)
    mock_b.side_effect = [
        '{"flags": ["flag 1"], "status": "PASSED", "citedClause": "Section I, Clause 1", "notes": "Guarantor clears veto"}',
        '{"flags": ["flag 1"], "status": "PASSED", "citedClause": "Section I, Clause 1", "notes": "Guarantor clears veto"}'
    ]

    # Agent C side-effects (Round 1: Partial, Round 2: Approved)
    mock_c.side_effect = [
        '{"status": "PARTIAL", "availableAmount": 5000.00, "citedClause": "Section I, Clause 1", "requiresHumanReview": false}',
        '{"status": "APPROVED", "availableAmount": 5000.00, "citedClause": "Section I, Clause 1", "requiresHumanReview": false}'
    ]

    engine = NegotiationEngine(max_rounds=4)
    result = await engine.run(ctx)

    assert result.accepted is True
    assert result.rounds == 2
    assert result.final_proposal.proposed_amount == Decimal("5000.00")
    assert result.final_proposal.status == "accepted"
    
    # Verify the transcript length (1 proposal per round)
    assert len(result.transcript) == 2
    assert result.transcript[0].status == "countered"
    assert result.transcript[1].status == "accepted"


@pytest.mark.asyncio
@patch("app.agents.agent_a_loans.call_llm")
@patch("app.agents.agent_b_risk.call_llm")
@patch("app.agents.agent_c_treasury.call_llm")
async def test_trigger_1_negotiation_max_rounds(mock_c, mock_b, mock_a):
    """
    Forces the round cap by having Agent C continuously return PARTIAL.
    Expect escalation to Manager Approval Desk.
    """
    ctx = _make_ctx("insufficientflagged")
    ctx = ctx.model_copy(update={"notes": "Guarantor: Jane Doe"})

    # We set max_rounds = 3 to save time in test.
    # 3 calls each for Agent A, B, C
    mock_a.side_effect = ['{"route": "A->B->C", "cited_clause": "Section I, Clause 1"}'] * 3
    mock_b.side_effect = ['{"flags": [], "status": "PASSED", "citedClause": "Section I, Clause 1"}'] * 3
    
    # Always PARTIAL
    mock_c.side_effect = ['{"status": "PARTIAL", "availableAmount": 5000.00, "citedClause": "Section I, Clause 1", "requiresHumanReview": false}'] * 3

    engine = NegotiationEngine(max_rounds=3)
    result = await engine.run(ctx)

    assert result.accepted is False
    assert result.rounds == 3
    
    # Round limit escalation should add a final rejected proposal to the transcript
    assert len(result.transcript) == 4
    assert result.final_proposal.status == "rejected"
    assert result.final_proposal.originated_by == "Orchestrator"
    assert result.final_proposal.metadata["requires_human_review"] == "true"
