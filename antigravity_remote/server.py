import os
import json
import asyncio
import psutil
import datetime
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
            # Include tasks list
            status_data["tasks"] = [t.to_dict() for t in runner.list_tasks()]
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
