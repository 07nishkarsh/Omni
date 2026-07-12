"""
bank-agent-orchestrator – application entry-point.

Creates and configures the FastAPI application instance.
All external data sources (Notion, Gmail, Slack, credit-bureau, core-banking)
are **mocked** – this project contains no real financial integrations.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import health, orchestration, webhooks
from app.routes import simulate as simulate_route
from app.services.notion_poller import start_poller, stop_poller

settings = get_settings()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start/stop background services around the app lifetime."""
    start_poller(settings)
    yield
    stop_poller()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Bank Agent Orchestrator",
        description=(
            "Simulated banking workflow orchestrator. "
            "All external integrations (Notion, Gmail, Slack, core-banking, "
            "credit bureau) are mocked – no real financial data is processed."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, tags=["Health"])
    app.include_router(orchestration.router, prefix="/api/v1", tags=["Orchestration"])
    app.include_router(webhooks.router, tags=["Webhooks"])
    app.include_router(simulate_route.router, prefix="/api/v1", tags=["Simulation"])

    return app


app = create_app()
