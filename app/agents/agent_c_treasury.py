"""
app/agents/agent_c_treasury.py

Agent C — Treasury & Disbursement Reviewer.

Receives the transaction and assesses fund availability from the Treasury & Scheme Ledger.
If a fund is Frozen, it immediately rejects (TREASURY_REJECT) via code, regardless of
the LLM's assessment, and flags for human review.
Otherwise, it queries the LLM via app.agents.llm_client.call_llm.

NOTE: All financial data is MOCKED — this agent does not connect to real systems.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.agents.llm_client import call_llm, LLMUnavailableError
from app.models.transaction import TransactionContext
from app.integrations.mock_services import simulate_treasury_ledger

AGENT_PROMPT_PATH = Path(__file__).parent / "agent_c_treasury.md"


class AgentCResponse(BaseModel):
    """Structured output from Agent C."""
    status: str = Field(description="'APPROVED', 'TREASURY_REJECT', or 'PARTIAL'")
    availableAmount: Decimal = Field(description="Amount available in the fund")
    citedClause: str = Field(description="Policy clause used to justify the status")
    requiresHumanReview: bool = Field(default=False)
    notes: str = Field(default="", description="Optional reasoning")


class AgentCError(Exception):
    """Raised when Agent C cannot produce a valid treasury assessment."""


def evaluate_treasury(ctx: TransactionContext, route: str) -> AgentCResponse:
    """
    Run Agent C treasury assessment.
    """
    try:
        system_prompt = AGENT_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AgentCError(f"Agent C prompt file missing: {exc}")

    # Read from the (simulated) Treasury & Scheme Ledger via Notion MCP.
    fund_data = simulate_treasury_ledger(ctx.transaction_id)
    fund_status = fund_data["fund_status"]
    available_balance = fund_data["available_balance"]
    
    # HARD-CODED RULE: If the fund is frozen, force reject and human review
    if fund_status.lower() == "frozen":
        return AgentCResponse(
            status="TREASURY_REJECT",
            availableAmount=available_balance,
            citedClause="Section I, Clause 2",  # Or whichever emergency clause applies
            requiresHumanReview=True,
            notes="Target fund is Frozen. Immediate rejection forced by code."
        )

    user_message = (
        f"Evaluate treasury availability for this transaction:\n"
        f"- Requested Amount: {ctx.requested_amount} {ctx.currency}\n"
        f"- Target Fund Name: {fund_data['fund_name']}\n"
        f"- Target Fund Status: {fund_status}\n"
        f"- Available Balance: {available_balance} {ctx.currency}\n"
        f"- Route: {route}\n"
    )

    raw = call_llm(system_prompt, user_message)

    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        return AgentCResponse(**json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AgentCError(f"Agent C produced invalid output: {exc}")
