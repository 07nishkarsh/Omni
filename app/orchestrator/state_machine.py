"""
State machine for the banking workflow orchestrator.

Executes the pipeline by sequentially invoking Agent A, Agent B, and Agent C.
Returns a Proposal object representing the outcome of the pipeline execution.
"""

from __future__ import annotations

import structlog

from app.models import TransactionContext, Proposal, ProposalStatus
from app.agents.llm_client import LLMUnavailableError
from app.agents.agent_a_loans import process_loan_application, AgentRoutingError
from app.agents.agent_b_risk import assess_risk, AgentBError
from app.agents.agent_c_treasury import evaluate_treasury, AgentCError
from app.services.transaction_store import transaction_store

log = structlog.get_logger(__name__)


class StateMachineError(Exception):
    """Raised when an unexpected error occurs during pipeline execution."""


class StateMachine:
    """
    Drives a TransactionContext through the Agent pipeline (A -> B -> C).
    """

    def __init__(self, ctx: TransactionContext) -> None:
        self._ctx = ctx

    async def run_pipeline(self) -> Proposal:
        """
        Execute one iteration of the pipeline.
        Returns a Proposal representing the outcome.
        """
        log.info("state_machine.pipeline.start", transaction_id=str(self._ctx.transaction_id))

        try:
            # 1. Agent A: Routing
            routing_decision = process_loan_application(self._ctx)
            route = routing_decision.route
            log.info("state_machine.agent_a", route=route)
            transaction_store.add_progress(
                self._ctx.transaction_id, 2, "Agent A — routing decision", f"Routed: {route}"
            )
            
            # 2. Agent B: Risk (Only if in route)
            requires_human_review = False
            b_clause = ""
            
            if "B" in route:
                risk_response = assess_risk(self._ctx, route)
                log.info("state_machine.agent_b", status=risk_response.status, flags=risk_response.flags)
                b_clause = risk_response.citedClause
                
                if risk_response.status == "RISK_VETO":
                    transaction_store.add_progress(
                        self._ctx.transaction_id, 3, "Agent B — compliance check", "FAILED — Vetoed"
                    )
                    return Proposal(
                        transaction_id=self._ctx.transaction_id,
                        originated_by="Agent B",
                        status=ProposalStatus.REJECTED,
                        proposed_amount=self._ctx.requested_amount,
                        metadata={"cited_clause": b_clause, "requires_human_review": "true"},
                        rationale=f"Risk Veto: {risk_response.notes}"
                    )
                else:
                    transaction_store.add_progress(
                        self._ctx.transaction_id, 3, "Agent B — compliance check", "PASSED"
                    )
            else:
                log.info("state_machine.agent_b_skipped", route=route)
                transaction_store.add_progress(
                    self._ctx.transaction_id, 3, "Agent B — compliance check", "Skipped (Fast Track)"
                )

            # 3. Agent C: Treasury
            treasury_response = evaluate_treasury(self._ctx, route)
            log.info("state_machine.agent_c", status=treasury_response.status)
            c_clause = treasury_response.citedClause
            
            if treasury_response.requiresHumanReview:
                requires_human_review = True

            if treasury_response.status == "TREASURY_REJECT":
                transaction_store.add_progress(self._ctx.transaction_id, 4, "Agent C — treasury check", "FAILED — Insufficient funds")
            elif treasury_response.status == "PARTIAL":
                transaction_store.add_progress(self._ctx.transaction_id, 4, "Agent C — treasury check", "COUNTERED — Partial funds")
            else:
                transaction_store.add_progress(self._ctx.transaction_id, 4, "Agent C — treasury check", "PASSED")

            # Convert to Proposal
            if treasury_response.status == "TREASURY_REJECT":
                return Proposal(
                    transaction_id=self._ctx.transaction_id,
                    originated_by="Agent C",
                    status=ProposalStatus.REJECTED,
                    proposed_amount=self._ctx.requested_amount,
                    metadata={"cited_clause": c_clause, "requires_human_review": str(requires_human_review).lower()},
                    rationale=f"Treasury Reject: {treasury_response.notes}"
                )
            elif treasury_response.status == "PARTIAL":
                return Proposal(
                    transaction_id=self._ctx.transaction_id,
                    originated_by="Agent C",
                    status=ProposalStatus.COUNTERED,
                    proposed_amount=treasury_response.availableAmount,
                    metadata={"cited_clause": c_clause, "requires_human_review": str(requires_human_review).lower()},
                    rationale=f"Partial Funds Available: {treasury_response.notes}"
                )
            else:
                return Proposal(
                    transaction_id=self._ctx.transaction_id,
                    originated_by="Agent C",
                    status=ProposalStatus.ACCEPTED,
                    proposed_amount=self._ctx.requested_amount,
                    metadata={"cited_clause": c_clause, "requires_human_review": str(requires_human_review).lower()},
                    rationale="Approved by Treasury"
                )

        except LLMUnavailableError as exc:
            log.error("state_machine.llm_unavailable", error=str(exc))
            return Proposal(
                transaction_id=self._ctx.transaction_id,
                originated_by="Orchestrator",
                status=ProposalStatus.REJECTED,
                proposed_amount=self._ctx.requested_amount,
                metadata={"cited_clause": "Section I, Clause 2", "requires_human_review": "true"},
                rationale="LLM Unavailable. Escalated to human review."
            )
        except (AgentRoutingError, AgentBError, AgentCError) as exc:
            log.error("state_machine.agent_error", error=str(exc))
            raise StateMachineError(f"Pipeline failed due to agent error: {exc}") from exc
