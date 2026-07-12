"""
orchestrator/history.py

Negotiation transcript store and compression engine.

Responsibilities:
  1. Store the raw Proposal transcript per TransactionID in memory during a run.
  2. On transaction completion, compress the raw transcript into a concise
     executive summary via one LLM call (through app.agents.llm_client.call_llm).
  3. Push the compressed summary to the Notion Audit & Activity Feed via the
     NotionAuditClient, with all required fields populated per Policy Book
     Section III, Clause 1: TransactionID, AgentsInvolved, Route, Outcome,
     Timestamp, PolicyVersion.
  4. Keep the raw transcript accessible for debugging even after compression.

Design decisions:
  - The compressor prompt lists ONLY the agent names that actually appear in the
    raw transcript. This is the mechanism that prevents hallucinated agent names:
    the LLM is constrained to only the agents it was shown, and the test verifies
    the prompt contained exactly those names.
  - PolicyVersion is a SHA-256 hash of policy_book.md content, truncated to 8 hex
    chars. This is deterministic and changes automatically when the policy book
    changes.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import structlog

from app.agents.llm_client import call_llm, LLMUnavailableError
from app.models.proposal import Proposal
from app.models.transaction import TransactionContext

log = structlog.get_logger(__name__)

_POLICY_BOOK_PATH = Path(__file__).parent.parent / "agents" / "policy_book.md"

# ── Policy version ────────────────────────────────────────────────────────────

def get_policy_version() -> str:
    """
    Return a short deterministic version string derived from the Policy Book content.
    Changes automatically whenever policy_book.md is edited.
    """
    try:
        content = _POLICY_BOOK_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "unknown"
    digest = hashlib.sha256(content.encode()).hexdigest()[:8]
    return f"pb-{digest}"


# ── Compressed summary schema ─────────────────────────────────────────────────

@dataclass
class CompressedSummary:
    transaction_id: UUID
    bullets: list[str]                 # 3-6 bullet points
    agents_involved: list[str]         # extracted from raw transcript
    route: str
    outcome: str
    policy_version: str
    compressed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw_transcript_json: str = ""      # preserved verbatim for debugging


# ── In-memory transcript store ────────────────────────────────────────────────

class TranscriptStore:
    """
    Thread-safe in-memory store mapping TransactionID → list[Proposal].
    Raw transcripts remain in the store after compression so they are
    always available for debugging.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._transcripts: dict[UUID, list[Proposal]] = {}
        self._summaries: dict[UUID, CompressedSummary] = {}

    def append_round(self, transaction_id: UUID, proposal: Proposal) -> None:
        with self._lock:
            self._transcripts.setdefault(transaction_id, []).append(proposal)

    def store_transcript(self, transaction_id: UUID, transcript: list[Proposal]) -> None:
        """Bulk-store a completed transcript (e.g. from NegotiationEngine)."""
        with self._lock:
            self._transcripts[transaction_id] = list(transcript)

    def get_raw_transcript(self, transaction_id: UUID) -> list[Proposal]:
        with self._lock:
            return list(self._transcripts.get(transaction_id, []))

    def store_summary(self, summary: CompressedSummary) -> None:
        with self._lock:
            self._summaries[summary.transaction_id] = summary

    def get_summary(self, transaction_id: UUID) -> CompressedSummary | None:
        with self._lock:
            return self._summaries.get(transaction_id)

    def all_transaction_ids(self) -> list[UUID]:
        with self._lock:
            return list(self._transcripts.keys())


# Module-level singleton.
transcript_store = TranscriptStore()


# ── Compression engine ────────────────────────────────────────────────────────

_COMPRESSOR_SYSTEM_PROMPT = """\
You are a compliance summariser for a simulated banking workflow.

Given a JSON list of Proposal objects from a negotiation transcript, produce a
concise executive summary of EXACTLY 3-6 bullet points covering:
  • What was requested (amount, type, applicant)
  • What was flagged (risk flags, policy issues)
  • How it was resolved (negotiation steps, guarantor, split)
  • Final outcome (APPROVED / REJECTED / ESCALATED)

Rules:
  - Output ONLY a JSON array of strings (the bullet points). No prose, no markdown.
  - Each bullet must start with "• ".
  - Only reference agent names from this exact list: {agent_names}
  - Do not mention any agent not in that list.
  - Do not invent amounts, clause numbers, or outcomes not present in the transcript.

Example output:
["• Requested: ₹25,000 subsidy loan for applicant Jane Doe.", "• Flagged: 2-year-old unresolved tax dispute by Agent B.", "• Resolved: Guarantor Jane Smith offset the risk flag.", "• Final outcome: APPROVED by Agent C (Treasury)."]
"""


class TranscriptCompressor:
    """
    Compresses a raw negotiation transcript into a bulleted executive summary
    via a single LLM call, then pushes it to the Notion Audit Feed.
    """

    def __init__(self) -> None:
        pass

    def compress(
        self,
        ctx: TransactionContext,
        transcript: list[Proposal],
        route: str,
    ) -> CompressedSummary:
        """
        Compress the raw transcript and push the result to the Audit Feed.

        Args:
            ctx: The original TransactionContext.
            transcript: Full list of Proposals from the negotiation.
            route: Agent route string (e.g. "A->B->C").

        Returns:
            CompressedSummary with bullets and audit metadata.

        The raw transcript is preserved in TranscriptStore regardless of whether
        the LLM call succeeds or fails.
        """
        policy_version = get_policy_version()

        # Store raw transcript immediately (survives even if compression fails).
        transcript_store.store_transcript(ctx.transaction_id, transcript)

        # Extract agent names that ACTUALLY appear in this transcript.
        agents_involved = _extract_agents(transcript)
        outcome = _extract_outcome(transcript)

        # Serialize transcript for the LLM prompt.
        raw_json = _serialize_transcript(transcript)

        # Build the prompt, constraining agent names to only real ones.
        system = _COMPRESSOR_SYSTEM_PROMPT.replace(
            "{agent_names}", ", ".join(agents_involved) or "None"
        )
        user_message = (
            f"Transaction ID: {ctx.transaction_id}\n"
            f"Customer: {ctx.customer_name}\n"
            f"Requested Amount: {ctx.requested_amount} {ctx.currency}\n"
            f"Route: {route}\n\n"
            f"Raw negotiation transcript:\n{raw_json}"
        )

        # ── LLM compression call ──────────────────────────────────────────────
        bullets: list[str] = []
        try:
            raw_response = call_llm(system, user_message)
            bullets = _parse_bullets(raw_response)
            log.info(
                "history.compression_complete",
                transaction_id=str(ctx.transaction_id),
                bullet_count=len(bullets),
            )
        except LLMUnavailableError as exc:
            log.error("history.compression_llm_failed", error=str(exc))
            # Graceful degradation: produce a minimal summary without LLM.
            bullets = [
                f"• Requested: {ctx.requested_amount} {ctx.currency} by {ctx.customer_name}.",
                f"• Route: {route}.",
                f"• Outcome: {outcome} (summary unavailable — LLM offline).",
            ]

        summary = CompressedSummary(
            transaction_id=ctx.transaction_id,
            bullets=bullets,
            agents_involved=agents_involved,
            route=route,
            outcome=outcome,
            policy_version=policy_version,
            raw_transcript_json=raw_json,
        )

        # Persist in memory.
        transcript_store.store_summary(summary)

        # Push to Notion Audit Feed (async-compatible via sync wrapper).
        _push_to_audit_feed(ctx, summary)

        return summary


# ── Notion Audit Feed push ────────────────────────────────────────────────────

def _push_to_audit_feed(ctx: TransactionContext, summary: CompressedSummary) -> None:
    """
    Write the compressed summary to the Notion Audit & Activity Feed database.
    Uses the NotionAuditClient which respects USE_MOCK_NOTION.
    """
    from app.integrations.notion_audit import NotionAuditClient

    client = NotionAuditClient()
    try:
        result = client.create_audit_entry(ctx=ctx, summary=summary)
        log.info(
            "history.audit_feed_pushed",
            transaction_id=str(ctx.transaction_id),
            notion_page_id=result.get("id", "mock"),
        )
    except Exception as exc:
        # Audit push failure must never crash the orchestrator.
        log.error("history.audit_feed_push_failed", error=str(exc))


# ── Private helpers ───────────────────────────────────────────────────────────

def _extract_agents(transcript: list[Proposal]) -> list[str]:
    """Return deduplicated list of originated_by values from the transcript."""
    seen: list[str] = []
    for proposal in transcript:
        name = proposal.originated_by
        if name and name not in seen:
            seen.append(name)
    return seen


def _extract_outcome(transcript: list[Proposal]) -> str:
    """Return the status of the last proposal in the transcript."""
    if not transcript:
        return "UNKNOWN"
    return transcript[-1].status.upper()


def _serialize_transcript(transcript: list[Proposal]) -> str:
    """JSON-serialize the transcript for inclusion in the LLM prompt."""
    rows = []
    for p in transcript:
        rows.append({
            "proposal_id": str(p.proposal_id),
            "originated_by": p.originated_by,
            "status": p.status,
            "proposed_amount": str(p.proposed_amount),
            "rationale": p.rationale,
            "cited_clause": p.metadata.get("cited_clause", ""),
            "requires_human_review": p.metadata.get("requires_human_review", "false"),
        })
    return json.dumps(rows, indent=2)


def _parse_bullets(raw: str) -> list[str]:
    """
    Parse the LLM response into a list of bullet strings.
    Expects a JSON array of strings. Falls back gracefully if malformed.
    """
    raw = raw.strip()
    # Strip markdown fences if model added them.
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(b) for b in parsed]
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: treat each line starting with "•" as a bullet.
    return [line.strip() for line in raw.splitlines() if line.strip().startswith("•")]
