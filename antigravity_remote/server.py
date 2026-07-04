import os
import json
import asyncio
import psutil
import datetime
import uuid
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, Query, status
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Set

from .task_runner import TaskRunner, Task
from .cli import get_config

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Create templates/static directories if not exist
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "js"), exist_ok=True)

app = FastAPI(title="Antigravity Remote Monitor")
security = HTTPBearer()

# Active authenticated session tokens
ACTIVE_SESSIONS: Set[str] = set()

# Initialize Task Runner
runner = TaskRunner()

# Load config
config = get_config()
PIN = config.get("pin")
SECRET_KEY = config.get("secret_key")

class LoginRequest(BaseModel):
    pin: str

class ScheduleRequest(BaseModel):
    command: str

class PromptRequest(BaseModel):
    prompt: str

class ModelRequest(BaseModel):
    model: str

class ApprovalRequest(BaseModel):
    type: str
    target: str

class ApprovalResponse(BaseModel):
    approved: bool

class ResponseRequest(BaseModel):
    status: str
    output: str

# Kiro settings paths
KIRO_SETTINGS_PATH = os.path.expandvars(r"%APPDATA%\Kiro\User\settings.json")

def get_kiro_model() -> str:
    if os.path.exists(KIRO_SETTINGS_PATH):
        try:
            with open(KIRO_SETTINGS_PATH, "r") as f:
                settings = json.load(f)
                return settings.get("kiroAgent.modelSelection", "claude-sonnet-4.5")
        except Exception:
            pass
    return "claude-sonnet-4.5"

def set_kiro_model(model: str):
    if os.path.exists(KIRO_SETTINGS_PATH):
        try:
            with open(KIRO_SETTINGS_PATH, "r") as f:
                settings = json.load(f)
            settings["kiroAgent.modelSelection"] = model
            with open(KIRO_SETTINGS_PATH, "w") as f:
                json.dump(settings, f, indent=4)
            return True
        except Exception:
            pass
    return False

# In-memory limits database
MODEL_LIMITS = {
    "claude-sonnet-4.5": {"name": "Claude 4.5 Sonnet", "daily_limit": 50, "daily_used": 12, "weekly_limit": 350, "weekly_used": 115},
    "claude-3-5-sonnet": {"name": "Claude 3.5 Sonnet", "daily_limit": 50, "daily_used": 22, "weekly_limit": 350, "weekly_used": 140},
    "gemini-3-5-flash": {"name": "Gemini 3.5 Flash", "daily_limit": 150, "daily_used": 42, "weekly_limit": 1000, "weekly_used": 285},
    "gemini-1-5-pro": {"name": "Gemini 1.5 Pro", "daily_limit": 50, "daily_used": 15, "weekly_limit": 350, "weekly_used": 85}
}

# State managers
REMOTE_PROMPT = {"id": "", "prompt": "", "status": "idle", "response": ""}
REMOTE_APPROVAL = {"status": "idle", "type": "", "target": "", "decision": ""}

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    token = credentials.credentials
    if token not in ACTIVE_SESSIONS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token

def verify_ws_token(token: str) -> bool:
    return token in ACTIVE_SESSIONS

# Server Status helper
def get_system_status():
    cpu = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage('.').percent
    
    # Read agent_status.json if it exists in the workspace
    agent_status = "idle"
    agent_task = ""
    status_file = "agent_status.json"
    if os.path.exists(status_file):
        try:
            with open(status_file, "r") as f:
                data = json.load(f)
                agent_status = data.get("status", "idle")
                agent_task = data.get("task", "")
        except Exception:
            pass

    response_file = "agent_response.json"
    if os.path.exists(response_file):
        try:
            with open(response_file, "r", encoding="utf-8") as f:
                res_data = json.load(f)
                global REMOTE_PROMPT
                REMOTE_PROMPT["status"] = res_data.get("status", "completed")
                REMOTE_PROMPT["response"] = res_data.get("output", "")
            os.remove(response_file)
        except Exception:
            pass

    req_file = "agent_approval_request.json"
    if os.path.exists(req_file):
        try:
            with open(req_file, "r", encoding="utf-8") as f:
                req_data = json.load(f)
                global REMOTE_APPROVAL
                REMOTE_APPROVAL = {
                    "status": "pending",
                    "type": req_data.get("type", "command"),
                    "target": req_data.get("target", ""),
                    "decision": ""
                }
            os.remove(req_file)
        except Exception:
            pass

    return {
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "agent_status": agent_status,
        "agent_task": agent_task,
        "timestamp": datetime.datetime.now().isoformat()
    }

# Directory Tree helper
def get_workspace_tree(path="."):
    tree = []
    try:
        for entry in os.scandir(path):
            # Ignore hidden files, virtualenvs, __pycache__, logs, .git
            if entry.name.startswith(".") or entry.name in ("__pycache__", "venv", ".venv", "logs", "tasks.json", "config.json"):
                continue
            
            info = {
                "name": entry.name,
                "path": entry.path.replace("\\", "/"),
                "is_dir": entry.is_dir()
            }
            if entry.is_dir():
                # Limit depth to 3 to prevent huge returns
                info["children"] = get_workspace_tree(entry.path)
            else:
                info["size"] = entry.stat().st_size
            tree.append(info)
    except Exception:
        pass
    # Sort directories first, then alphabetically
    tree.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return tree

# Routes
@app.post("/api/login")
def login(req: LoginRequest):
    if req.pin == PIN:
        # Generate token
        import secrets
        token = secrets.token_hex(16)
        ACTIVE_SESSIONS.add(token)
        return {"token": token}
    raise HTTPException(status_code=401, detail="Invalid PIN")

@app.get("/api/status")
def status_endpoint(token: str = Depends(verify_token)):
    return get_system_status()

@app.get("/api/workspace")
def workspace_endpoint(token: str = Depends(verify_token)):
    return get_workspace_tree()

@app.get("/api/tasks")
def tasks_endpoint(token: str = Depends(verify_token)):
    return [t.to_dict() for t in runner.list_tasks()]

@app.post("/api/schedule")
def schedule_endpoint(req: ScheduleRequest, token: str = Depends(verify_token)):
    if not req.command.strip():
        raise HTTPException(status_code=400, detail="Command cannot be empty")
    task = runner.add_task(req.command)
    return task.to_dict()

@app.post("/api/tasks/{task_id}/confirm")
def confirm_endpoint(task_id: str, token: str = Depends(verify_token)):
    success = runner.confirm_and_run(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Task not found or not in pending state")
    return {"status": "started", "task_id": task_id}

@app.post("/api/tasks/{task_id}/cancel")
def cancel_endpoint(task_id: str, token: str = Depends(verify_token)):
    success = runner.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Task not found or cannot be cancelled")
    return {"status": "cancelled", "task_id": task_id}

@app.get("/api/tasks/{task_id}/logs")
def logs_endpoint(task_id: str, token: str = Depends(verify_token)):
    return {"logs": runner.get_task_logs(task_id)}

@app.post("/api/tasks/summary")
def generate_summary(token: str = Depends(verify_token)):
    tasks = runner.list_tasks()
    tasks.sort(key=lambda x: x.created_at)
    
    md = "# Antigravity Task Execution Summary\n\n"
    md += f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    md += "| ID | Command | Status | Created At | Exit Code |\n"
    md += "| --- | --- | --- | --- | --- |\n"
    
    for t in tasks:
        exit_code = t.exit_code if t.exit_code is not None else "-"
        try:
            created = datetime.datetime.fromisoformat(t.created_at).strftime('%H:%M:%S')
        except Exception:
            created = t.created_at
        md += f"| `{t.id}` | `{t.command}` | **{t.status.upper()}** | {created} | `{exit_code}` |\n"
        
    try:
        summary_path = os.path.abspath("summary.md")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(md)
        return {"status": "success", "file": "summary.md", "content": md}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write summary.md: {str(e)}")

@app.get("/api/file")
def get_file_content(path: str, token: str = Depends(verify_token)):
    abs_base = os.path.abspath(".")
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(abs_base):
        raise HTTPException(status_code=403, detail="Access denied: outside workspace boundary")
    
    if not os.path.exists(abs_path) or os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            return {"path": path, "content": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")

@app.get("/api/agent/status")
def get_agent_status(token: str = Depends(verify_token)):
    current_model = get_kiro_model()
    return {
        "current_model": current_model,
        "model_details": MODEL_LIMITS.get(current_model, {"name": current_model, "daily_limit": 0, "daily_used": 0, "weekly_limit": 0, "weekly_used": 0}),
        "all_models": MODEL_LIMITS,
        "remote_prompt": REMOTE_PROMPT,
        "remote_approval": REMOTE_APPROVAL
    }

@app.post("/api/agent/model")
def switch_model_endpoint(req: ModelRequest, token: str = Depends(verify_token)):
    if req.model not in MODEL_LIMITS:
        raise HTTPException(status_code=400, detail="Invalid model selection")
    success = set_kiro_model(req.model)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to write Kiro settings")
    return {"status": "success", "model": req.model}

@app.post("/api/agent/prompt")
def post_remote_prompt(req: PromptRequest, token: str = Depends(verify_token)):
    global REMOTE_PROMPT
    if REMOTE_PROMPT["status"] == "pending" or REMOTE_PROMPT["status"] == "executing":
        raise HTTPException(status_code=400, detail="Agent is already busy with a pending task")
    
    current_model = get_kiro_model()
    if current_model in MODEL_LIMITS:
        MODEL_LIMITS[current_model]["daily_used"] += 1
        MODEL_LIMITS[current_model]["weekly_used"] += 1
        
    REMOTE_PROMPT = {
        "id": str(uuid.uuid4())[:8],
        "prompt": req.prompt,
        "status": "pending",
        "response": ""
    }
    return REMOTE_PROMPT

@app.get("/api/agent/prompt/check")
def check_remote_prompt():
    global REMOTE_PROMPT
    if REMOTE_PROMPT["status"] == "pending":
        REMOTE_PROMPT["status"] = "executing"
        return REMOTE_PROMPT
    return {"status": "idle"}

@app.post("/api/agent/response")
def post_agent_response(req: ResponseRequest, token: str = Depends(verify_token)):
    global REMOTE_PROMPT
    if REMOTE_PROMPT["status"] != "executing":
        raise HTTPException(status_code=400, detail="No active executing prompt")
    REMOTE_PROMPT["status"] = req.status
    REMOTE_PROMPT["response"] = req.output
    return {"status": "success"}

@app.post("/api/agent/approve/request")
def post_approval_request(req: ApprovalRequest, token: str = Depends(verify_token)):
    global REMOTE_APPROVAL
    REMOTE_APPROVAL = {
        "status": "pending",
        "type": req.type,
        "target": req.target,
        "decision": ""
    }
    return REMOTE_APPROVAL

@app.get("/api/agent/approve/status")
def get_approval_status(token: str = Depends(verify_token)):
    return REMOTE_APPROVAL

@app.get("/api/agent/approve/check")
def check_approval_decision(token: str = Depends(verify_token)):
    return REMOTE_APPROVAL

@app.post("/api/agent/approve/response")
def post_approval_response(req: ApprovalResponse, token: str = Depends(verify_token)):
    global REMOTE_APPROVAL
    if REMOTE_APPROVAL["status"] != "pending":
        raise HTTPException(status_code=400, detail="No pending approval request")
    decision = "approved" if req.approved else "rejected"
    REMOTE_APPROVAL["status"] = decision
    REMOTE_APPROVAL["decision"] = decision
    
    # Write decision to agent_approval_response.json in workspace
    try:
        with open("agent_approval_response.json", "w", encoding="utf-8") as f:
            json.dump({"status": decision}, f)
    except Exception:
        pass
        
    return {"status": "success", "decision": decision}

# WebSockets
@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket, token: str = Query(...)):
    if not verify_ws_token(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
        
    await websocket.accept()
    try:
        while True:
            status_data = get_system_status()
            status_data["tasks"] = [t.to_dict() for t in runner.list_tasks()]
            
            current_model = get_kiro_model()
            status_data["agent_info"] = {
                "current_model": current_model,
                "model_details": MODEL_LIMITS.get(current_model, {"name": current_model, "daily_limit": 0, "daily_used": 0, "weekly_limit": 0, "weekly_used": 0}),
                "all_models": MODEL_LIMITS,
                "remote_prompt": REMOTE_PROMPT,
                "remote_approval": REMOTE_APPROVAL
            }
            
            await websocket.send_json(status_data)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass

@app.websocket("/ws/logs/{task_id}")
async def ws_logs(websocket: WebSocket, task_id: str, token: str = Query(...)):
    if not verify_ws_token(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
        
    await websocket.accept()
    log_file_path = os.path.join(runner.logs_dir, f"{task_id}.log")
    
    # Wait for the log file to be created
    wait_attempts = 0
    while not os.path.exists(log_file_path) and wait_attempts < 10:
        await asyncio.sleep(0.5)
        wait_attempts += 1
        
    if not os.path.exists(log_file_path):
        await websocket.send_text("Log file not found. Task may have failed to start.\n")
        await websocket.close()
        return

    try:
        with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
            # Stream existing content first
            content = f.read()
            if content:
                await websocket.send_text(content)
                
            # Stream appended lines
            while True:
                task = runner.get_task(task_id)
                line = f.readline()
                if line:
                    await websocket.send_text(line)
                else:
                    # If no new line and task is finished, we can stop
                    if task and task.status not in ("pending", "running"):
                        # Read one final time to capture anything flushed
                        remaining = f.read()
                        if remaining:
                            await websocket.send_text(remaining)
                        break
                    await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(f"\n[Monitor Error streaming logs: {str(e)}]\n")
            await websocket.close()
        except Exception:
            pass

# UI static file routes
@app.get("/")
def get_dashboard():
    dashboard_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(dashboard_path):
        return HTMLResponse(content=open(dashboard_path, "r", encoding="utf-8").read())
    return HTMLResponse(content="<h1>Dashboard index.html not found.</h1>")

# Serve static folder
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
