"""
Health-check router.

Provides a lightweight liveness probe (`GET /health`) and a detailed
readiness probe (`GET /health/ready`) that validates configuration.
"""

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    version: str
    environment: str


class ReadinessResponse(HealthResponse):
    mock_toggles: dict[str, bool]
    llm_model: str
    config_ok: bool


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 if the application process is running.",
)
async def health_check() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
        version="0.1.0",
        environment=settings.app_env,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Returns 200 if the application is ready to serve traffic. "
        "Validates that required configuration is present (mock values are acceptable)."
    ),
)
async def readiness_check() -> ReadinessResponse:
    settings = get_settings()
    config_ok = bool(settings.llm_api_key and settings.notion_token)
    return ReadinessResponse(
        status="ready" if config_ok else "degraded",
        timestamp=datetime.now(timezone.utc).isoformat(),
        version="0.1.0",
        environment=settings.app_env,
        mock_toggles={
            "notion": settings.use_mock_notion,
            "gmail": settings.use_mock_gmail,
            "slack": settings.use_mock_slack,
            "llm": settings.use_mock_llm,
        },
        llm_model=settings.llm_model,
        config_ok=config_ok,
    )
