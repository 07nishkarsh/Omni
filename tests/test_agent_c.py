"""
tests/test_agent_c.py

Unit tests for Agent C (Treasury & Disbursement Reviewer).
These tests mock the LLM call completely. The deterministic mock service
(simulate_treasury_ledger) provides the fixture data based on the transaction UUID string.
Zero network calls made.
"""

import uuid
from decimal import Decimal
from unittest.mock import patch
import pytest

from app.models.transaction import TransactionContext, TransactionType
from app.agents.agent_c_treasury import evaluate_treasury, AgentCResponse


def _make_ctx(txn_id_str: str, requested_amount: Decimal = Decimal("10000")) -> TransactionContext:
    # Must be valid hex!
    hex_str = txn_id_str.encode('utf-8').hex().ljust(32, '0')[:32]
    _id = uuid.UUID(hex_str)
    
    return TransactionContext(
        transaction_id=_id,
        transaction_type=TransactionType.LOAN_APPLICATION,
        customer_name="Test Applicant",
        customer_email="test@example.com",
        requested_amount=requested_amount
    )


@patch("app.agents.agent_c_treasury.call_llm")
def test_agent_c_sufficient_funds_approves(mock_call_llm):
    """Sufficient funds -> APPROVED"""
    # "normal" ensures simulate_treasury_ledger returns 100,000 balance
    ctx = _make_ctx("normal", requested_amount=Decimal("10000"))
    
    # Mock LLM response matching the AgentCResponse schema
    mock_call_llm.return_value = '''
    {
      "status": "APPROVED",
      "availableAmount": 100000.00,
      "citedClause": "Section I, Clause 1",
      "requiresHumanReview": false,
      "notes": "Sufficient funds available."
    }
    '''
    
    response = evaluate_treasury(ctx, "A->B->C")
    
    assert response.status == "APPROVED"
    assert response.availableAmount == Decimal("100000.00")
    assert not response.requiresHumanReview
    mock_call_llm.assert_called_once()


@patch("app.agents.agent_c_treasury.call_llm")
def test_agent_c_insufficient_funds_partial(mock_call_llm):
    """Insufficient funds -> PARTIAL"""
    # "insufficient" triggers simulate_treasury_ledger to return 5000 balance
    ctx = _make_ctx("insufficient", requested_amount=Decimal("10000"))
    
    mock_call_llm.return_value = '''
    {
      "status": "PARTIAL",
      "availableAmount": 5000.00,
      "citedClause": "Section I, Clause 2",
      "requiresHumanReview": false,
      "notes": "Insufficient funds for full amount."
    }
    '''
    
    response = evaluate_treasury(ctx, "A->B->C")
    
    assert response.status == "PARTIAL"
    assert response.availableAmount == Decimal("5000.00")
    assert not response.requiresHumanReview
    mock_call_llm.assert_called_once()


@patch("app.agents.agent_c_treasury.call_llm")
def test_agent_c_frozen_fund_vetoes_despite_llm(mock_call_llm):
    """Frozen fund -> TREASURY_REJECT + requiresHumanReview, bypassing LLM"""
    ctx = _make_ctx("frozen")
    
    # Even if we set up the mock LLM to say APPROVED, the python code should intercept it.
    mock_call_llm.return_value = '''
    {
      "status": "APPROVED",
      "availableAmount": 100000.00,
      "citedClause": "Section I, Clause 1",
      "requiresHumanReview": false,
      "notes": "LLM ignored the freeze!"
    }
    '''
    
    response = evaluate_treasury(ctx, "A->B->C")
    
    # Assertions
    assert response.status == "TREASURY_REJECT"
    assert response.requiresHumanReview is True
    # The LLM should never even be called because the freeze check is pre-flight
    mock_call_llm.assert_not_called()
