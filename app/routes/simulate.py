"""
app/routes/simulate.py

POST /simulate/trigger — HTTP interface to the simulation engine.

Accepts the same trigger_type and applicant_id as the CLI script and
returns a structured SimulationResult JSON response. Useful for:
  - Testing via curl / Postman / FastAPI /docs
  - Automated integration tests that need a live running server
  - Triggering simulations remotely without SSH access to the host

All 4 triggers can be called back-to-back without restarting the server.
Each call generates a fresh UUID — running the same trigger twice never
produces duplicate TransactionIDs.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.scripts.persistence import VALID_TRIGGER_TYPES, FixtureError
from app.scripts.simulate_engine import run_simulation, SimulationResult

router = APIRouter()


# ── Request / Response models ─────────────────────────────────────────────────

class SimulateTriggerRequest(BaseModel):
    trigger_type: str
    applicant_id: str


class SimulateTriggerResponse(BaseModel):
    transaction_id: UUID
    trigger_type: str
    applicant_id: str
    applicant_name: str
    final_status: str
    route: str
    rounds: int
    outcome_reason: str
    requires_human_review: bool
    policy_version: str
    error: str | None = None


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post(
    "/simulate/trigger",
    response_model=SimulateTriggerResponse,
    status_code=status.HTTP_200_OK,
    summary="Simulate a banking workflow trigger",
    description=(
        "Runs the full production orchestrator pipeline for the given trigger_type "
        "and applicant_id from fixtures.json. "
        "Returns a structured result including TransactionID, route, and outcome. "
        "All 4 trigger types can be called back-to-back without restarting.\n\n"
        f"**Valid trigger_type values:** {sorted(VALID_TRIGGER_TYPES)}"
    ),
    tags=["Simulation"],
)
async def simulate_trigger(body: SimulateTriggerRequest) -> SimulateTriggerResponse:
    try:
        result = await run_simulation(
            trigger_type=body.trigger_type,
            applicant_id=body.applicant_id,
        )
    except FixtureError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Simulation pipeline error: {exc}",
        )

    return SimulateTriggerResponse(
        transaction_id=result.transaction_id,
        trigger_type=result.trigger_type,
        applicant_id=result.applicant_id,
        applicant_name=result.applicant_name,
        final_status=result.final_status,
        route=result.route,
        rounds=result.rounds,
        outcome_reason=result.outcome_reason,
        requires_human_review=result.requires_human_review,
        policy_version=result.policy_version,
        error=result.error,
    )
