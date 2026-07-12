"""
tests/test_agent_b.py

Unit tests for Agent B (Risk & Compliance Reviewer).
These tests mock the LLM call completely. The deterministic mock service
(simulate_tax_registry) provides the fixture data based on the applicant's UUID string.
Zero network calls made.
"""

import uuid
from decimal import Decimal
from unittest.mock import patch
import pytest

from app.models.transaction import TransactionContext, TransactionType
from app.agents.agent_b_risk import assess_risk, AgentBResponse


def _make_ctx(customer_id_str: str, metadata: dict = None) -> TransactionContext:
    if metadata is None:
        metadata = {}
    
    # We use the specific string UUID to trigger our deterministic mock service
    # Must be valid hex!
    hex_str = customer_id_str.encode('utf-8').hex().ljust(32, '0')[:32]
    _id = uuid.UUID(hex_str)
    
    return TransactionContext(
        transaction_type=TransactionType.LOAN_APPLICATION,
        customer_id=_id,
        customer_name="Test Applicant",
        customer_email="test@example.com",
        requested_amount=Decimal("10000"),
        metadata=metadata
    )


@patch("app.agents.agent_b_risk.call_llm")
def test_agent_b_clean_applicant_passes(mock_call_llm):
    """A clean applicant with no flags gets a PASSED status."""
    # "clean" string ensures simulate_tax_registry returns no flags
    ctx = _make_ctx("clean")
    
    # Mock LLM response matching the AgentBResponse schema
    mock_call_llm.return_value = '''
    {
      "flags": [],
      "status": "PASSED",
      "citedClause": "Section I, Clause 1",
      "notes": "No risk flags found."
    }
    '''
    
    response = assess_risk(ctx, "A->B->C")
    
    assert response.status == "PASSED"
    assert len(response.flags) == 0
    mock_call_llm.assert_called_once()
    
    # Verify the LLM prompt didn't contain unnecessary raw applicant data,
    # but did contain the applicant ID and Name
    prompt_used = mock_call_llm.call_args[0][1]
    assert str(ctx.customer_id) in prompt_used


@patch("app.agents.agent_b_risk.call_llm")
def test_agent_b_flagged_applicant_vetoes(mock_call_llm):
    """An applicant with a tax flag gets a RISK_VETO status."""
    # "flagged" triggers simulate_tax_registry to return flags
    ctx = _make_ctx("flagged")
    
    mock_call_llm.return_value = '''
    {
      "flags": ["2-year-old unresolved tax dispute", "Recent suspicious transaction pattern"],
      "status": "RISK_VETO",
      "citedClause": "Section I, Clause 2",
      "notes": "Tax flags present without guarantor."
    }
    '''
    
    response = assess_risk(ctx, "A->B->C")
    
    assert response.status == "RISK_VETO"
    assert len(response.flags) == 2
    assert "dispute" in response.flags[0]


@patch("app.agents.agent_b_risk.call_llm")
def test_agent_b_flagged_with_guarantor_passes(mock_call_llm):
    """An applicant with a tax flag BUT a guarantor gets PASSED."""
    ctx = _make_ctx("flagged", metadata={"guarantor": "Jane Doe"})
    
    mock_call_llm.return_value = '''
    {
      "flags": ["2-year-old unresolved tax dispute", "Recent suspicious transaction pattern"],
      "status": "PASSED",
      "citedClause": "Section I, Clause 3",
      "notes": "Tax flags offset by guarantor."
    }
    '''
    
    response = assess_risk(ctx, "A->B->C")
    
    assert response.status == "PASSED"
    assert len(response.flags) == 2
    mock_call_llm.assert_called_once()
    
    prompt_used = mock_call_llm.call_args[0][1]
    assert "Jane Doe" in prompt_used
