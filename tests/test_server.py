import os
import json
import pytest
import time
from fastapi.testclient import TestClient

# Mock the PIN and config before importing server
test_config = {
    "pin": "999999",
    "secret_key": "test_secret_key_12345"
}

with open("config.json", "w") as f:
    json.dump(test_config, f)

from antigravity_remote.server import app, ACTIVE_SESSIONS, LOGIN_ATTEMPTS, runner

client = TestClient(app)

@pytest.fixture(autouse=True)
def cleanup():
    # Make sure config.json exists before each test starts
    with open("config.json", "w") as f:
        json.dump(test_config, f)
        
    ACTIVE_SESSIONS.clear()
    LOGIN_ATTEMPTS.clear()
    runner.tasks_data.clear()
    if os.path.exists(runner.db_path):
        try:
            os.remove(runner.db_path)
        except Exception:
            pass
    yield
    # Clean up test files
    for fn in ("config.json", runner.db_path):
        if os.path.exists(fn):
            try:
                os.remove(fn)
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
        ("/api/agent/prompt/check", "GET"),  # Verify check endpoint requires auth now!
    ]
    for endpoint, method in endpoints:
        if method == "GET":
            response = client.get(endpoint)
        else:
            response = client.post(endpoint, json={})
        assert response.status_code in (401, 403)

def test_authorized_status():
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

def test_login_rate_limiting():
    # Attempt 5 incorrect logins
    for _ in range(5):
        response = client.post("/api/login", json={"pin": "000000"})
        assert response.status_code == 401
        
    # 6th attempt should trigger 429 rate limit lockout
    response = client.post("/api/login", json={"pin": "999999"})
    assert response.status_code == 429
    assert "Too many failed attempts" in response.json()["detail"]

def test_session_expiry():
    # Login to generate token
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    
    # Artificially expire the token by backdating its creation timestamp
    ACTIVE_SESSIONS[token] = time.time() - 90000  # 90000s > 86400s (24h)
    
    # Request status with expired token
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/status", headers=headers)
    assert response.status_code == 401
    assert "Invalid or expired session token" in response.json()["detail"]

def test_path_traversal_protection():
    # Login
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # Attempt traversals
    bad_paths = [
        "../config.json",
        "..\\config.json",
        "d:/Remote Antigravity/../config.json",
    ]
    for bp in bad_paths:
        response = client.get(f"/api/file?path={bp}", headers=headers)
        assert response.status_code in (403, 404)

def test_command_validation():
    # Login
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 1. Null byte
    response = client.post("/api/schedule", json={"command": "echo \x00 test"}, headers=headers)
    assert response.status_code == 400
    assert "null bytes" in response.json()["detail"]
    
    # 2. Control chars
    response = client.post("/api/schedule", json={"command": "echo \x01 test"}, headers=headers)
    assert response.status_code == 400
    assert "control characters" in response.json()["detail"]
    
    # 3. Too long command
    long_cmd = "a" * 2001
    response = client.post("/api/schedule", json={"command": long_cmd}, headers=headers)
    assert response.status_code == 400
    assert "exceeds maximum length" in response.json()["detail"]

def test_task_lifecycle():
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
    time.sleep(1.0)
    
    # Check status
    status_resp = client.get("/api/tasks", headers=headers)
    task_updated = next(t for t in status_resp.json() if t["id"] == task_id)
    assert task_updated["status"] in ("running", "completed")
    
    # 3. Check logs
    logs_resp = client.get(f"/api/tasks/{task_id}/logs", headers=headers)
    assert logs_resp.status_code == 200
    assert "logs" in logs_resp.json()
