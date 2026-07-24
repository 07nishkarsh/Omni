import asyncio
from typing import Optional
from uuid import UUID, uuid4
from decimal import Decimal
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.models.transaction import TransactionContext, TransactionType
from app.services.transaction_store import transaction_store

router = APIRouter()

class ApplicationCreate(BaseModel):
    customer_name: str
    loan_type: str
    requested_amount: float
    annual_declared_income: float
    is_urgent: bool
    target_fund: str

class ApplicationResponse(BaseModel):
    transaction_id: UUID

@router.post("/applications", response_model=ApplicationResponse)
async def create_application(app_data: ApplicationCreate, background_tasks: BackgroundTasks):
    applicant_id = f"APP-DASH-{uuid4().hex[:8]}"
    transaction_id = uuid4()
    
    # Map loan type string to enum
    try:
        transaction_type = TransactionType(app_data.loan_type)
    except ValueError:
        transaction_type = TransactionType.LOAN_APPLICATION
        
    ctx = TransactionContext(
        transaction_id=transaction_id,
        transaction_type=transaction_type,
        customer_name=app_data.customer_name,
        customer_email=f"{app_data.customer_name.lower().replace(' ', '.')}@example.com",
        requested_amount=Decimal(str(app_data.requested_amount)),
        annual_declared_income=Decimal(str(app_data.annual_declared_income)),
        is_urgent=app_data.is_urgent,
        target_fund=app_data.target_fund,
        mock_credit_score=750,  # default
        mock_treasury_pool=app_data.target_fund  # Use the selected target fund
    )
    
    # Upsert immediately so status endpoint finds it
    transaction_store.upsert(ctx)
    transaction_store.add_progress(transaction_id, 1, "Application received & validated", f"Applicant: {app_data.customer_name}")
    
    background_tasks.add_task(_run_dashboard_pipeline, ctx, applicant_id)
    
    return ApplicationResponse(transaction_id=transaction_id)

async def _run_dashboard_pipeline(ctx: TransactionContext, applicant_id: str):
    from app.scripts.simulate_engine import _extract_route, _check_human_review, _map_final_status
    from app.orchestrator.negotiation import NegotiationEngine
    from app.orchestrator.history import TranscriptCompressor
    from app.orchestrator.verdict import generate_verdict_text
    from app.models.transaction import TransactionStatus
    import structlog
    from datetime import datetime, timezone

    log = structlog.get_logger(__name__)
    log.info("simulate.start", trigger_type="dashboard", applicant_id=applicant_id, transaction_id=str(ctx.transaction_id))

    error = None
    try:
        engine = NegotiationEngine()
        result = await engine.run(ctx)
    except Exception as exc:
        log.error("simulate.pipeline_error", error=str(exc))
        error = str(exc)
        result = None

    route = _extract_route(result)
    requires_human = _check_human_review(result)

    policy_version = "unknown"
    summary = None
    if result and result.transcript:
        try:
            compressor = TranscriptCompressor()
            summary = compressor.compress(ctx, result.transcript, route=route)
            policy_version = summary.policy_version
        except Exception as exc:
            log.error("simulate.compression_error", error=str(exc))

    if result:
        final_status = _map_final_status(result, requires_human)
    else:
        final_status = TransactionStatus.REJECTED

    transaction_store.set_status(ctx.transaction_id, final_status)
    verdict_text = generate_verdict_text(ctx)
    transaction_store.add_progress(ctx.transaction_id, 8, "Verdict issued", verdict_text)

    if final_status == TransactionStatus.ESCALATED and summary:
        try:
            from app.integrations.notion_audit import NotionAuditClient
            client = NotionAuditClient()
            client.create_approval_desk_entry(ctx, summary)
            log.info("simulate.manager_desk_pushed", transaction_id=str(ctx.transaction_id))
            transaction_store.add_progress(ctx.transaction_id, 9, "Manager decision", "Awaiting human review")
        except Exception as exc:
            log.error("simulate.manager_desk_push_failed", error=str(exc))

    if final_status == TransactionStatus.APPROVED:
        transaction_store.add_progress(ctx.transaction_id, 10, "Credit/disbursement confirmed", "Funds released")
    elif final_status == TransactionStatus.REJECTED:
        transaction_store.add_progress(ctx.transaction_id, 10, "Credit/disbursement confirmed", "Disbursement halted")
    elif final_status == TransactionStatus.ESCALATED:
        transaction_store.add_progress(ctx.transaction_id, 10, "Credit/disbursement confirmed", "Pending manager approval")

@router.get("/applications/{transaction_id}/status")
def get_application_status(transaction_id: UUID):
    ctx = transaction_store.get(transaction_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Transaction not found")
        
    progress = transaction_store.get_progress(transaction_id)
    return {
        "transaction_id": transaction_id,
        "status": ctx.status,
        "steps": progress,
        "requires_human_review": any(s["step_num"] == 9 for s in progress) or ctx.status == "TransactionStatus.ESCALATED"
    }
