"""
app/agents/agent_a_loans.py

Agent A — Loan Intake Router.

Evaluates a TransactionContext against the Policy Book and returns a structured
AgentRoutingDecision (route + cited clause).  All LLM calls go through
app.agents.llm_client.call_llm — never httpx directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.agents.llm_client import call_llm, LLMUnavailableError
from app.models.transaction import TransactionContext, AgentRoutingDecision

AGENT_PROMPT_PATH = Path(__file__).parent / "agent_a_loans.md"
POLICY_BOOK_PATH = Path(__file__).parent / "policy_book.md"


class AgentRoutingError(Exception):
    """Raised when Agent A cannot produce a valid routing decision."""


def process_loan_application(ctx: TransactionContext) -> AgentRoutingDecision:
    """
    Evaluate a loan application and return a structured routing decision.

    Raises:
        AgentRoutingError: on JSON/schema parse failures.
        LLMUnavailableError: propagated from call_llm when the API is down;
            the orchestrator must catch this and escalate to human review.
    """
    try:
        system_prompt = AGENT_PROMPT_PATH.read_text(encoding="utf-8")
        policy_book = POLICY_BOOK_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AgentRoutingError(f"Required prompt or policy file missing: {exc}")

    full_system = (
        f"{system_prompt}\n\n"
        f"--- POLICY BOOK ---\n{policy_book}\n-------------------\n"
    )

    user_message = (
        f"Please evaluate this loan application and choose the correct routing path:\n"
        f"- Applicant: {ctx.customer_name}\n"
        f"- Amount: {ctx.requested_amount} {ctx.currency}\n"
        f"- Subsidy %: {ctx.requested_subsidy_pct}\n"
        f"- Urgency Flag: {ctx.urgency_flag}\n"
        f"- Notes: {ctx.notes}\n"
    )

    # LLMUnavailableError is intentionally NOT caught here — let it bubble up
    # so the orchestrator can route the transaction to human review.
    raw = call_llm(full_system, user_message)

    # Strip markdown fences if the model added them despite instructions
    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.endswith("```"):
        raw = raw[:-3]

    try:
        decision = AgentRoutingDecision(**json.loads(raw))
    except json.JSONDecodeError as exc:
        raise AgentRoutingError(f"LLM did not return valid JSON: {exc}")
    except ValidationError as exc:
        raise AgentRoutingError(f"LLM JSON did not match AgentRoutingDecision schema: {exc}")

    return decision
