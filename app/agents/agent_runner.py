"""
Agent Runner — loads system-prompt Markdown files and invokes the LLM.

In mock mode (``USE_MOCK_LLM=true``) the runner returns a deterministic
synthetic Proposal without making any real API calls.

In live mode it sends a structured prompt to the configured LLM endpoint
using ``httpx`` and parses the JSON response into a ``Proposal``.
"""

from __future__ import annotations

import json
from pathlib import Path
from decimal import Decimal

import httpx
import structlog

from app.config import get_settings
from app.models import TransactionContext, Proposal, ProposalStatus

log = structlog.get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent  # sibling .md files live here


class AgentRunnerError(Exception):
    """Raised when the agent runner cannot obtain a valid response."""


class AgentRunner:
    """
    Loads agent system prompts and drives LLM inference.

    Supported agents
    ----------------
    - ``underwriter``  (underwriter_agent.md)
    - ``negotiator``   (negotiator_agent.md)
    - ``compliance``   (compliance_agent.md)
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    # ── Public agent methods ──────────────────────────────────────────────────

    async def run_underwriter(self, ctx: TransactionContext) -> Proposal:
        """Invoke the underwriter agent for the given transaction context."""
        return await self._run_agent("underwriter_agent", ctx)

    async def run_negotiator(self, ctx: TransactionContext) -> Proposal:
        """Invoke the negotiator agent for the given transaction context."""
        return await self._run_agent("negotiator_agent", ctx)

    async def run_compliance(self, ctx: TransactionContext) -> Proposal:
        """Invoke the compliance agent for the given transaction context."""
        return await self._run_agent("compliance_agent", ctx)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _run_agent(self, agent_name: str, ctx: TransactionContext) -> Proposal:
        system_prompt = self._load_prompt(agent_name)
        user_message = self._build_user_message(ctx)

        if self._settings.use_mock_llm:
            log.info("agent_runner.mock_mode", agent=agent_name)
            return self._mock_response(ctx, agent_name)

        log.info("agent_runner.llm_call", agent=agent_name, model=self._settings.llm_model)
        raw = await self._call_llm(system_prompt, user_message)
        return self._parse_response(raw, ctx, agent_name)

    def _load_prompt(self, agent_name: str) -> str:
        prompt_path = _PROMPTS_DIR / f"{agent_name}.md"
        if not prompt_path.exists():
            raise AgentRunnerError(f"System prompt not found: {prompt_path}")
        return prompt_path.read_text(encoding="utf-8")

    @staticmethod
    def _build_user_message(ctx: TransactionContext) -> str:
        return (
            "Evaluate the following simulated transaction context and respond "
            "with a JSON Proposal object as specified in your system prompt.\n\n"
            f"```json\n{ctx.model_dump_json(indent=2)}\n```"
        )

    async def _call_llm(self, system_prompt: str, user_message: str) -> str:
        """POST to the configured LLM endpoint and return the raw text response."""
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": system_prompt + "\n\n" + user_message}]}
            ],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        url = (
            f"{self._settings.llm_base_url}/models/"
            f"{self._settings.llm_model}:generateContent"
            f"?key={self._settings.llm_api_key}"
        )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        data = response.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise AgentRunnerError(f"Unexpected LLM response structure: {data}") from exc

    @staticmethod
    def _parse_response(raw: str, ctx: TransactionContext, agent_name: str) -> Proposal:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentRunnerError(f"LLM returned invalid JSON: {raw[:200]}") from exc
        obj.setdefault("transaction_id", str(ctx.transaction_id))
        obj.setdefault("originated_by", agent_name)
        return Proposal.model_validate(obj)

    @staticmethod
    def _mock_response(ctx: TransactionContext, agent_name: str) -> Proposal:
        """
        Return a deterministic mock Proposal based on the mock credit score.

        Thresholds mirror the underwriter_agent.md decision table.
        """
        score = ctx.mock_credit_score
        if score >= 750:
            rate = Decimal("5.50")
            status = ProposalStatus.SUBMITTED
            rationale = (
                "Mock credit score is excellent. Approved at the best simulated rate. "
                "No conditions required for this synthetic workflow demonstration."
            )
        elif score >= 680:
            rate = Decimal("8.00")
            status = ProposalStatus.SUBMITTED
            rationale = (
                "Mock credit score is good. Approved at the standard simulated rate. "
                "This is synthetic data only — no real underwriting has occurred."
            )
        elif score >= 620:
            rate = Decimal("12.00")
            status = ProposalStatus.SUBMITTED
            rationale = (
                "Mock credit score is fair. Conditionally approved at a higher simulated rate. "
                "Conditions are illustrative only for this workflow demonstration."
            )
        else:
            rate = None
            status = ProposalStatus.REJECTED
            rationale = (
                "Mock credit score is below the synthetic minimum threshold. "
                "Rejected in this simulated workflow — no real decision has been made."
            )

        return Proposal(
            transaction_id=ctx.transaction_id,
            originated_by=agent_name,
            status=status,
            proposed_amount=ctx.requested_amount,
            proposed_rate=rate,
            proposed_term_months=36 if rate is not None else None,
            rationale=rationale,
            conditions=(
                ["Verify mock income documentation"]
                if 620 <= score < 680
                else []
            ),
        )
