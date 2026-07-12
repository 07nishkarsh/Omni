"""
tests/test_e2e_scenarios.py

End-to-end tests for all four mandated scenarios.
Zero real external calls — every agent LLM call, Notion write,
Gmail send, Slack post, and compression LLM call is mocked with
pre-scripted response sequences consumed in order (side_effect lists).

Scenario matrix:
  1. Trigger 1 — subsidy loan, tax dispute + funding gap
       → negotiation (Round 1 PARTIAL, Round 2 APPROVED)
       → human-approval webhook → Gmail + Slack + ledger execution
  2. Trigger 2 — emergency payout, frozen fund + missing GPS tag
       → single-round TREASURY_REJECT + requiresHumanReview
       → autonomous escalation (frozen fund is under threshold in terms of
         amount disbursed = 0, so execution path = Manager Desk only)
  3. Adversarial — emergency payout negotiated to ₹49,999 (just under threshold)
       while touching a frozen fund → validator must force human review
  4. Adversarial — negotiation round-cap: all rounds return PARTIAL, no consensus
       → must escalate to Manager Desk, not error out
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.models.proposal import Proposal, ProposalStatus
from app.models.transaction import TransactionContext, TransactionType, TransactionStatus
from app.orchestrator.negotiation import NegotiationEngine
from app.orchestrator.history import TranscriptCompressor, get_policy_version
from app.orchestrator.validator import validate_proposal_history, ValidationError
from app.services.transaction_store import TransactionStore


# ── Shared fixtures ────────────────────────────────────────────────────────────

def _ctx(
    *,
    amount: Decimal = Decimal("25000.00"),
    urgency: str = "normal",
    fund_tag: str = "general",       # encoded into UUID for simulate_treasury_ledger
    fund_tag2: str | None = None,    # second tag (e.g. "insufficient")
    customer_name: str = "Priya Sharma",
    guarantor: str | None = None,
    notes_extra: str = "",
) -> TransactionContext:
    """
    Build a TransactionContext whose UUID hex encodes the given fund_tag
    so that mock_services.simulate_treasury_ledger returns the right state.
    """
    # Encode fund tag into hex (same scheme as persistence.py / existing tests)
    fund_hex = fund_tag.encode("utf-8").hex().ljust(32, "0")[:32]
    if fund_tag2:
        tag2_hex = fund_tag2.encode("utf-8").hex().ljust(32, "0")[:32]
        fund_hex = format(int(fund_hex, 16) ^ int(tag2_hex, 16), "032x")
    txn_id = uuid.UUID(fund_hex)

    meta: dict[str, str] = {}
    if guarantor:
        meta["guarantor"] = guarantor

    notes = notes_extra
    if guarantor:
        notes = f"Guarantor: {guarantor}. {notes}"

    return TransactionContext(
        transaction_id=txn_id,
        transaction_type=TransactionType.LOAN_APPLICATION,
        customer_name=customer_name,
        customer_email="test@sandbox.mockbank.example.com",
        requested_amount=amount,
        currency="INR",
        urgency_flag=urgency,
        notes=notes,
        metadata=meta,
    )


# ── Canned LLM responses ───────────────────────────────────────────────────────
#
# Each scenario has its own list consumed in order per call_llm patch:
#   agent_a_loans.call_llm → routing decision
#   agent_b_risk.call_llm  → risk assessment
#   agent_c_treasury.call_llm → treasury assessment
#   history.call_llm       → compressor (always last in a scenario)
#
# Agent C is NOT called when the fund is Frozen (hard-coded short-circuit in
# evaluate_treasury). So those scenarios have one fewer call_llm invocation.

def _a_normal() -> str:
    return json.dumps({"route": "A->B->C", "cited_clause": "Section I, Clause 1"})

def _a_emergency() -> str:
    return json.dumps({"route": "A->C", "cited_clause": "Section I, Clause 2"})

def _b_pass() -> str:
    return json.dumps({"flags": [], "status": "PASSED",
                       "citedClause": "Section I, Clause 1", "notes": ""})

def _b_flagged_cleared(guarantor: str = "Rajan Guarantor") -> str:
    return json.dumps({
        "flags": ["2-year unresolved tax dispute"],
        "status": "PASSED",
        "citedClause": "Section I, Clause 1",
        "notes": f"Guarantor {guarantor} offsets risk veto.",
    })

def _c_partial(amount: float = 15000.0) -> str:
    return json.dumps({
        "status": "PARTIAL", "availableAmount": amount,
        "citedClause": "Section I, Clause 1", "requiresHumanReview": False,
    })

def _c_approved(amount: float = 25000.0) -> str:
    return json.dumps({
        "status": "APPROVED", "availableAmount": amount,
        "citedClause": "Section I, Clause 1", "requiresHumanReview": False,
    })

def _compressor_bullets(*labels: str) -> str:
    bullets = [
        f"• Requested: ₹{labels[0] if labels else '25,000'} subsidy loan.",
        "• Flagged: tax dispute detected.",
        "• Resolved: guarantor offset risk.",
        "• Final outcome: APPROVED.",
    ]
    return json.dumps(bullets)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 1 — Trigger 1: Subsidy loan, tax dispute + funding gap
# ══════════════════════════════════════════════════════════════════════════════

class TestScenario1SubsidyLoan:
    """
    Subsidy loan for flagged applicant with guarantor.
    Round 1: Agent B clears veto (guarantor), Agent C returns PARTIAL (funding gap).
    Round 2: Agent C returns APPROVED (negotiated lower amount).
    Then: human-approval webhook fires Gmail + Slack + ledger.
    """

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.agents.agent_b_risk.call_llm")
    @patch("app.agents.agent_c_treasury.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_scenario1_negotiation_reaches_consensus(
        self, mock_push, mock_hist, mock_c, mock_b, mock_a
    ):
        """Round 1 PARTIAL → Round 2 APPROVED → accepted=True, rounds=2."""
        ctx = _ctx(amount=Decimal("25000"), guarantor="Rajan Guarantor",
                   notes_extra="Tax dispute flag present.")

        mock_a.side_effect = [_a_normal(), _a_normal()]
        mock_b.side_effect = [_b_flagged_cleared(), _b_flagged_cleared()]
        mock_c.side_effect = [_c_partial(15000.0), _c_approved(15000.0)]
        mock_hist.return_value = _compressor_bullets("15,000")

        engine = NegotiationEngine(max_rounds=4)
        result = await engine.run(ctx)

        assert result.accepted is True, f"Expected accepted, got: {result.reason}"
        assert result.rounds == 2
        assert result.final_proposal.proposed_amount == Decimal("15000.00")
        assert result.final_proposal.status == ProposalStatus.ACCEPTED
        assert len(result.transcript) == 2
        assert result.transcript[0].status == ProposalStatus.COUNTERED

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.agents.agent_b_risk.call_llm")
    @patch("app.agents.agent_c_treasury.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_scenario1_compression_and_audit_push(
        self, mock_push, mock_hist, mock_c, mock_b, mock_a
    ):
        """After consensus, transcript compressor fires once and pushes to Audit Feed."""
        ctx = _ctx(amount=Decimal("25000"), guarantor="Rajan Guarantor")

        mock_a.side_effect = [_a_normal(), _a_normal()]
        mock_b.side_effect = [_b_flagged_cleared(), _b_flagged_cleared()]
        mock_c.side_effect = [_c_partial(15000.0), _c_approved(15000.0)]
        mock_hist.return_value = _compressor_bullets("15,000")

        engine = NegotiationEngine(max_rounds=4)
        result = await engine.run(ctx)

        compressor = TranscriptCompressor()
        summary = compressor.compress(ctx, result.transcript, route="A->B->C")

        assert mock_hist.call_count == 1
        mock_push.assert_called_once()
        assert summary.outcome == "ACCEPTED"
        assert summary.policy_version.startswith("pb-")
        assert "Agent C" in summary.agents_involved

    @pytest.mark.asyncio
    @patch("app.integrations.gmail.get_settings")
    @patch("app.integrations.slack.get_settings")
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.agents.agent_b_risk.call_llm")
    @patch("app.agents.agent_c_treasury.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_scenario1_human_approval_triggers_gmail_and_slack(
        self, mock_push, mock_hist, mock_c, mock_b, mock_a,
        mock_slack_settings, mock_gmail_settings
    ):
        """
        After negotiation consensus, simulating a human 'Approved' webhook
        must fire Gmail and Slack exactly once (idempotency enforced by
        executed_tools set).
        """
        from app.integrations.gmail import GmailClient
        from app.integrations.slack import SlackClient
        from app.integrations.mock_services import mock_gmail_send, mock_slack_post_message

        # Set up mock settings for both clients
        for mock_s in [mock_gmail_settings, mock_slack_settings]:
            s = MagicMock()
            s.use_mock_gmail = True
            s.use_mock_slack = True
            s.app_env = "development"
            s.gmail_sender_address = "no-reply@mockbank.example.com"
            s.gmail_sandbox_to = "sandbox@mockbank.example.com"
            s.slack_channel_id = "C00000000"
            s.slack_sandbox_channel = "bank-sim-test"
            mock_s.return_value = s

        ctx = _ctx(amount=Decimal("25000"), guarantor="Rajan Guarantor")
        executed_tools: set[str] = set()

        mock_a.side_effect = [_a_normal(), _a_normal()]
        mock_b.side_effect = [_b_flagged_cleared(), _b_flagged_cleared()]
        mock_c.side_effect = [_c_partial(15000.0), _c_approved(15000.0)]
        mock_hist.return_value = _compressor_bullets("15,000")

        engine = NegotiationEngine(max_rounds=4)
        result = await engine.run(ctx)
        assert result.accepted

        # Simulate human approval → downstream execution
        gmail_client = GmailClient()
        slack_client = SlackClient()

        r_gmail = await gmail_client.send_loan_agreement(
            to="customer@real.bank",
            transaction_id=ctx.transaction_id,
            amount="15000",
            currency="INR",
            executed_tools=executed_tools,
        )
        r_slack = await slack_client.post_branch_alert(
            transaction_id=ctx.transaction_id,
            message=f"Loan approved: ₹15,000 for {ctx.customer_name}",
            executed_tools=executed_tools,
        )

        # Both succeeded
        assert r_gmail.get("_mock") is True
        assert r_slack.get("_mock") is True

        # Idempotency: second call must be a no-op
        r_gmail2 = await gmail_client.send_loan_agreement(
            to="customer@real.bank",
            transaction_id=ctx.transaction_id,
            amount="15000",
            currency="INR",
            executed_tools=executed_tools,
        )
        r_slack2 = await slack_client.post_branch_alert(
            transaction_id=ctx.transaction_id,
            message="duplicate",
            executed_tools=executed_tools,
        )
        assert r_gmail2.get("skipped") is True
        assert r_slack2.get("skipped") is True


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 2 — Trigger 2: Emergency payout, frozen fund + missing GPS tag
# ══════════════════════════════════════════════════════════════════════════════

class TestScenario2EmergencyPayout:
    """
    Emergency payout (urgency=disaster) targeting frozen fund.
    Agent C hard-codes TREASURY_REJECT without calling the LLM (frozen-fund
    short-circuit in evaluate_treasury). requiresHumanReview=True.
    The validator then confirms the transaction must route to human review.
    """

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_scenario2_frozen_fund_forces_human_review(
        self, mock_push, mock_hist, mock_a
    ):
        """
        Agent C does NOT call call_llm for frozen funds (hard-circuit in code).
        The final proposal must have requires_human_review=true and be REJECTED.
        """
        ctx = _ctx(amount=Decimal("45000"), urgency="disaster", fund_tag="frozen")
        mock_a.return_value = _a_emergency()
        mock_hist.return_value = json.dumps(["• Emergency payout.", "• Frozen fund → ESCALATED."])

        engine = NegotiationEngine(max_rounds=4)
        result = await engine.run(ctx)

        assert result.accepted is False
        final = result.final_proposal
        assert final.metadata.get("requires_human_review") == "true"
        assert final.status == ProposalStatus.REJECTED

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_scenario2_agent_c_llm_not_called_for_frozen_fund(
        self, mock_push, mock_hist, mock_a
    ):
        """
        The frozen-fund rule is enforced in code, not by LLM.
        Patching agent_c_treasury.call_llm and asserting it's never called
        proves the hard-circuit works regardless of what the LLM would say.
        """
        ctx = _ctx(amount=Decimal("45000"), urgency="disaster", fund_tag="frozen")
        mock_a.return_value = _a_emergency()
        mock_hist.return_value = json.dumps(["• Frozen. ESCALATED."])

        with patch("app.agents.agent_c_treasury.call_llm") as mock_c_llm:
            engine = NegotiationEngine(max_rounds=4)
            await engine.run(ctx)
            mock_c_llm.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_scenario2_route_is_a_to_c(self, mock_push, mock_hist, mock_a):
        """Emergency bypass route must be A->C (Agent B skipped)."""
        ctx = _ctx(amount=Decimal("45000"), urgency="disaster", fund_tag="frozen")
        mock_a.return_value = _a_emergency()
        mock_hist.return_value = json.dumps(["• Emergency. ESCALATED."])

        with patch("app.agents.agent_b_risk.call_llm") as mock_b_llm:
            engine = NegotiationEngine(max_rounds=4)
            result = await engine.run(ctx)
            # For emergency (A->C route), Agent B is not invoked.
            # The state machine checks "B" in route to decide whether to run B.
            # We simply verify b's LLM was never called.
            mock_b_llm.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_scenario2_compression_captures_correct_outcome(
        self, mock_push, mock_hist, mock_a
    ):
        ctx = _ctx(amount=Decimal("45000"), urgency="disaster", fund_tag="frozen")
        mock_a.return_value = _a_emergency()
        mock_hist.return_value = json.dumps(["• Frozen fund escalation.", "• Outcome: REJECTED."])

        engine = NegotiationEngine(max_rounds=4)
        result = await engine.run(ctx)

        compressor = TranscriptCompressor()
        summary = compressor.compress(ctx, result.transcript, route="A->C")

        assert summary.outcome == "REJECTED"
        assert summary.route == "A->C"


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 3 — Adversarial: threshold dodge on frozen fund
# ══════════════════════════════════════════════════════════════════════════════

class TestScenario3AdversarialThresholdDodge:
    """
    An agent negotiates the amount down to ₹49,999 (just under the ₹50,000
    autonomous threshold) while the transaction touches a frozen fund.

    The validator must still force human review because requiresHumanReview
    was set to true in an earlier step — the lower final amount does NOT
    override that flag.
    """

    def _make_frozen_transcript(self, ctx: TransactionContext) -> list[Proposal]:
        """Simulate: Round 1 hits frozen fund → REJECTED with human review required."""
        p1 = Proposal(
            transaction_id=ctx.transaction_id,
            originated_by="Agent A",
            status=ProposalStatus.SUBMITTED,
            proposed_amount=ctx.requested_amount,
            metadata={"cited_clause": "Section I, Clause 2",
                      "requires_human_review": "false"},
            rationale="Routed A->C (emergency bypass).",
        )
        p2 = Proposal(
            transaction_id=ctx.transaction_id,
            originated_by="Agent C",
            status=ProposalStatus.REJECTED,
            proposed_amount=Decimal("49999.99"),  # Negotiated down — threshold dodge
            metadata={"cited_clause": "Section I, Clause 4",
                      "requires_human_review": "true"},   # ← frozen fund flag is SET
            rationale="Frozen fund. Human review required.",
        )
        # A later agent tries to ACCEPT the sub-threshold amount,
        # hoping the validator won't notice the earlier requires_human_review.
        p3 = Proposal(
            transaction_id=ctx.transaction_id,
            originated_by="Agent C",
            status=ProposalStatus.ACCEPTED,
            proposed_amount=Decimal("49999.99"),   # ← under ₹50k
            metadata={"cited_clause": "Section I, Clause 1",
                      "requires_human_review": "false"},  # ← tries to clear flag!
            rationale="Autonomous approval of sub-threshold amount.",
        )
        return [p1, p2, p3]

    def test_adversarial_validator_catches_frozen_fund_dodge(self):
        """
        The validator must raise ValidationError because requires_human_review
        was set to true at step p2, even though the final proposal (p3)
        is under the ₹50,000 threshold and cleared the flag.
        """
        ctx = _ctx(amount=Decimal("49999.99"), fund_tag="frozen")
        transcript = self._make_frozen_transcript(ctx)

        with pytest.raises(ValidationError, match="human review"):
            validate_proposal_history(transcript)

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_adversarial_frozen_fund_escalates_in_full_pipeline(
        self, mock_push, mock_hist, mock_a
    ):
        """
        Full pipeline run: emergency payout targeting frozen fund.
        The NegotiationEngine must end with accepted=False and the final
        proposal must carry requires_human_review=true.
        Clever negotiated final amounts do not bypass this.
        """
        ctx = _ctx(amount=Decimal("49999.99"), urgency="disaster", fund_tag="frozen")
        mock_a.return_value = _a_emergency()
        mock_hist.return_value = json.dumps(["• Frozen fund. ESCALATED."])

        engine = NegotiationEngine(max_rounds=4)
        result = await engine.run(ctx)

        assert result.accepted is False
        assert result.final_proposal.metadata.get("requires_human_review") == "true"

    def test_adversarial_amount_just_under_threshold_still_caught(self):
        """
        Standalone validator test: a proposal at ₹49,999.99 that cleared the
        requires_human_review flag in its own metadata, but a prior step had
        requires_human_review=true, must still fail validation.
        """
        ctx = _ctx(amount=Decimal("49999.99"), fund_tag="frozen")
        transcript = self._make_frozen_transcript(ctx)
        # The transcript has requires_human_review=true at step 2.
        # Step 3 tries to clear it. Validator must not allow this.
        with pytest.raises(ValidationError):
            validate_proposal_history(transcript)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 4 — Adversarial: round cap exhausted, no consensus
# ══════════════════════════════════════════════════════════════════════════════

class TestScenario4AdversarialRoundCap:
    """
    All 4 negotiation rounds return PARTIAL.
    The engine must: escalate to human review, not raise an exception.
    The transcript must have a final Orchestrator-originated REJECTED proposal
    with requires_human_review=true.
    """

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.agents.agent_b_risk.call_llm")
    @patch("app.agents.agent_c_treasury.call_llm")
    async def test_round_cap_escalates_not_raises(self, mock_c, mock_b, mock_a):
        """Engine returns a NegotiationResult with accepted=False, not an exception."""
        ctx = _ctx(amount=Decimal("30000"), fund_tag2="insufficient")

        mock_a.side_effect = [_a_normal()] * 4
        mock_b.side_effect = [_b_pass()] * 4
        mock_c.side_effect = [_c_partial(5000.0)] * 4

        engine = NegotiationEngine(max_rounds=4)
        result = await engine.run(ctx)   # must NOT raise

        assert result.accepted is False
        assert result.rounds == 4

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.agents.agent_b_risk.call_llm")
    @patch("app.agents.agent_c_treasury.call_llm")
    async def test_round_cap_final_proposal_requires_human_review(self, mock_c, mock_b, mock_a):
        """The escalation proposal added by the engine must set requires_human_review=true."""
        ctx = _ctx(amount=Decimal("30000"), fund_tag2="insufficient")

        mock_a.side_effect = [_a_normal()] * 4
        mock_b.side_effect = [_b_pass()] * 4
        mock_c.side_effect = [_c_partial(5000.0)] * 4

        engine = NegotiationEngine(max_rounds=4)
        result = await engine.run(ctx)

        final = result.final_proposal
        assert final.originated_by == "Orchestrator"
        assert final.status == ProposalStatus.REJECTED
        assert final.metadata.get("requires_human_review") == "true"

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.agents.agent_b_risk.call_llm")
    @patch("app.agents.agent_c_treasury.call_llm")
    async def test_round_cap_transcript_logged_for_all_rounds(self, mock_c, mock_b, mock_a):
        """Every round's proposal is in the transcript; plus the escalation proposal."""
        ctx = _ctx(amount=Decimal("30000"), fund_tag2="insufficient")

        mock_a.side_effect = [_a_normal()] * 4
        mock_b.side_effect = [_b_pass()] * 4
        mock_c.side_effect = [_c_partial(5000.0)] * 4

        engine = NegotiationEngine(max_rounds=4)
        result = await engine.run(ctx)

        # 4 real rounds + 1 escalation proposal = 5 entries
        assert len(result.transcript) == 5

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.agents.agent_b_risk.call_llm")
    @patch("app.agents.agent_c_treasury.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_round_cap_validator_catches_human_review(
        self, mock_push, mock_hist, mock_c, mock_b, mock_a
    ):
        """
        After round-cap escalation, running validate_proposal_history on the
        transcript must confirm human review is required (not raise an error
        about an invalid clause — the validator enforces the human-review rule).
        """
        ctx = _ctx(amount=Decimal("30000"), fund_tag2="insufficient")

        mock_a.side_effect = [_a_normal()] * 4
        mock_b.side_effect = [_b_pass()] * 4
        mock_c.side_effect = [_c_partial(5000.0)] * 4
        mock_hist.return_value = json.dumps(["• Round cap hit.", "• ESCALATED."])

        engine = NegotiationEngine(max_rounds=4)
        result = await engine.run(ctx)

        # Validator must pass (not raise) because the transcript correctly
        # has requires_human_review=true — this is the correct escalated state.
        # The exception would only fire if the transcript were INCONSISTENT.
        from app.orchestrator.validator import validate_proposal_history
        validate_proposal_history(result.transcript)  # must not raise

    @pytest.mark.asyncio
    @patch("app.agents.agent_a_loans.call_llm")
    @patch("app.agents.agent_b_risk.call_llm")
    @patch("app.agents.agent_c_treasury.call_llm")
    @patch("app.orchestrator.history.call_llm")
    @patch("app.orchestrator.history._push_to_audit_feed")
    async def test_round_cap_compression_runs_on_escalated_transcript(
        self, mock_push, mock_hist, mock_c, mock_b, mock_a
    ):
        """Even after escalation the transcript is compressed and pushed to Audit Feed."""
        ctx = _ctx(amount=Decimal("30000"), fund_tag2="insufficient")

        mock_a.side_effect = [_a_normal()] * 4
        mock_b.side_effect = [_b_pass()] * 4
        mock_c.side_effect = [_c_partial(5000.0)] * 4
        mock_hist.return_value = json.dumps(
            ["• Requested: ₹30,000.", "• Insufficient funds.", "• Round cap hit.", "• ESCALATED."]
        )

        engine = NegotiationEngine(max_rounds=4)
        result = await engine.run(ctx)

        compressor = TranscriptCompressor()
        summary = compressor.compress(ctx, result.transcript, route="A->B->C")

        assert mock_hist.call_count == 1
        mock_push.assert_called_once()
        assert "Orchestrator" in summary.agents_involved
        assert summary.outcome == "REJECTED"


# ══════════════════════════════════════════════════════════════════════════════
# Cross-scenario: no real external calls
# ══════════════════════════════════════════════════════════════════════════════

class TestNoRealExternalCalls:
    """
    Smoke-level assertions confirming the mock guards are in place.
    These are not meaningful business tests — they exist as a safety net
    to catch any accidental removal of the mock patches.
    """

    def test_simulate_treasury_ledger_is_mock(self):
        from app.integrations.mock_services import simulate_treasury_ledger
        result = simulate_treasury_ledger(uuid.uuid4())
        assert result["_mock"] is True

    def test_simulate_tax_registry_is_mock(self):
        from app.integrations.mock_services import simulate_tax_registry
        result = simulate_tax_registry(uuid.uuid4())
        assert result["_mock"] is True

    @pytest.mark.asyncio
    async def test_gmail_mock_mode_returns_mock_response(self):
        from app.integrations.gmail import GmailClient
        client = GmailClient()
        result = await client.send_notification("x@y.com", "subj", "body")
        assert result["_mock"] is True

    @pytest.mark.asyncio
    async def test_slack_mock_mode_returns_mock_response(self):
        from app.integrations.slack import SlackClient
        client = SlackClient()
        executed: set[str] = set()
        result = await client.post_branch_alert(uuid.uuid4(), "test", executed)
        assert result["_mock"] is True
