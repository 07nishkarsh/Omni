import pytest
from fastapi.testclient import TestClient
from uuid import UUID

from app.main import app
from app.services.transaction_store import transaction_store

client = TestClient(app)

def test_create_application():
    payload = {
        "customer_name": "Test User",
        "loan_type": "personal",
        "requested_amount": 10000,
        "annual_declared_income": 50000,
        "is_urgent": False,
        "target_fund": "General Subsidy Pool"
    }
    
    response = client.post("/api/v1/applications", json=payload)
    assert response.status_code == 200
    
    data = response.json()
    assert "transaction_id" in data
    
    # Check if it was added to the store immediately
    txn_id = UUID(data["transaction_id"])
    ctx = transaction_store.get(txn_id)
    assert ctx is not None
    assert ctx.customer_name == "Test User"
    
    # Check status endpoint
    status_response = client.get(f"/api/v1/applications/{txn_id}/status")
    assert status_response.status_code == 200
    
    status_data = status_response.json()
    assert status_data["transaction_id"] == str(txn_id)
    assert len(status_data["steps"]) >= 1
    assert status_data["steps"][0]["step_num"] == 1
    assert "Test User" in status_data["steps"][0]["detail"]
