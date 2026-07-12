"""
Tests for the health-check endpoints.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app import app


@pytest.fixture()
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_liveness(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert "timestamp" in body
    assert "environment" in body


@pytest.mark.asyncio
async def test_readiness(client: AsyncClient):
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ready", "degraded"}
    assert "mock_toggles" in body
    assert "llm_model" in body
    assert isinstance(body["config_ok"], bool)
