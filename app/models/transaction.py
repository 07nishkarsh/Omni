"""
TransactionContext schema.

⚠️  DISCLAIMER: This schema represents a *simulated* banking transaction for
workflow-orchestration purposes only.  No real account numbers, balances, or
credit-bureau data are used or processed anywhere in this codebase.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4
from datetime import datetime, timezone

from pydantic import BaseModel, Field, ConfigDict
from typing import Any


class TransactionType(StrEnum):
    LOAN_APPLICATION = "loan_application"
    CREDIT_LIMIT_INCREASE = "credit_limit_increase"
    DISPUTE = "dispute"
    WIRE_TRANSFER = "wire_transfer"
    ACCOUNT_CLOSURE = "account_closure"


class TransactionStatus(StrEnum):
    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    NEGOTIATING = "negotiating"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class TransactionContext(BaseModel):
    """
    Immutable snapshot of a banking transaction passed through the orchestrator.

    All monetary values are represented as Decimal to avoid floating-point
    rounding issues.  Account identifiers are UUIDs — no real PII is stored.
    """

    model_config = ConfigDict(frozen=True)

    transaction_id: UUID = Field(default_factory=uuid4)
    transaction_type: TransactionType
    status: TransactionStatus = TransactionStatus.PENDING

    # ── Customer (mock identifiers only) ────────────────────────────────────
    customer_id: UUID = Field(default_factory=uuid4, description="Mock customer UUID")
    customer_name: str = Field(..., min_length=1, max_length=200)
    customer_email: str = Field(..., description="Mock email for notification routing")

    # ── Financial figures (all mocked / simulated) ───────────────────────────
    requested_amount: Decimal = Field(..., gt=Decimal(0), description="Simulated amount")
    currency: str = Field(default="USD", max_length=3)
    account_id: UUID = Field(default_factory=uuid4, description="Mock account UUID")

    # ── Risk / scoring (all synthetic) ──────────────────────────────────────
    mock_credit_score: int = Field(
        default=700,
        ge=300,
        le=850,
        description="Synthetic credit score – not from any real credit bureau",
    )
    annual_declared_income: Decimal = Field(
        default=Decimal("50000.00"),
        gt=Decimal(0),
        description="Synthetic annual income – not verified",
    )

    # ── Routing ──────────────────────────────────────────────────────────────
    urgency_flag: str = Field(
        default="normal",
        description="'normal', 'disaster', or 'emergency' — drives bypass routing in Agent A",
    )
    requested_subsidy_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Requested subsidy percentage (simulated)",
    )

    # ── Metadata ─────────────────────────────────────────────────────────────
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: str = Field(default="", max_length=2000)
    metadata: dict[str, str] = Field(default_factory=dict)
    
    # ── Decision Tracking ────────────────────────────────────────────────────
    decision_type: str = Field(default="AGENT_AUTOMATED", description="'AGENT_AUTOMATED' or 'MANAGER_DECISION'")
    decision_maker: str | None = Field(default=None, description="Who made the decision (e.g. 'Manager')")
    decision_reason: str | None = Field(default=None, description="Reason for decision, required for rejected manager decisions")


class AgentRoutingDecision(BaseModel):
    """Structured output returned by Agent A after evaluating the Policy Book."""
    route: str = Field(description="Chosen route: 'A->B->C' or 'A->C'")
    cited_clause: str = Field(description="Exact policy clause number that justified the route")
    payload: dict[str, Any] = Field(default_factory=dict, description="Additional data passed downstream")
