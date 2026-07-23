import os
import pytest
from uuid import uuid4
from decimal import Decimal
from app.models.transaction import TransactionContext, TransactionType
from app.models.proposal import Proposal, ProposalStatus
from app.orchestrator.validator import validate_proposal_history, ValidationError
from app.orchestrator.negotiation import NegotiationEngine

@pytest.mark.asyncio
async def test_high_proportionality_forces_human_review():
    """
    Test that even if Agents A, B, and C pass a transaction,
    the validator catches a High proportionality ratio and 
    forces human review.
    """
    os.environ["USE_MOCK_LLM"] = "true"
    engine = NegotiationEngine()
    
    ctx = TransactionContext(
        transaction_id=uuid4(),
        transaction_type=TransactionType.LOAN_APPLICATION,
        customer_name="Vikram Singh",
        customer_email="vikram@example.com",
        requested_amount=Decimal("40000.00"),
        annual_declared_income=Decimal("15000.00"), # Ratio = 2.66 (High)
        mock_credit_score=810
    )
    
    result = await engine.run(ctx)
    
    assert result.accepted is False
    assert result.final_proposal.originated_by == "Validator"
    assert result.final_proposal.status == ProposalStatus.REJECTED
    assert result.final_proposal.metadata.get("requires_human_review") == "true"
    assert "Income Proportionality Review Required" in result.reason
    assert "High" in result.reason
