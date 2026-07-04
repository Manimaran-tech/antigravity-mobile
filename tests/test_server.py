import os
import json
import pytest
from fastapi.testclient import TestClient

# Mock the PIN and config before importing server
# Create a temporary config.json for testing
test_config = {
    "pin": "999999",
    "secret_key": "test_secret_key_12345"
}
with open("config.json", "w") as f:
    json.dump(test_config, f)

from antigravity_remote.server import app, ACTIVE_SESSIONS, runner

client = TestClient(app)

@pytest.fixture(autouse=True)
def cleanup():
    # Clear active sessions and tasks database
    ACTIVE_SESSIONS.clear()
    runner.tasks_data.clear()
    if os.path.exists(runner.db_path):
        try:
            os.remove(runner.db_path)
        except Exception:
            pass
    yield
    # Clean up test files
    if os.path.exists("config.json"):
        try:
            os.remove("config.json")
        except Exception:
            pass
    if os.path.exists(runner.db_path):
        try:
            os.remove(runner.db_path)
        except Exception:
            pass

def test_login_invalid_pin():
    response = client.post("/api/login", json={"pin": "000000"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid PIN"

def test_login_success():
    response = client.post("/api/login", json={"pin": "999999"})
    assert response.status_code == 200
    assert "token" in response.json()
    token = response.json()["token"]
    assert token in ACTIVE_SESSIONS

def test_unauthorized_endpoints():
    endpoints = [
        ("/api/status", "GET"),
        ("/api/workspace", "GET"),
        ("/api/tasks", "GET"),
        ("/api/schedule", "POST"),
    ]
    for endpoint, method in endpoints:
        if method == "GET":
            response = client.get(endpoint)
        else:
            response = client.post(endpoint, json={})
        assert response.status_code == 403  # HTTPBearer returns 403 on missing credentials

def test_authorized_status():
    # Login first
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/status", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "cpu" in data
    assert "memory" in data
    assert "disk" in data
    assert "agent_status" in data

def test_task_lifecycle():
    # Login
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 1. Schedule a task
    cmd = "echo 'Testing Antigravity'"
    response = client.post("/api/schedule", json={"command": cmd}, headers=headers)
    assert response.status_code == 200
    task_data = response.json()
    assert task_data["command"] == cmd
    assert task_data["status"] == "pending"
    task_id = task_data["id"]
    
    # Verify in tasks list
    list_resp = client.get("/api/tasks", headers=headers)
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["id"] == task_id
    
    # 2. Confirm and Run the task
    run_resp = client.post(f"/api/tasks/{task_id}/confirm", headers=headers)
    assert run_resp.status_code == 200
    assert run_resp.json()["status"] == "started"
    
    # Wait briefly for background execution
    import time
    time.sleep(1.0)
    
    # Check status (should be completed or running)
    status_resp = client.get("/api/tasks", headers=headers)
    task_updated = next(t for t in status_resp.json() if t["id"] == task_id)
    assert task_updated["status"] in ("running", "completed")
    
    # 3. Check logs
    logs_resp = client.get(f"/api/tasks/{task_id}/logs", headers=headers)
    assert logs_resp.status_code == 200
    assert "logs" in logs_resp.json()
