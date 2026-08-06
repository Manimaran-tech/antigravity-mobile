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

# Override CONFIG_FILE path in cli to use the local test config
import antigravity_remote.cli
antigravity_remote.cli.CONFIG_FILE = os.path.abspath("config.json")

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

def test_ag2r_plan_endpoints():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # GET Plan — starts empty now (no hardcoded default)
    res = client.get("/api/agent/plan", headers=headers)
    assert res.status_code == 200

    # POST Plan first — create a real plan
    new_plan = {
        "title": "Test Plan",
        "goal": "Test Goal",
        "status": "pending",
        "steps": [{"id": "s1", "text": "Step 1", "status": "pending", "comments": []}]
    }
    res = client.post("/api/agent/plan", json=new_plan, headers=headers)
    assert res.status_code == 200
    assert res.json()["title"] == "Test Plan"

    # GET Plan — now it should have the plan we just posted
    res = client.get("/api/agent/plan", headers=headers)
    assert res.status_code == 200
    plan = res.json()
    assert "title" in plan
    assert "steps" in plan

    # Approve Step
    res = client.post("/api/agent/plan/approve", json={"step_id": "s1", "approved": True}, headers=headers)
    assert res.status_code == 200
    assert res.json()["step"]["status"] == "completed"

    # Comment Step
    res = client.post("/api/agent/plan/comment", json={"step_id": "s1", "comment": "Nice step"}, headers=headers)
    assert res.status_code == 200
    assert len(res.json()["comments"]) == 1

def test_ag2r_questions_and_btw_endpoints():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # POST Question
    q_data = {"question": "Should we proceed?", "options": ["Yes", "No"], "is_multi_select": False}
    res = client.post("/api/agent/questions/request", json=q_data, headers=headers)
    assert res.status_code == 200
    q_id = res.json()["id"]

    # Answer Question
    res = client.post("/api/agent/questions/response", json={"question_id": q_id, "answers": ["Yes"]}, headers=headers)
    assert res.status_code == 200
    assert res.json()["question"]["status"] == "answered"

    # POST BTW
    btw_data = {"category": "test", "title": "Note 1", "content": "Sample content"}
    res = client.post("/api/agent/btw", json=btw_data, headers=headers)
    assert res.status_code == 200
    btw_id = res.json()["id"]

    # DELETE BTW
    res = client.delete(f"/api/agent/btw/{btw_id}", headers=headers)
    assert res.status_code == 200

def test_ag2r_project_explorer_and_diffs():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Tree
    res = client.get("/api/project/tree", headers=headers)
    assert res.status_code == 200
    assert "children" in res.json()

    # Diffs
    res = client.get("/api/project/diffs", headers=headers)
    assert res.status_code == 200
    assert "is_git" in res.json()

    # File view
    abs_path = os.path.abspath("pyproject.toml")
    res = client.get(f"/api/project/file?path={abs_path}", headers=headers)
    assert res.status_code == 200
    assert "content" in res.json()

def test_workspace_switching_and_file_diff():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Test get targets returns workspaces/projects
    res = client.get("/api/agent/targets", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "workspace_path" in data
    assert "projects" in data

    # 2. Test post targets switches targets
    original_dir = os.getcwd()
    new_dir = os.path.dirname(original_dir)
    res = client.post("/api/agent/targets", json={"workspace_path": new_dir}, headers=headers)
    assert res.status_code == 200
    
    # Verify directory switched
    assert os.path.realpath(os.getcwd()) == os.path.realpath(new_dir)
    
    # Restore original directory
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

def test_ag2r_plan_endpoints():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # GET Plan — starts empty now (no hardcoded default)
    res = client.get("/api/agent/plan", headers=headers)
    assert res.status_code == 200

    # POST Plan first — create a real plan
    new_plan = {
        "title": "Test Plan",
        "goal": "Test Goal",
        "status": "pending",
        "steps": [{"id": "s1", "text": "Step 1", "status": "pending", "comments": []}]
    }
    res = client.post("/api/agent/plan", json=new_plan, headers=headers)
    assert res.status_code == 200
    assert res.json()["title"] == "Test Plan"

    # GET Plan — now it should have the plan we just posted
    res = client.get("/api/agent/plan", headers=headers)
    assert res.status_code == 200
    plan = res.json()
    assert "title" in plan
    assert "steps" in plan

    # Approve Step
    res = client.post("/api/agent/plan/approve", json={"step_id": "s1", "approved": True}, headers=headers)
    assert res.status_code == 200
    assert res.json()["step"]["status"] == "completed"

    # Comment Step
    res = client.post("/api/agent/plan/comment", json={"step_id": "s1", "comment": "Nice step"}, headers=headers)
    assert res.status_code == 200
    assert len(res.json()["comments"]) == 1

def test_ag2r_questions_and_btw_endpoints():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # POST Question
    q_data = {"question": "Should we proceed?", "options": ["Yes", "No"], "is_multi_select": False}
    res = client.post("/api/agent/questions/request", json=q_data, headers=headers)
    assert res.status_code == 200
    q_id = res.json()["id"]

    # Answer Question
    res = client.post("/api/agent/questions/response", json={"question_id": q_id, "answers": ["Yes"]}, headers=headers)
    assert res.status_code == 200
    assert res.json()["question"]["status"] == "answered"

    # POST BTW
    btw_data = {"category": "test", "title": "Note 1", "content": "Sample content"}
    res = client.post("/api/agent/btw", json=btw_data, headers=headers)
    assert res.status_code == 200
    btw_id = res.json()["id"]

    # DELETE BTW
    res = client.delete(f"/api/agent/btw/{btw_id}", headers=headers)
    assert res.status_code == 200

def test_ag2r_project_explorer_and_diffs():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Tree
    res = client.get("/api/project/tree", headers=headers)
    assert res.status_code == 200
    assert "children" in res.json()

    # Diffs
    res = client.get("/api/project/diffs", headers=headers)
    assert res.status_code == 200
    assert "is_git" in res.json()

    # File view
    abs_path = os.path.abspath("pyproject.toml")
    res = client.get(f"/api/project/file?path={abs_path}", headers=headers)
    assert res.status_code == 200
    assert "content" in res.json()

def test_workspace_switching_and_file_diff():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Test get targets returns workspaces/projects
    res = client.get("/api/agent/targets", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "workspace_path" in data
    assert "projects" in data

    # 2. Test post targets switches targets
    original_dir = os.getcwd()
    new_dir = os.path.dirname(original_dir)
    res = client.post("/api/agent/targets", json={"workspace_path": new_dir}, headers=headers)
    assert res.status_code == 200
    
    # Verify directory switched
    assert os.path.realpath(os.getcwd()) == os.path.realpath(new_dir)
    
    # Restore original directory and target
    client.post("/api/agent/targets", json={"workspace_path": original_dir}, headers=headers)
    os.chdir(original_dir)

    # 3. Test git diff endpoint
    res = client.get("/api/project/diff?path=pyproject.toml", headers=headers)
    assert res.status_code == 200
    assert "diff" in res.json()


def test_remote_prompt_lifecycle():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Post remote prompt
    res = client.post("/api/agent/prompt", json={"prompt": "delete startup.md"}, headers=headers)
    assert res.status_code == 200
    assert "delete startup.md" in res.json()["prompt"]
    assert res.json()["status"] == "pending"

    # 2. Check remote prompt
    res = client.get("/api/agent/prompt/check", headers=headers)
    assert res.status_code == 200
    assert "delete startup.md" in res.json()["prompt"]
    assert res.json()["status"] == "executing"

    # 3. Post agent response
    res = client.post("/api/agent/response", json={"status": "completed", "output": "Done!"}, headers=headers)
    assert res.status_code == 200

def test_history_endpoints():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    res = client.get("/api/history/sessions", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "sessions" in data
    assert isinstance(data["sessions"], list)

def test_history_continue():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    from antigravity_remote.server import BRAIN_DIR
    import shutil

    session_id = "test_dummy_session_123"
    session_dir = os.path.join(BRAIN_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    
    logs_dir = os.path.join(session_dir, ".system_generated", "logs")
    os.makedirs(logs_dir, exist_ok=True)
    
    dummy_cwd = os.path.abspath(os.getcwd())
    transcript_line = {
        "step_index": 0,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "tool_calls": [
            {
                "arguments": {
                    "CommandLine": "git status",
                    "Cwd": dummy_cwd
                }
            }
        ]
    }
    with open(os.path.join(logs_dir, "transcript.jsonl"), "w") as f:
        f.write(json.dumps(transcript_line) + "\n")

    try:
        res = client.post(f"/api/history/session/{session_id}/continue", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert os.path.realpath(data["workspace_path"]) == os.path.realpath(dummy_cwd)
    finally:
        if os.path.exists(session_dir):
            shutil.rmtree(session_dir)

def test_browse_endpoint():
    login_resp = client.post("/api/login", json={"pin": "999999"})
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Test browsing current directory
    res = client.get("/api/browse", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "path" in data
    assert "entries" in data
    assert isinstance(data["entries"], list)
    
    # 2. Test browsing with specific path
    cwd = os.path.abspath(os.getcwd())
    res = client.get(f"/api/browse?path={cwd}", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["path"] == cwd.replace("\\", "/")
    
    # 3. Test non-existent path
    res = client.get("/api/browse?path=C:/invalid/path/that/does/not/exist", headers=headers)
    assert res.status_code == 200
    assert "error" in res.json()

