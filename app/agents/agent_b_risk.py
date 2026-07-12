"""
app/agents/agent_b_risk.py

Agent B — Risk & Compliance Reviewer.

Receives the routing decision from Agent A and performs a synthetic risk
assessment by checking mock tax registry and credit bureau services.
All LLM calls go through app.agents.llm_client.call_llm.

NOTE: All risk data (credit scores, fund balances, etc.) is MOCKED — this
agent does not connect to any real financial or credit-bureau system.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.agents.llm_client import call_llm, LLMUnavailableError
from app.models.transaction import TransactionContext
from app.integrations.mock_services import simulate_tax_registry

AGENT_PROMPT_PATH = Path(__file__).parent / "agent_b_risk.md"


class AgentBResponse(BaseModel):
    """Structured output from Agent B."""
    flags: list[str] = Field(description="List of risk flags identified, if any")
    status: str = Field(description="'RISK_VETO' or 'PASSED'")
    citedClause: str = Field(description="Policy clause used to justify the status")
    notes: str = Field(default="", description="Optional reasoning")


class AgentBError(Exception):
    """Raised when Agent B cannot produce a valid risk assessment."""


def assess_risk(ctx: TransactionContext, route: str) -> AgentBResponse:
    """
    Run Agent B risk assessment.  LLMUnavailableError propagates to the
    orchestrator for human escalation.
    """
    try:
        system_prompt = AGENT_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AgentBError(f"Agent B prompt file missing: {exc}")

    tax_data = simulate_tax_registry(ctx.customer_id)

    # Note: we use metadata.get("guarantor") for the guarantor override logic.
    guarantor_info = ctx.metadata.get("guarantor", "None")

    user_message = (
        f"Assess the risk for this transaction:\n"
        f"- Applicant ID: {ctx.customer_id}\n"
        f"- Applicant Name: {ctx.customer_name}\n"
        f"- Route: {route}\n"
        f"- Mock Credit Score: {ctx.mock_credit_score}\n"
        f"- Mock Annual Income: {ctx.mock_annual_income}\n"
        f"- Guarantor: {guarantor_info}\n"
        f"- Tax Registry Flags: {tax_data['flags']}\n"
    )

    raw = call_llm(system_prompt, user_message)

    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        return AgentBResponse(**json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AgentBError(f"Agent B produced invalid output: {exc}")
