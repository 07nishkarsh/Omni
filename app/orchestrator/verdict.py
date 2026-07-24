from app.models.transaction import TransactionContext, TransactionStatus

def generate_verdict_text(ctx: TransactionContext, clauses: str = "") -> str:
    """
    Pure deterministic string builder — no LLM call.
    AGENT_AUTOMATED + approved:
      "The request meets the policies as outlined in {clauses}."
    AGENT_AUTOMATED + rejected:
      "The request does not align with the policies as outlined in {clauses}."
    MANAGER_DECISION + approved:
      "Manager approved the request." (+ f" Note: {decision_reason}" if present)
    MANAGER_DECISION + rejected:
      "Manager rejected the request." (+ f" Reason: {decision_reason}" if present)
    Raise ValueError if decision_type is MANAGER_DECISION and rejected but
    decision_reason is missing — a rejection must always have a reason on file.
    """
    is_approved = ctx.status == TransactionStatus.APPROVED

    if ctx.decision_type == "MANAGER_DECISION":
        if not is_approved and not ctx.decision_reason:
            raise ValueError("A manager rejection must have a decision_reason.")
        
        base_text = "Manager approved the request." if is_approved else "Manager rejected the request."
        
        if ctx.decision_reason:
            prefix = "Note:" if is_approved else "Reason:"
            return f"{base_text} {prefix} {ctx.decision_reason}"
        return base_text
        
    elif ctx.decision_type == "AGENT_AUTOMATED":
        if is_approved:
            return f"The request meets the policies as outlined in {clauses}."
        else:
            return f"The request does not align with the policies as outlined in {clauses}."
    
    # Fallback
    return f"Verdict: {ctx.status.upper()}"
