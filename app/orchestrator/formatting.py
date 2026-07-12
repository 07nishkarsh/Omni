from app.models.proposal import Proposal

def build_conflict_summary(proposals: list[Proposal]) -> str:
    """
    Builds a structured, human-readable summary from a Proposal history.
    Derived purely from structured Proposal objects — no LLM call inside.

    Icon legend:
      🔴  requires_human_review is True
      💰  originated_by Agent C and proposed_amount > 0
      🟡  route or amount changed from the previous proposal (negotiated change)
      ℹ️   default
    """
    lines = []
    prev_amount = None

    for i, p in enumerate(proposals):
        agent = p.originated_by
        amount = p.proposed_amount
        description = p.rationale or "No rationale provided"
        cited_clause = p.metadata.get("cited_clause", "N/A")

        if p.metadata.get("requires_human_review", "").lower() == "true":
            icon = "🔴"
        elif agent.strip().lower() == "agent c" and amount > 0:
            icon = "💰"
        elif i > 0 and amount != prev_amount:
            icon = "🟡"
        else:
            icon = "ℹ️"

        lines.append(f"{icon} {agent}: {description}. ({cited_clause})")
        prev_amount = amount

    return "\n".join(lines)
