"""
Policy schema.

Policies are rule sets that the Validator uses to decide whether a Proposal
or TransactionContext satisfies business constraints.

All thresholds are synthetic and exist only to demonstrate the orchestration
logic — they do not reflect any real banking regulation.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ConfigDict


class PolicyAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"
    REQUEST_MORE_INFO = "request_more_info"


class PolicyRule(BaseModel):
    """
    A single conditional rule within a Policy.

    Example::

        PolicyRule(
            name="min_credit_score",
            description="Reject if mock credit score is below threshold",
            field="mock_credit_score",
            operator="gte",
            threshold="620",
            action_on_fail=PolicyAction.REJECT,
        )
    """

    model_config = ConfigDict(frozen=True)

    rule_id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)

    # ── Condition ────────────────────────────────────────────────────────────
    field: str = Field(..., description="Dot-path to the field on TransactionContext or Proposal")
    operator: str = Field(
        ...,
        pattern="^(gte|lte|gt|lt|eq|neq|in|not_in)$",
        description="Comparison operator",
    )
    threshold: str = Field(..., description="Threshold value (always stored as string, cast at eval)")

    action_on_fail: PolicyAction = PolicyAction.REJECT
    priority: int = Field(default=10, ge=1, le=100, description="Higher = evaluated first")


class Policy(BaseModel):
    """
    A named collection of PolicyRules applied during transaction validation.

    Policies are evaluated in descending rule priority order.  The first
    failing rule determines the outcome action.
    """

    model_config = ConfigDict(frozen=True)

    policy_id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    version: str = Field(default="1.0.0")

    rules: list[PolicyRule] = Field(default_factory=list)
    default_action: PolicyAction = Field(
        default=PolicyAction.APPROVE,
        description="Action taken when all rules pass",
    )
    is_active: bool = True

    @property
    def sorted_rules(self) -> list[PolicyRule]:
        """Rules sorted by descending priority (highest evaluated first)."""
        return sorted(self.rules, key=lambda r: r.priority, reverse=True)


# ── Default built-in policies (synthetic thresholds only) ───────────────────

DEFAULT_LOAN_POLICY = Policy(
    name="default_loan_policy",
    description=(
        "Synthetic policy for simulated loan applications. "
        "Thresholds are illustrative only — not based on real regulations."
    ),
    rules=[
        PolicyRule(
            name="min_credit_score",
            description="Reject applications with mock credit score below 620",
            field="mock_credit_score",
            operator="gte",
            threshold="620",
            action_on_fail=PolicyAction.REJECT,
            priority=90,
        ),
        PolicyRule(
            name="max_loan_amount",
            description="Escalate if requested amount exceeds mock income × 5",
            field="requested_amount",
            operator="lte",
            threshold="250000",
            action_on_fail=PolicyAction.ESCALATE,
            priority=80,
        ),
        PolicyRule(
            name="min_income",
            description="Request more info if annual income is below $20,000",
            field="mock_annual_income",
            operator="gte",
            threshold="20000",
            action_on_fail=PolicyAction.REQUEST_MORE_INFO,
            priority=70,
        ),
    ],
    default_action=PolicyAction.APPROVE,
)
