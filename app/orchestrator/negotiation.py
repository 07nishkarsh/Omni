"""
Negotiation engine — multi-round proposal loop between agents.

Executes a bounded loop where agents exchange Proposals. 
If Agent C returns PARTIAL (disbursement split), the engine automatically 
lowers the requested amount and counters.
"""

from __future__ import annotations

from dataclasses import dataclass
import structlog
from uuid import uuid4

from app.models import TransactionContext, Proposal, ProposalStatus
from app.orchestrator.state_machine import StateMachine
from app.orchestrator.validator import validate_proposal_history, ValidationError

log = structlog.get_logger(__name__)

MAX_ROUNDS = 4


@dataclass
class NegotiationResult:
    accepted: bool
    final_proposal: Proposal
    rounds: int
    transcript: list[Proposal]
    reason: str = ""


class NegotiationEngine:
    """
    Drives a multi-round negotiation between the orchestrator and the agent pipeline.
    """

    def __init__(self, max_rounds: int = MAX_ROUNDS) -> None:
        self._max_rounds = max_rounds

    async def run(self, ctx: TransactionContext) -> NegotiationResult:
        log.info("negotiation.start", transaction_id=str(ctx.transaction_id))
        
        transcript: list[Proposal] = []
        current_ctx = ctx.model_copy()

        for round_num in range(1, self._max_rounds + 1):
            log.info("negotiation.round", round=round_num, transaction_id=str(current_ctx.transaction_id))
            
            sm = StateMachine(current_ctx)
            proposal = await sm.run_pipeline()
            transcript.append(proposal)

            if proposal.status == ProposalStatus.ACCEPTED:
                log.info("negotiation.accepted", round=round_num)
                # Ensure the history is valid
                validate_proposal_history(transcript)
                return NegotiationResult(
                    accepted=True,
                    final_proposal=proposal,
                    rounds=round_num,
                    transcript=transcript,
                    reason="Consensus reached."
                )
            
            if proposal.status == ProposalStatus.REJECTED:
                log.info("negotiation.rejected", round=round_num)
                validate_proposal_history(transcript)
                return NegotiationResult(
                    accepted=False,
                    final_proposal=proposal,
                    rounds=round_num,
                    transcript=transcript,
                    reason="Pipeline rejected transaction."
                )

            if proposal.status == ProposalStatus.COUNTERED:
                log.info("negotiation.countered", round=round_num, new_amount=proposal.proposed_amount)
                # Counter-offer: lower requested amount to match available amount (disbursement split)
                current_ctx = current_ctx.model_copy(update={"requested_amount": proposal.proposed_amount})
                
                # We could add an Orchestrator Counter Proposal to the transcript here to reflect 
                # the customer accepting the split, but we'll let the next round's output represent it.

        # Exhausted rounds without agreement
        log.warning("negotiation.max_rounds_exceeded", transaction_id=str(ctx.transaction_id))
        
        # Escalate to human review
        final_proposal = Proposal(
            transaction_id=ctx.transaction_id,
            originated_by="Orchestrator",
            status=ProposalStatus.REJECTED,
            proposed_amount=current_ctx.requested_amount,
            metadata={"cited_clause": "Section II, Clause 1", "requires_human_review": "true"},
            rationale=f"Round limit ({self._max_rounds}) reached without consensus. Escalated to Manager Approval Desk."
        )
        transcript.append(final_proposal)
        
        validate_proposal_history(transcript)
        
        return NegotiationResult(
            accepted=False,
            final_proposal=final_proposal,
            rounds=self._max_rounds,
            transcript=transcript,
            reason=f"No agreement reached after {self._max_rounds} rounds."
        )
