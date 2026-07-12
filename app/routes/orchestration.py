"""
Orchestration router.

Exposes the public API surface for submitting transactions and querying
their state as they move through the simulated banking workflow.

All external data (credit scores, account balances, etc.) is **mocked**.
No real financial systems are called at any point.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.models import TransactionContext, TransactionStatus
from app.orchestrator.state_machine import StateMachine
from app.services.transaction_store import transaction_store

router = APIRouter()


class SubmitTransactionRequest(BaseModel):
    """Request body for submitting a new transaction to the orchestrator."""

    transaction: TransactionContext


class TransactionResponse(BaseModel):
    transaction_id: UUID
    status: TransactionStatus
    message: str


@router.post(
    "/transactions",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new (simulated) transaction",
    description=(
        "Accepts a TransactionContext and begins the orchestration workflow. "
        "All financial data in the payload is treated as synthetic simulation data."
    ),
)
async def submit_transaction(body: SubmitTransactionRequest) -> TransactionResponse:
    ctx = body.transaction
    transaction_store.upsert(ctx)

    sm = StateMachine(ctx)
    proposal = await sm.run_pipeline()

    # If the proposal requires human review, mark the transaction ESCALATED.
    # The webhook / poller will resume it once the manager acts in Notion.
    requires_human = proposal.metadata.get("requires_human_review", "false") == "true"
    if requires_human:
        new_status = TransactionStatus.ESCALATED
    elif proposal.status in ("accepted",):
        new_status = TransactionStatus.APPROVED
    elif proposal.status in ("rejected",):
        new_status = TransactionStatus.REJECTED
    else:
        new_status = TransactionStatus.UNDER_REVIEW

    updated_ctx = transaction_store.set_status(ctx.transaction_id, new_status)

    return TransactionResponse(
        transaction_id=ctx.transaction_id,
        status=new_status,
        message=f"Transaction advanced to '{new_status}'.",
    )


@router.get(
    "/transactions/{transaction_id}",
    response_model=TransactionContext,
    summary="Get transaction state",
    description="Returns the current state of a submitted simulated transaction.",
)
async def get_transaction(transaction_id: UUID) -> TransactionContext:
    ctx = transaction_store.get(transaction_id)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {transaction_id} not found.",
        )
    return ctx


@router.get(
    "/transactions",
    response_model=list[TransactionContext],
    summary="List all transactions",
    description="Returns all simulated transactions held in the in-memory store.",
)
async def list_transactions() -> list[TransactionContext]:
    return transaction_store.all()
