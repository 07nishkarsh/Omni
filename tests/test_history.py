"""
tests/test_history.py

Unit tests for orchestrator/history.py (transcript store + compression).

Checklist verified:
  ✓ Compressed summary meaningfully shorter than raw transcript
  ✓ No hallucinated agent names — mock called with correct transcript content
  ✓ PolicyVersion matches Policy Book version derived during the run
  ✓ Raw transcript preserved after compression (accessible for debugging)
  ✓ All required Notion Audit Feed fields populated in mock output
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest

from app.models.proposal import Proposal, ProposalStatus
from app.models.transaction import TransactionContext, TransactionType
from app.orchestrator.history import (
    TranscriptCompressor,
    TranscriptStore,
    transcript_store,
    get_policy_version,
    _extract_agents,
    _extract_outcome,
    _serialize_transcript,
    _parse_bullets,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ctx(name: str = "Test Applicant") -> TransactionContext:
    return TransactionContext(
        transaction_id=uuid.uuid4(),
        transaction_type=TransactionType.LOAN_APPLICATION,
        customer_name=name,
        customer_email="test@example.com",
        requested_amount=Decimal("25000"),
        currency="INR",
    )


def _make_proposal(
    ctx: TransactionContext,
    originated_by: str,
    status: ProposalStatus = ProposalStatus.ACCEPTED,
    amount: Decimal | None = None,
    rationale: str = "Test rationale",
    cited_clause: str = "Section I, Clause 1",
) -> Proposal:
    return Proposal(
        transaction_id=ctx.transaction_id,
        originated_by=originated_by,
        status=status,
        proposed_amount=amount or ctx.requested_amount,
        rationale=rationale,
        metadata={"cited_clause": cited_clause, "requires_human_review": "false"},
    )


# ── Canned LLM response used across compression tests ─────────────────────────

_CANNED_SUMMARY = json.dumps([
    "• Requested: ₹25,000 subsidy loan for Test Applicant.",
    "• Flagged: 2-year unresolved tax dispute detected by Agent B.",
    "• Resolved: Guarantor offset the risk flag per Section I, Clause 1.",
    "• Negotiated: Agent C offered partial disbursement of ₹15,000.",
    "• Final outcome: ACCEPTED by Agent C (Treasury).",
])


# ══════════════════════════════════════════════════════════════════════════════
# TranscriptStore tests
# ══════════════════════════════════════════════════════════════════════════════

class TestTranscriptStore:
    def test_store_and_retrieve_transcript(self):
        store = TranscriptStore()
        ctx = _make_ctx()
        p1 = _make_proposal(ctx, "Agent A", ProposalStatus.SUBMITTED)
        p2 = _make_proposal(ctx, "Agent B", ProposalStatus.COUNTERED)
        p3 = _make_proposal(ctx, "Agent C", ProposalStatus.ACCEPTED)

        store.store_transcript(ctx.transaction_id, [p1, p2, p3])
        retrieved = store.get_raw_transcript(ctx.transaction_id)

        assert len(retrieved) == 3
        assert retrieved[0].originated_by == "Agent A"
        assert retrieved[2].status == ProposalStatus.ACCEPTED

    def test_raw_transcript_preserved_after_summary_stored(self):
        from app.orchestrator.history import CompressedSummary
        import datetime

        store = TranscriptStore()
        ctx = _make_ctx()
        proposals = [_make_proposal(ctx, "Agent C")]
        store.store_transcript(ctx.transaction_id, proposals)

        # Store a summary
        summary = CompressedSummary(
            transaction_id=ctx.transaction_id,
            bullets=["• Test bullet."],
            agents_involved=["Agent C"],
            route="A->C",
            outcome="ACCEPTED",
            policy_version="pb-test0001",
            raw_transcript_json="[]",
        )
        store.store_summary(summary)

        # Raw transcript must still be there
        raw = store.get_raw_transcript(ctx.transaction_id)
        assert len(raw) == 1
        assert raw[0].originated_by == "Agent C"

    def test_get_nonexistent_returns_empty_list(self):
        store = TranscriptStore()
        assert store.get_raw_transcript(uuid.uuid4()) == []

    def test_get_nonexistent_summary_returns_none(self):
        store = TranscriptStore()
        assert store.get_summary(uuid.uuid4()) is None


# ══════════════════════════════════════════════════════════════════════════════
# Helper function tests
# ══════════════════════════════════════════════════════════════════════════════

class TestHelpers:
    def test_extract_agents_deduplicated_in_order(self):
        ctx = _make_ctx()
        transcript = [
            _make_proposal(ctx, "Agent A"),
            _make_proposal(ctx, "Agent B"),
            _make_proposal(ctx, "Agent C"),
            _make_proposal(ctx, "Agent C"),  # duplicate
        ]
        agents = _extract_agents(transcript)
        assert agents == ["Agent A", "Agent B", "Agent C"]

    def test_extract_outcome_uses_last_proposal(self):
        ctx = _make_ctx()
        transcript = [
            _make_proposal(ctx, "Agent A", ProposalStatus.COUNTERED),
            _make_proposal(ctx, "Agent C", ProposalStatus.ACCEPTED),
        ]
        assert _extract_outcome(transcript) == "ACCEPTED"

    def test_extract_outcome_empty_transcript(self):
        assert _extract_outcome([]) == "UNKNOWN"

    def test_serialize_transcript_is_valid_json(self):
        ctx = _make_ctx()
        transcript = [_make_proposal(ctx, "Agent A")]
        raw_json = _serialize_transcript(transcript)
        parsed = json.loads(raw_json)
        assert isinstance(parsed, list)
        assert parsed[0]["originated_by"] == "Agent A"

    def test_parse_bullets_valid_json_array(self):
        raw = json.dumps(["• Bullet one.", "• Bullet two."])
        bullets = _parse_bullets(raw)
        assert bullets == ["• Bullet one.", "• Bullet two."]

    def test_parse_bullets_with_markdown_fence(self):
        raw = "```json\n[\"• Bullet one.\"]\n```"
        bullets = _parse_bullets(raw)
        assert bullets == ["• Bullet one."]

    def test_parse_bullets_fallback_to_lines(self):
        raw = "• Bullet one.\n• Bullet two.\nIgnored line."
        bullets = _parse_bullets(raw)
        assert "• Bullet one." in bullets
        assert "• Bullet two." in bullets
        assert "Ignored line." not in bullets


# ══════════════════════════════════════════════════════════════════════════════
# Compression tests (key requirements)
# ══════════════════════════════════════════════════════════════════════════════

class TestCompression:
    """
    All three LLM-dependent tests mock call_llm to return _CANNED_SUMMARY.
    This avoids quota/network dependencies while still verifying the full
    compression pipeline.
    """

    def _build_transcript(self, ctx: TransactionContext) -> list[Proposal]:
        return [
            _make_proposal(ctx, "Agent A", ProposalStatus.SUBMITTED,
                           rationale="Routed to A->B->C per Section I, Clause 1."),
            _make_proposal(ctx, "Agent B", ProposalStatus.COUNTERED,
                           rationale="Tax dispute flagged. Guarantor offsets risk.",
                           cited_clause="Section I, Clause 1"),
            _make_proposal(ctx, "Agent C", ProposalStatus.COUNTERED,
                           amount=Decimal("15000"),
                           rationale="Partial disbursement: ₹15,000 available.",
                           cited_clause="Section I, Clause 1"),
            _make_proposal(ctx, "Agent C", ProposalStatus.ACCEPTED,
                           amount=Decimal("15000"),
                           rationale="Approved on adjusted amount.",
                           cited_clause="Section I, Clause 1"),
        ]

    @patch("app.orchestrator.history.call_llm", return_value=_CANNED_SUMMARY)
    @patch("app.orchestrator.history._push_to_audit_feed")  # isolate Notion
    def test_compressed_summary_shorter_than_raw_transcript(self, mock_push, mock_llm):
        ctx = _make_ctx()
        transcript = self._build_transcript(ctx)
        raw_json = _serialize_transcript(transcript)

        compressor = TranscriptCompressor()
        summary = compressor.compress(ctx, transcript, route="A->B->C")

        compressed_text = " ".join(summary.bullets)
        assert len(compressed_text) < len(raw_json), (
            f"Compressed ({len(compressed_text)}) is not shorter than raw ({len(raw_json)})"
        )

    @patch("app.orchestrator.history.call_llm", return_value=_CANNED_SUMMARY)
    @patch("app.orchestrator.history._push_to_audit_feed")
    def test_mock_called_with_correct_agent_names_in_prompt(self, mock_push, mock_llm):
        """
        Anti-hallucination test: asserts the LLM prompt was constructed with
        ONLY the agent names extracted from the real transcript.
        This verifies the constraint mechanism — we do not trust the LLM response.
        """
        ctx = _make_ctx()
        transcript = self._build_transcript(ctx)
        real_agents = _extract_agents(transcript)  # ["Agent A", "Agent B", "Agent C"]

        compressor = TranscriptCompressor()
        compressor.compress(ctx, transcript, route="A->B->C")

        # The mock must have been called once.
        assert mock_llm.call_count == 1

        # The system prompt (first positional arg) must contain the real agent names.
        system_prompt_used = mock_llm.call_args[0][0]
        for agent_name in real_agents:
            assert agent_name in system_prompt_used, (
                f"Agent '{agent_name}' was not in the compressor system prompt. "
                f"This means the anti-hallucination constraint was not applied."
            )

        # The system prompt must NOT contain agent names we didn't see.
        for hallucinated_name in ["Agent D", "Agent E", "Orchestrator-X", "SuperAgent"]:
            assert hallucinated_name not in system_prompt_used

    @patch("app.orchestrator.history.call_llm", return_value=_CANNED_SUMMARY)
    @patch("app.orchestrator.history._push_to_audit_feed")
    def test_user_message_contains_raw_transcript(self, mock_push, mock_llm):
        """
        The raw transcript JSON must be passed verbatim to the LLM so it has
        full context and cannot invent amounts or clauses.
        """
        ctx = _make_ctx()
        transcript = self._build_transcript(ctx)
        expected_raw = _serialize_transcript(transcript)

        TranscriptCompressor().compress(ctx, transcript, route="A->B->C")

        user_message_used = mock_llm.call_args[0][1]
        assert expected_raw in user_message_used

    @patch("app.orchestrator.history.call_llm", return_value=_CANNED_SUMMARY)
    @patch("app.orchestrator.history._push_to_audit_feed")
    def test_raw_transcript_preserved_after_compression(self, mock_push, mock_llm):
        ctx = _make_ctx()
        transcript = self._build_transcript(ctx)

        TranscriptCompressor().compress(ctx, transcript, route="A->B->C")

        raw = transcript_store.get_raw_transcript(ctx.transaction_id)
        assert len(raw) == len(transcript)
        assert raw[0].originated_by == "Agent A"

    @patch("app.orchestrator.history.call_llm", return_value=_CANNED_SUMMARY)
    @patch("app.orchestrator.history._push_to_audit_feed")
    def test_policy_version_is_consistent(self, mock_push, mock_llm):
        """
        PolicyVersion must be the same across two compress() calls on the same
        policy_book.md — i.e. it's deterministic, not random.
        """
        ctx = _make_ctx()
        transcript = self._build_transcript(ctx)

        v1 = get_policy_version()
        summary = TranscriptCompressor().compress(ctx, transcript, route="A->B->C")
        v2 = get_policy_version()

        assert v1 == v2
        assert summary.policy_version == v1
        assert summary.policy_version.startswith("pb-")

    @patch("app.orchestrator.history.call_llm", return_value=_CANNED_SUMMARY)
    @patch("app.orchestrator.history._push_to_audit_feed")
    def test_summary_contains_correct_outcome(self, mock_push, mock_llm):
        ctx = _make_ctx()
        transcript = self._build_transcript(ctx)

        summary = TranscriptCompressor().compress(ctx, transcript, route="A->B->C")

        assert summary.outcome == "ACCEPTED"

    @patch("app.orchestrator.history.call_llm", return_value=_CANNED_SUMMARY)
    @patch("app.orchestrator.history._push_to_audit_feed")
    def test_summary_agents_match_transcript(self, mock_push, mock_llm):
        """agents_involved must exactly match the transcript, not be invented."""
        ctx = _make_ctx()
        transcript = self._build_transcript(ctx)

        summary = TranscriptCompressor().compress(ctx, transcript, route="A->B->C")

        assert summary.agents_involved == ["Agent A", "Agent B", "Agent C"]
        assert "Agent D" not in summary.agents_involved

    @patch("app.orchestrator.history.call_llm", return_value=_CANNED_SUMMARY)
    @patch("app.orchestrator.history._push_to_audit_feed")
    def test_audit_feed_push_called_once(self, mock_push, mock_llm):
        ctx = _make_ctx()
        transcript = self._build_transcript(ctx)

        TranscriptCompressor().compress(ctx, transcript, route="A->B->C")

        mock_push.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# Notion Audit Client tests
# ══════════════════════════════════════════════════════════════════════════════

class TestNotionAuditClient:
    """Verify the Audit Feed entry is populated with all required fields."""

    def test_mock_audit_entry_has_all_required_fields(self):
        from app.integrations.notion_audit import NotionAuditClient
        from app.orchestrator.history import CompressedSummary

        ctx = _make_ctx()
        summary = CompressedSummary(
            transaction_id=ctx.transaction_id,
            bullets=["• Requested: ₹25,000.", "• Outcome: APPROVED."],
            agents_involved=["Agent A", "Agent C"],
            route="A->C",
            outcome="APPROVED",
            policy_version="pb-abc12345",
            raw_transcript_json="[{}]",
        )

        client = NotionAuditClient()
        result = client.create_audit_entry(ctx=ctx, summary=summary)

        props = result["properties"]
        assert props["TransactionID"] == str(ctx.transaction_id)
        assert "Agent A" in props["AgentsInvolved"]
        assert "Agent C" in props["AgentsInvolved"]
        assert props["Route"] == "A->C"
        assert props["Outcome"] == "APPROVED"
        assert props["PolicyVersion"] == "pb-abc12345"
        assert "₹25,000" in props["Summary"]
        assert result["_mock"] is True

    def test_mock_audit_summary_text_contains_bullets(self):
        from app.integrations.notion_audit import NotionAuditClient
        from app.orchestrator.history import CompressedSummary

        ctx = _make_ctx()
        bullets = ["• First bullet.", "• Second bullet.", "• Third bullet."]
        summary = CompressedSummary(
            transaction_id=ctx.transaction_id,
            bullets=bullets,
            agents_involved=["Agent B"],
            route="A->B->C",
            outcome="REJECTED",
            policy_version="pb-xyz99999",
            raw_transcript_json="[]",
        )

        result = NotionAuditClient().create_audit_entry(ctx=ctx, summary=summary)
        for bullet in bullets:
            assert bullet in result["properties"]["Summary"]


# ══════════════════════════════════════════════════════════════════════════════
# LLM fallback (graceful degradation)
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMFallback:
    """If the LLM is unavailable, the compressor must degrade gracefully."""

    @patch("app.orchestrator.history.call_llm",
           side_effect=__import__("app.agents.llm_client", fromlist=["LLMUnavailableError"]).LLMUnavailableError("offline"))
    @patch("app.orchestrator.history._push_to_audit_feed")
    def test_llm_unavailable_produces_minimal_summary(self, mock_push, mock_llm):
        ctx = _make_ctx()
        transcript = [_make_proposal(ctx, "Agent A")]

        summary = TranscriptCompressor().compress(ctx, transcript, route="A->C")

        assert len(summary.bullets) >= 1
        assert any("LLM offline" in b or "unavailable" in b.lower() for b in summary.bullets)

    @patch("app.orchestrator.history.call_llm",
           side_effect=__import__("app.agents.llm_client", fromlist=["LLMUnavailableError"]).LLMUnavailableError("offline"))
    @patch("app.orchestrator.history._push_to_audit_feed")
    def test_llm_unavailable_still_preserves_raw_transcript(self, mock_push, mock_llm):
        ctx = _make_ctx()
        transcript = [_make_proposal(ctx, "Agent A")]

        TranscriptCompressor().compress(ctx, transcript, route="A->C")

        assert len(transcript_store.get_raw_transcript(ctx.transaction_id)) == 1
