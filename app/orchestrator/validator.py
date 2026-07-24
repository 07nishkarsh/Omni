"""
app/orchestrator/validator.py

Pure Python validator. No LLM calls.
Validates agent proposals before they are executed.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Sequence

from app.models.proposal import Proposal
from app.models.transaction import TransactionContext
from app.orchestrator.judgment import calculate_income_proportionality


class ValidationError(Exception):
    """Raised when a proposal violates policy rules."""


def _get_valid_clauses() -> set[str]:
    """Parse policy_book.md and extract valid citations like 'Section I, Clause 2'."""
    policy_path = Path(__file__).parent.parent / "agents" / "policy_book.md"
    try:
        content = policy_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()

    valid_clauses = set()
    current_section = None
    
    for line in content.splitlines():
        if line.startswith("# Section "):
            # e.g. "# Section I: Standard Loan Routing" -> "Section I"
            current_section = line.split(":")[0].strip("# ")
        elif line.startswith("- **Clause "):
            # e.g. "- **Clause 1**: ..." -> "Clause 1"
            clause = line.split("**:")[0].strip("- *")
            if current_section:
                valid_clauses.add(f"{current_section}, {clause}")
                
    return valid_clauses


def validate_proposal_history(proposals: Sequence[Proposal], ctx: TransactionContext) -> None:
    """
    Validates a transaction history of agent proposals.
    
    Rules:
      1. Every proposal must cite a valid clause from the Policy Book.
      2. No proposal may approve an amount > 50,000 autonomously.
      3. If any proposal in the history required human review, the final
         execution path (the last proposal) must also route to human review.
      4. Income proportionality (amount/income) > 0.5 must be forced to human review.
    """
    if not proposals:
        return

    valid_clauses = _get_valid_clauses()
    any_human_review_required = False

    for i, prop in enumerate(proposals):
        # Check 1: Valid clause
        cited = prop.metadata.get("cited_clause", "").strip()
        if not cited:
            raise ValidationError(f"Proposal {prop.proposal_id} by {prop.originated_by} is missing a cited_clause.")
        
        if cited not in valid_clauses:
            raise ValidationError(
                f"Proposal {prop.proposal_id} cited an invalid clause: '{cited}'. "
                f"Valid clauses are: {sorted(list(valid_clauses))}"
            )

        # Check 2: Threshold limits
        # Autonomous threshold is 50,000. If amount > 50,000, requires_human_review MUST be true.
        is_human_review = prop.metadata.get("requires_human_review", "").lower() == "true"
        
        if prop.proposed_amount > Decimal("50000") and not is_human_review:
            raise ValidationError(
                f"Proposal {prop.proposal_id} by {prop.originated_by} proposes {prop.proposed_amount}, "
                f"which exceeds the 50,000 autonomous threshold, but requires_human_review is false."
            )
            
        if is_human_review:
            any_human_review_required = True

    # Check 3: Human review persistence
    # If any earlier step flagged human review, the final decision MUST also be human review.
    final_prop = proposals[-1]
    final_is_human_review = final_prop.metadata.get("requires_human_review", "").lower() == "true"
    
    if any_human_review_required and not final_is_human_review:
        raise ValidationError(
            f"A previous proposal required human review, but the final proposal "
            f"by {final_prop.originated_by} attempted to bypass it."
        )

    # Check 4: Income Proportionality Check
    prop_result = calculate_income_proportionality(
        float(final_prop.proposed_amount), 
        float(ctx.annual_declared_income)
    )
    
    from app.services.transaction_store import transaction_store
    if prop_result.requires_human_review and not final_is_human_review:
        transaction_store.add_progress(ctx.transaction_id, 5, "Income Proportionality Score", f"FAILED — {prop_result.note}")
        # Raise ValidationError but we'll attach the cited clause so negotiation.py can use it
        err = ValidationError(f"Income Proportionality Review Required: {prop_result.note}")
        err.cited_clause = prop_result.cited_clause
        raise err
    else:
        transaction_store.add_progress(ctx.transaction_id, 5, "Income Proportionality Score", f"PASSED — {prop_result.band} band")

    transaction_store.add_progress(ctx.transaction_id, 7, "Validator — final determination", "PASSED")
