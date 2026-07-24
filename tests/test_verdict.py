import pytest
from uuid import uuid4
from decimal import Decimal
from app.models.transaction import TransactionContext, TransactionType, TransactionStatus
from app.orchestrator.verdict import generate_verdict_text

def create_ctx(status: TransactionStatus, decision_type: str, decision_reason: str = None) -> TransactionContext:
    return TransactionContext(
        transaction_id=uuid4(),
        transaction_type=TransactionType.LOAN_APPLICATION,
        customer_name="Test User",
        customer_email="test@example.com",
        requested_amount=Decimal("1000"),
        status=status,
        decision_type=decision_type,
        decision_maker="Manager" if decision_type == "MANAGER_DECISION" else None,
        decision_reason=decision_reason
    )

def test_automated_approved():
    ctx = create_ctx(TransactionStatus.APPROVED, "AGENT_AUTOMATED")
    verdict = generate_verdict_text(ctx, clauses="Section I, Clause 2")
    assert verdict == "The request meets the policies as outlined in Section I, Clause 2."

def test_automated_rejected():
    ctx = create_ctx(TransactionStatus.REJECTED, "AGENT_AUTOMATED")
    verdict = generate_verdict_text(ctx, clauses="Section II, Clause 1")
    assert verdict == "The request does not align with the policies as outlined in Section II, Clause 1."

def test_manager_approved_with_note():
    ctx = create_ctx(TransactionStatus.APPROVED, "MANAGER_DECISION", decision_reason="Looks good.")
    verdict = generate_verdict_text(ctx)
    assert verdict == "Manager approved the request. Note: Looks good."

def test_manager_approved_no_note():
    ctx = create_ctx(TransactionStatus.APPROVED, "MANAGER_DECISION")
    verdict = generate_verdict_text(ctx)
    assert verdict == "Manager approved the request."

def test_manager_rejected_with_note():
    ctx = create_ctx(TransactionStatus.REJECTED, "MANAGER_DECISION", decision_reason="Too risky.")
    verdict = generate_verdict_text(ctx)
    assert verdict == "Manager rejected the request. Reason: Too risky."

def test_manager_rejected_no_note():
    ctx = create_ctx(TransactionStatus.REJECTED, "MANAGER_DECISION")
    with pytest.raises(ValueError, match="A manager rejection must have a decision_reason."):
        generate_verdict_text(ctx)
