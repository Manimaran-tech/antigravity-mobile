import os
import json
import asyncio
import logging
import psutil
import datetime
import time
import uuid
import subprocess
import glob
import shutil
import re
from fastapi import FastAPI, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect, Query, status
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import threading
from pydantic import BaseModel
from typing import Dict, Set, Tuple, List, Optional, Any

from .task_runner import TaskRunner, Task
from .cli import get_config

def get_workspace_root() -> str:
    if 'REMOTE_TARGET' in globals() and REMOTE_TARGET and REMOTE_TARGET.get("workspace_path"):
        wp = REMOTE_TARGET["workspace_path"]
        if os.path.exists(wp):
            return os.path.abspath(wp)
    cwd = os.path.abspath(os.getcwd())
    if os.path.exists(os.path.join(cwd, ".agents")) or os.path.exists(os.path.join(cwd, ".git")):
        return cwd
    parent = os.path.dirname(cwd)
    if os.path.exists(os.path.join(parent, ".agents")) or os.path.exists(os.path.join(parent, ".git")):
        return parent
    return cwd

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Create templates/static directories if not exist
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "js"), exist_ok=True)

# Antigravity IDE Brain directory for implementation plans/walkthroughs
BRAIN_DIR = os.path.expandvars(r"%USERPROFILE%\.gemini\antigravity-ide\brain")

def get_git_branch(repo_path: str = None) -> str:
    """Get the current git branch name for a repository."""
    cwd = repo_path or get_workspace_root()
    # 1. Try git branch --show-current
    try:
        res = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=cwd, timeout=3
        )
        branch = res.stdout.strip()
        if branch and branch != "HEAD":
            return branch
    except Exception:
        pass

    # 2. Try git symbolic-ref
    try:
        res = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=3
        )
        branch = res.stdout.strip()
        if branch and branch != "HEAD":
            return branch
    except Exception:
        pass

    # 3. Try git rev-parse --abbrev-ref HEAD
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=3
        )
        branch = res.stdout.strip()
        if branch and branch != "HEAD":
            return branch
    except Exception:
        pass

    return "main"

def get_brain_sessions() -> List[Dict[str, Any]]:
    """Scan the Antigravity IDE brain directory for sessions with artifacts."""
    sessions = []
    if not os.path.exists(BRAIN_DIR):
        return sessions
    try:
        for entry in os.scandir(BRAIN_DIR):
            if not entry.is_dir() or entry.name == "tempmediaStorage":
                continue
            session = {
                "id": entry.name,
                "path": entry.path.replace("\\", "/"),
                "has_plan": False,
                "has_walkthrough": False,
                "has_task": False,
                "mtime": 0
            }
            plan_path = os.path.join(entry.path, "implementation_plan.md")
            walk_path = os.path.join(entry.path, "walkthrough.md")
            task_path = os.path.join(entry.path, "task.md")
            if os.path.exists(plan_path):
                session["has_plan"] = True
                session["mtime"] = os.path.getmtime(plan_path)
            if os.path.exists(walk_path):
                session["has_walkthrough"] = True
                if not session["mtime"]:
                    session["mtime"] = os.path.getmtime(walk_path)
            if os.path.exists(task_path):
                session["has_task"] = True
            if session["has_plan"] or session["has_walkthrough"] or session["has_task"]:
                sessions.append(session)
    except Exception:
        pass
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions

def get_latest_brain_plan() -> Dict[str, Any]:
    """Find and parse the most recent implementation_plan.md from the brain directory."""
    sessions = get_brain_sessions()
    for session in sessions:
        plan_path = os.path.join(BRAIN_DIR, session["id"], "implementation_plan.md")
        if os.path.exists(plan_path):
            try:
                with open(plan_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                meta_path = plan_path + ".metadata.json"
                meta = {}
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as mf:
                            meta = json.load(mf)
                    except Exception:
                        pass
                mtime = os.path.getmtime(plan_path)
                age_secs = time.time() - mtime
                if age_secs < 60:
                    age_str = f"{int(age_secs)} seconds ago"
                elif age_secs < 3600:
                    age_str = f"{int(age_secs / 60)} minutes ago"
                elif age_secs < 86400:
                    age_str = f"{int(age_secs / 3600)} hours ago"
                else:
                    age_str = f"{int(age_secs / 86400)} days ago"
                # Extract title from first heading
                title = "Implementation Plan"
                for line in content.splitlines():
                    if line.strip().startswith("# "):
                        title = line.strip()[2:].strip()
                        break
                return {
                    "session_id": session["id"],
                    "title": title,
                    "content": content,
                    "age": age_str,
                    "mtime": mtime,
                    "meta": meta,
                    "path": plan_path.replace("\\", "/")
                }
            except Exception:
                continue
    return {}

def get_latest_brain_walkthrough() -> Dict[str, Any]:
    """Find and parse the most recent walkthrough.md from the brain directory."""
    sessions = get_brain_sessions()
    for session in sessions:
        walk_path = os.path.join(BRAIN_DIR, session["id"], "walkthrough.md")
        if os.path.exists(walk_path):
            try:
                with open(walk_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                mtime = os.path.getmtime(walk_path)
                age_secs = time.time() - mtime
                if age_secs < 60:
                    age_str = f"{int(age_secs)} seconds ago"
                elif age_secs < 3600:
                    age_str = f"{int(age_secs / 60)} minutes ago"
                elif age_secs < 86400:
                    age_str = f"{int(age_secs / 3600)} hours ago"
                else:
                    age_str = f"{int(age_secs / 86400)} days ago"
                title = "Walkthrough"
                for line in content.splitlines():
                    if line.strip().startswith("# "):
                        title = line.strip()[2:].strip()
                        break
                return {
                    "session_id": session["id"],
                    "title": title,
                    "content": content,
                    "age": age_str,
                    "mtime": mtime,
                    "path": walk_path.replace("\\", "/")
                }
            except Exception:
                continue
    return {}

def read_brain_artifact(session_id: str, filename: str) -> Optional[str]:
    """Read a specific artifact from a brain session."""
    artifact_path = os.path.join(BRAIN_DIR, session_id, filename)
    if os.path.exists(artifact_path):
        try:
            with open(artifact_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            pass
    return None

def clean_prompt_text(text: str) -> str:
    """Strip XML-style system wrappers from user prompt text for clean display."""
    if not text:
        return ""
    clean = text
    if "<USER_REQUEST>" in clean:
        parts = clean.split("<USER_REQUEST>")
        if len(parts) > 1:
            clean = parts[1].split("</USER_REQUEST>")[0]
    # Strip metadata tags if present
    if "<ADDITIONAL_METADATA>" in clean:
        clean = clean.split("<ADDITIONAL_METADATA>")[0]
    if "<USER_SETTINGS_CHANGE>" in clean:
        clean = clean.split("<USER_SETTINGS_CHANGE>")[0]
    return clean.strip()

def get_conversation_sessions() -> List[Dict[str, Any]]:
    """Scan the brain directory for session transcripts and return summarized session history."""
    sessions = []
    if not os.path.exists(BRAIN_DIR):
        return sessions
    try:
        for entry in os.scandir(BRAIN_DIR):
            if not entry.is_dir() or entry.name == "tempmediaStorage":
                continue
            transcript_path = os.path.join(entry.path, ".system_generated", "logs", "transcript.jsonl")
            if not os.path.exists(transcript_path):
                continue
            
            first_prompt = ""
            turn_count = 0
            created_at = ""
            
            try:
                with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        if '"type"' in line and '"USER_INPUT"' in line:
                            try:
                                obj = json.loads(line)
                                step_type = obj.get("type")
                                if step_type == "USER_INPUT":
                                    turn_count += 1
                                    if not first_prompt:
                                        raw_content = obj.get("content", "")
                                        first_prompt = clean_prompt_text(raw_content)
                                    if not created_at and obj.get("created_at"):
                                        created_at = obj.get("created_at")
                            except Exception:
                                continue
            except Exception:
                continue

            if turn_count > 0:
                mtime = os.path.getmtime(transcript_path)
                age_secs = time.time() - mtime
                if age_secs < 60:
                    age_str = f"{int(age_secs)}s ago"
                elif age_secs < 3600:
                    age_str = f"{int(age_secs / 60)}m ago"
                elif age_secs < 86400:
                    age_str = f"{int(age_secs / 3600)}h ago"
                else:
                    age_str = f"{int(age_secs / 86400)}d ago"

                ws_path = find_session_workspace(entry.name)
                sessions.append({
                    "session_id": entry.name,
                    "title": first_prompt[:80] or "Conversation Session",
                    "turn_count": turn_count,
                    "mtime": mtime,
                    "age": age_str,
                    "created_at": created_at,
                    "workspace_path": ws_path
                })
    except Exception:
        pass

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions

def get_session_transcript(session_id: str) -> List[Dict[str, Any]]:
    """Parse transcript.jsonl for a specific session into clean chat turns."""
    turns = []
    transcript_path = os.path.join(BRAIN_DIR, session_id, ".system_generated", "logs", "transcript.jsonl")
    if not os.path.exists(transcript_path):
        return turns
        
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    step_type = obj.get("type")
                    if step_type == "USER_INPUT":
                        raw_content = obj.get("content", "")
                        clean_text = clean_prompt_text(raw_content)
                        if clean_text:
                            turns.append({
                                "id": f"hist_u_{obj.get('step_index', len(turns))}",
                                "sender": "user",
                                "text": clean_text,
                                "timestamp": obj.get("created_at", "")
                            })
                    elif step_type == "PLANNER_RESPONSE":
                        content = obj.get("content", "")
                        if content and content.strip():
                            turns.append({
                                "id": f"hist_a_{obj.get('step_index', len(turns))}",
                                "sender": "agent",
                                "text": content.strip(),
                                "timestamp": obj.get("created_at", "")
                            })
                except Exception:
                    continue
    except Exception:
        pass
    return turns

def find_session_workspace(session_id: str) -> Optional[str]:
    """Scan transcript.jsonl and brain artifacts for paths to determine workspace directory."""
    # 1. Try to scan transcript.jsonl
    transcript_path = os.path.join(BRAIN_DIR, session_id, ".system_generated", "logs", "transcript.jsonl")
    if os.path.exists(transcript_path):
        try:
            with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if not line.strip():
                        continue
                    if '"Cwd"' in line or '"DirectoryPath"' in line or '"TargetFile"' in line or '"AbsolutePath"' in line or ':/' in line or ':\\\\' in line:
                        obj = json.loads(line)
                        # Look at tool calls
                        for call in obj.get("tool_calls", []):
                            args = call.get("arguments", {})
                            # check Cwd of run_command
                            if "Cwd" in args:
                                p = args["Cwd"].replace("\\", "/")
                                if os.path.exists(p) and os.path.isdir(p):
                                    return p
                            # check DirectoryPath of list_dir
                            if "DirectoryPath" in args:
                                p = args["DirectoryPath"].replace("\\", "/")
                                if os.path.exists(p) and os.path.isdir(p):
                                    return p
                            # check TargetFile or AbsolutePath
                            for key in ["TargetFile", "AbsolutePath"]:
                                if key in args:
                                    p = os.path.dirname(args[key]).replace("\\", "/")
                                    # walk up to find a directory with .agents or .git or workspace root
                                    while len(p) > 3:
                                        if os.path.exists(os.path.join(p, ".agents")) or os.path.exists(os.path.join(p, ".git")):
                                            return p
                                        p = os.path.dirname(p).replace("\\", "/")
                        
                        # Look at USER_INPUT content for active workspace logs
                        if obj.get("type") == "USER_INPUT":
                            content = obj.get("content", "")
                            # Look for drive paths (e.g. C:\path or c:/path)
                            matches = re.findall(r"([a-zA-Z]:[/\\][^ \n\r\t\-]+)", content)
                            for m in matches:
                                p = m.replace("\\", "/").rstrip(":")
                                if os.path.exists(p):
                                    if not os.path.isdir(p):
                                        p = os.path.dirname(p)
                                    return p
        except Exception:
            pass
            
    # 2. Try implementation_plan.md, walkthrough.md, etc.
    for filename in ["implementation_plan.md", "walkthrough.md", "task.md"]:
        path = os.path.join(BRAIN_DIR, session_id, filename)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    # Find file links like file:///c:/path
                    matches = re.findall(r"file:///([a-zA-Z]:/[^ \n\r\t\"')#>\?]+)", content)
                    for m in matches:
                        p = m.rstrip("/")
                        while len(p) > 3:
                            if os.path.exists(os.path.join(p, ".agents")) or os.path.exists(os.path.join(p, ".git")):
                                return p
                            p = os.path.dirname(p).replace("\\", "/")
            except Exception:
                pass
    return None

app = FastAPI(title="Antigravity Remote Monitor")
security = HTTPBearer()
logger = logging.getLogger("antigravity_remote.server")

def file_executor_loop():
    while True:
        try:
            req_file = os.path.join(get_workspace_root(), "agent_execute_request.json")
            if os.path.exists(req_file):
                with open(req_file, "r", encoding="utf-8") as f:
                    req = json.load(f)
                
                cmd = req.get("command")
                cwd = req.get("cwd", get_workspace_root())
                
                try:
                    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=300)
                    resp = {
                        "status": "success" if result.returncode == 0 else "error",
                        "returncode": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr
                    }
                except Exception as e:
                    resp = {"status": "error", "error": str(e)}
                
                with open(os.path.join(get_workspace_root(), "agent_execute_response.json"), "w", encoding="utf-8") as f:
                    json.dump(resp, f)
                    
                try:
                    os.remove(req_file)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(1.0)

threading.Thread(target=file_executor_loop, daemon=True).start()


# Active authenticated session tokens — maps token -> creation timestamp
ACTIVE_SESSIONS: Dict[str, float] = {}
SESSION_TTL_SECONDS = 86400  # 24 hours

# Login rate limiting — maps IP -> (fail_count, first_fail_timestamp)
LOGIN_ATTEMPTS: Dict[str, Tuple[int, float]] = {}
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 900  # 15 minutes

# Initialize Task Runner
runner = TaskRunner()

# NOTE: Config (PIN, secret key) is read dynamically on each login attempt.
# Do NOT cache at module level — it causes PIN desync when the server is
# restarted with a fresh PIN via `antigravity-mobile start`.

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



class PlanStepModel(BaseModel):
    id: str
    text: str
    status: str = "pending"  # pending, active, completed, failed
    comments: List[Dict[str, Any]] = []

class PlanRequestModel(BaseModel):
    title: str
    goal: str
    steps: List[Dict[str, Any]] = []
    status: str = "pending"

class PlanStepApproveModel(BaseModel):
    step_id: str
    approved: bool

class PlanCommentModel(BaseModel):
    step_id: str
    comment: str

class QuestionRequestModel(BaseModel):
    id: Optional[str] = None
    question: str
    options: List[str] = []
    is_multi_select: bool = False

class QuestionResponseModel(BaseModel):
    question_id: str
    answers: List[str]

class BTWRequestModel(BaseModel):
    category: str = "note"
    title: str
    content: str

class ActionRunModel(BaseModel):
    action_name: str
    command: Optional[str] = None

class TargetModel(BaseModel):
    workspace_path: Optional[str] = None
    active_file: Optional[str] = None

# settings paths
ANTIGRAVITY_SETTINGS_PATH = os.path.expandvars(r"%APPDATA%\Antigravity IDE\User\settings.json")

def get_antigravity_model() -> str:
    if os.path.exists(ANTIGRAVITY_SETTINGS_PATH):
        try:
            with open(ANTIGRAVITY_SETTINGS_PATH, "r") as f:
                settings = json.load(f)
                val = settings.get("antigravity.modelSelection") or settings.get("antigravity.model")
                if val:
                    return val
        except Exception:
            pass
    return "gemini-3-5-flash-high"

def set_antigravity_model(model: str):
    success = False
    if os.path.exists(ANTIGRAVITY_SETTINGS_PATH):
        try:
            with open(ANTIGRAVITY_SETTINGS_PATH, "r") as f:
                settings = json.load(f)
            settings["antigravity.modelSelection"] = model
            settings["antigravity.model"] = model
            with open(ANTIGRAVITY_SETTINGS_PATH, "w") as f:
                json.dump(settings, f, indent=4)
            success = True
        except Exception:
            pass
    return success

# In-memory limits database (matching Antigravity IDE models)
MODEL_LIMITS = {
    "gemini-3-6-flash-high": {"name": "Gemini 3.6 Flash (High)", "category": "gemini"},
    "gemini-3-6-flash-medium": {"name": "Gemini 3.6 Flash (Medium)", "category": "gemini"},
    "gemini-3-6-flash-low": {"name": "Gemini 3.6 Flash (Low)", "category": "gemini"},
    "gemini-3-5-flash-medium": {"name": "Gemini 3.5 Flash (Medium)", "category": "gemini"},
    "gemini-3-5-flash-high": {"name": "Gemini 3.5 Flash (High)", "category": "gemini"},
    "gemini-3-5-flash-low": {"name": "Gemini 3.5 Flash (Low)", "category": "gemini"},
    "gemini-3-1-pro-low": {"name": "Gemini 3.1 Pro (Low)", "category": "gemini"},
    "gemini-3-1-pro-high": {"name": "Gemini 3.1 Pro (High)", "category": "gemini"},
    "claude-sonnet-4-6": {"name": "Claude Sonnet 4.6 (Thinking)", "category": "claude"},
    "claude-opus-4-6": {"name": "Claude Opus 4.6 (Thinking)", "category": "claude"},
    "gpt-oss-120b": {"name": "GPT-OSS 120B (Medium)", "category": "claude"}
}

def get_system_limits():
    default_limits = {
        "gemini": {
            "weekly_used_pct": 100,
            "five_hour_used_pct": 76
        },
        "claude": {
            "weekly_used_pct": 67,
            "five_hour_used_pct": 100
        }
    }
    limits_file = os.path.join(get_workspace_root(), "ide_limits.json")
    if not os.path.exists(limits_file):
        try:
            with open(limits_file, "w") as f:
                json.dump(default_limits, f, indent=4)
        except Exception:
            pass
        return default_limits
        
    try:
        with open(limits_file, "r") as f:
            data = json.load(f)
            for cat in ["gemini", "claude"]:
                if cat not in data:
                    data[cat] = default_limits[cat]
                else:
                    for key in ["weekly_used_pct", "five_hour_used_pct"]:
                        if key not in data[cat]:
                            data[cat][key] = default_limits[cat][key]
            return data
    except Exception:
        return default_limits

# State managers
REMOTE_PROMPT = {"id": "", "prompt": "", "status": "idle", "response": ""}
REMOTE_PROMPTS_HISTORY = []
REMOTE_APPROVAL = {"status": "idle", "type": "", "target": "", "decision": ""}

# Start with empty plan — real plans are read from the brain directory
REMOTE_PLAN: Dict[str, Any] = {}

REMOTE_QUESTIONS: List[Dict[str, Any]] = []
# Start with empty BTW list — no fabricated notes
REMOTE_BTW: List[Dict[str, Any]] = []
REMOTE_TARGET = {
    "workspace_path": get_workspace_root().replace("\\", "/"),
    "active_file": "antigravity_remote/server.py"
}



_GIT_DIFFS_CACHE = {"time": 0, "data": None, "root": ""}

def get_git_diffs() -> Dict[str, Any]:
    global _GIT_DIFFS_CACHE
    root = get_workspace_root()
    if time.time() - _GIT_DIFFS_CACHE["time"] < 5 and _GIT_DIFFS_CACHE["root"] == root:
        return _GIT_DIFFS_CACHE["data"]
        
    try:
        root = get_workspace_root()
        res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=root, timeout=3)
        lines = res.stdout.strip().splitlines()
        modified_files = []
        for line in lines:
            if line:
                raw_code = line[:2]
                status_code = raw_code.strip()
                filename = line[3:].strip()
                
                # Map porcelain status code to human readable state badge
                if '?' in raw_code:
                    state_code = 'U'
                    state_label = 'Untracked'
                elif 'A' in raw_code:
                    state_code = 'A'
                    state_label = 'Added'
                elif 'D' in raw_code:
                    state_code = 'D'
                    state_label = 'Deleted'
                elif 'R' in raw_code:
                    state_code = 'R'
                    state_label = 'Renamed'
                elif 'M' in raw_code:
                    state_code = 'M'
                    state_label = 'Modified'
                else:
                    state_code = status_code or 'M'
                    state_label = 'Modified'

                modified_files.append({
                    "status": status_code,
                    "state": state_code,
                    "state_label": state_label,
                    "file": filename
                })
        
        diff_res = subprocess.run(["git", "diff", "--stat"], capture_output=True, text=True, cwd=root, timeout=3)
        branch = get_git_branch(root)
        result = {
            "is_git": True,
            "branch": branch,
            "modified_files": modified_files,
            "diff_stat": diff_res.stdout.strip()
        }
        _GIT_DIFFS_CACHE = {"time": time.time(), "data": result, "root": root}
        return result
    except Exception:
        result = {
            "is_git": False,
            "branch": "unknown",
            "modified_files": [],
            "diff_stat": "Git workspace status unavailable."
        }
        _GIT_DIFFS_CACHE = {"time": time.time(), "data": result, "root": root}
        return result

def get_all_chat_turns() -> List[Dict[str, Any]]:
    turns = []
    for item in REMOTE_PROMPTS_HISTORY:
        turns.append({
            "id": f"remote_{item['sender']}_{hash(item['text'])}",
            "sender": item["sender"],
            "text": item["text"]
        })
            
    # Also include the currently executing or pending REMOTE_PROMPT if it's not yet in the history
    if REMOTE_PROMPT.get("prompt") and REMOTE_PROMPT.get("status") in ("pending", "executing"):
        has_prompt = any(t.get("sender") == "user" and REMOTE_PROMPT["prompt"] in t.get("text", "") for t in turns)
        if not has_prompt:
            turns.append({
                "id": "remote_user_prompt_active",
                "sender": "user",
                "text": REMOTE_PROMPT["prompt"]
            })
            
    return turns

def _is_token_valid(token: str) -> bool:
    """Check if a session token exists and has not expired."""
    if token not in ACTIVE_SESSIONS:
        return False
    created_at = ACTIVE_SESSIONS[token]
    if time.time() - created_at > SESSION_TTL_SECONDS:
        # Token expired — remove it
        ACTIVE_SESSIONS.pop(token, None)
        return False
    return True

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    token = credentials.credentials
    if not _is_token_valid(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token

def verify_ws_token(token: str) -> bool:
    return _is_token_valid(token)

def sync_plan_from_file():
    global REMOTE_PLAN
    plan_file = os.path.join(get_workspace_root(), "implementation_plan.md")
    if not os.path.exists(plan_file):
        return
    
    try:
        try:
            with open(plan_file, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeError:
            with open(plan_file, "r", encoding="utf-16", errors="replace") as f:
                content = f.read()
            
        title = "Implementation Plan"
        goal = "Execution plan from implementation_plan.md"
        
        steps = []
        step_idx = 0
        
        lines = content.splitlines()
        for line in lines:
            line_strip = line.strip()
            if line_strip.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
                text = line_strip
                for marker in ["- [ ]", "- [x]", "- [/]", "* [ ]", "* [x]", "* [/]", "-", "*"]:
                    if text.startswith(marker):
                        text = text[len(marker):].strip()
                        break
                
                if not text or text.startswith("#"):
                    continue
                    
                status = "pending"
                if "[x]" in line_strip or "✅" in line_strip:
                    status = "completed"
                elif "[/]" in line_strip or "🔄" in line_strip:
                    status = "active"
                    
                steps.append({
                    "id": f"step_{step_idx}",
                    "text": text[:200],
                    "status": status,
                    "comments": []
                })
                step_idx += 1
                
        if steps:
            existing_comments = {}
            if REMOTE_PLAN and "steps" in REMOTE_PLAN:
                for s in REMOTE_PLAN["steps"]:
                    if s.get("comments"):
                        existing_comments[s["text"]] = s["comments"]
                        
            for s in steps:
                if s["text"] in existing_comments:
                    s["comments"] = existing_comments[s["text"]]
                    
            REMOTE_PLAN = {
                "id": "plan_file",
                "title": title,
                "goal": goal,
                "status": REMOTE_PLAN.get("status", "pending") if REMOTE_PLAN else "pending",
                "steps": steps
            }
    except Exception as e:
        logger.error(f"Error parsing implementation_plan.md: {e}")

# Server Status helper
def get_system_status():
    global REMOTE_PROMPT, REMOTE_APPROVAL
    sync_plan_from_file()
    cpu = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage('.').percent
    
    # Read agent_status.json if it exists in the workspace
    agent_status = "idle"
    agent_task = ""
    status_file = os.path.join(get_workspace_root(), "agent_status.json")
    if os.path.exists(status_file):
        try:
            with open(status_file, "r") as f:
                data = json.load(f)
                agent_status = data.get("status", "idle")
                agent_task = data.get("task", "")
        except Exception:
            pass

    response_file = os.path.join(get_workspace_root(), "agent_response.json")
    if os.path.exists(response_file):
        try:
            with open(response_file, "r", encoding="utf-8") as f:
                res_data = json.load(f)
                REMOTE_PROMPT["status"] = res_data.get("status", "completed")
                REMOTE_PROMPT["response"] = res_data.get("output", "")
                
                # Append to chat history so the response appears in mobile conversation
                response_text = res_data.get("output", "Task completed.")
                REMOTE_PROMPTS_HISTORY.append({
                    "sender": "agent",
                    "text": response_text
                })
            os.remove(response_file)
        except Exception:
            pass

    req_file = os.path.join(get_workspace_root(), "agent_approval_request.json")
    if os.path.exists(req_file):
        try:
            with open(req_file, "r", encoding="utf-8") as f:
                req_data = json.load(f)
                REMOTE_APPROVAL = {
                    "status": "pending",
                    "type": req_data.get("type", "command"),
                    "target": req_data.get("target", ""),
                    "decision": ""
                }
            os.remove(req_file)
        except Exception:
            pass

    q_req_file = os.path.join(get_workspace_root(), "agent_question_request.json")
    if os.path.exists(q_req_file):
        try:
            with open(q_req_file, "r", encoding="utf-8") as f:
                q_data = json.load(f)
                q_id = q_data.get("id") or f"q_{int(time.time()*1000)}"
                if not any(q["id"] == q_id for q in REMOTE_QUESTIONS):
                    REMOTE_QUESTIONS.append({
                        "id": q_id,
                        "question": q_data.get("question", ""),
                        "options": q_data.get("options", []),
                        "is_multi_select": q_data.get("is_multi_select", False),
                        "status": "pending",
                        "answers": []
                    })
            os.remove(q_req_file)
        except Exception:
            pass

    agent_logs = ""
    log_file = os.path.join(get_workspace_root(), "agent_execution.log")
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                non_empty = [l.strip() for l in lines if l.strip()]
                if non_empty:
                    agent_logs = non_empty[-1]
        except Exception:
            pass

    return {
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "agent_status": agent_status,
        "agent_task": agent_task,
        "agent_logs": agent_logs,
        "timestamp": datetime.datetime.now().isoformat()
    }

# Directory Tree helper
def get_workspace_tree(path=None):
    if path is None:
        path = get_workspace_root()
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
def login(req: LoginRequest, request: Request):
    # Rate limiting: track failed attempts per client IP
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    if client_ip in LOGIN_ATTEMPTS:
        fail_count, first_fail_time = LOGIN_ATTEMPTS[client_ip]
        # Reset window if lockout period has passed
        if now - first_fail_time > LOGIN_LOCKOUT_SECONDS:
            del LOGIN_ATTEMPTS[client_ip]
        elif fail_count >= MAX_LOGIN_ATTEMPTS:
            remaining = int(LOGIN_LOCKOUT_SECONDS - (now - first_fail_time))
            logger.warning(f"Login rate limit hit for IP {client_ip}")
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed attempts. Try again in {remaining} seconds."
            )

    # Read config fresh on every login so we always use the current PIN
    config = get_config()
    current_pin = config.get("pin")
    if req.pin == current_pin:
        import secrets
        # Clear failed attempts on successful login
        LOGIN_ATTEMPTS.pop(client_ip, None)
        token = secrets.token_hex(16)
        ACTIVE_SESSIONS[token] = now
        return {"token": token}

    # Track failed attempt
    if client_ip in LOGIN_ATTEMPTS:
        fail_count, first_fail_time = LOGIN_ATTEMPTS[client_ip]
        LOGIN_ATTEMPTS[client_ip] = (fail_count + 1, first_fail_time)
    else:
        LOGIN_ATTEMPTS[client_ip] = (1, now)

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
    try:
        task = runner.add_task(req.command)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
    task = runner.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "logs": runner.get_task_logs(task_id),
        "status": task.status,
        "exit_code": task.exit_code
    }

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
        logger.error(f"Failed to write summary.md: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate task summary")

@app.get("/api/file")
def get_api_file_content(path: str, token: str = Depends(verify_token)):
    # Use realpath to resolve symlinks and prevent symlink escape attacks
    real_base = os.path.realpath(".")
    real_path = os.path.realpath(path)
    # Ensure the resolved path is strictly within the workspace (+ os.sep prevents prefix tricks)
    if not (real_path == real_base or real_path.startswith(real_base + os.sep)):
        raise HTTPException(status_code=403, detail="Access denied: outside workspace boundary")
    
    if not os.path.exists(real_path) or os.path.isdir(real_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        with open(real_path, "r", encoding="utf-8", errors="ignore") as f:
            return {"path": path, "content": f.read()}
    except Exception as e:
        logger.error(f"Error reading file {real_path}: {e}")
        raise HTTPException(status_code=500, detail="Internal error reading file")

@app.get("/api/agent/status")
def get_agent_status(token: str = Depends(verify_token)):
    current_model = get_antigravity_model()
    return {
        "current_model": current_model,
        "model_details": MODEL_LIMITS.get(current_model, {"name": current_model, "category": "gemini"}),
        "all_models": MODEL_LIMITS,
        "ide_limits": get_system_limits(),
        "remote_prompt": REMOTE_PROMPT,
        "remote_approval": REMOTE_APPROVAL
    }

@app.post("/api/agent/model")
def switch_model_endpoint(req: ModelRequest, token: str = Depends(verify_token)):
    if req.model not in MODEL_LIMITS:
        raise HTTPException(status_code=400, detail="Invalid model selection")
    success = set_antigravity_model(req.model)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to write Antigravity settings")
    
    # Generate and execute VBScript to simulate typing '/model {model_name}'
    try:
        model_name = MODEL_LIMITS[req.model]["name"]
        # Escape parenthesis for SendKeys
        safe_model_name = model_name.replace("(", "{(}").replace(")", "{)}")
        import tempfile
        import subprocess
        vbs_script = (
            "Set WshShell = WScript.CreateObject(\"WScript.Shell\")\n"
            "WshShell.AppActivate \"Antigravity IDE\"\n"
            "WScript.Sleep 500\n"
            "WshShell.AppActivate \"Visual Studio Code\"\n"
            "WScript.Sleep 500\n"
            f"WshShell.SendKeys \"/model {safe_model_name}{{ENTER}}\"\n"
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=".vbs", mode="w", encoding="utf-8") as f:
            f.write(vbs_script)
            vbs_path = f.name
        subprocess.run(["cscript.exe", "//Nologo", vbs_path], timeout=5)
        import os as _os
        try:
            _os.unlink(vbs_path)
        except Exception:
            pass
    except Exception as e:
        print("Failed to simulate model switch keystrokes:", e)
        
    return {"status": "success", "model": req.model}

def kill_tunnel():
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            cmd_str = " ".join(cmdline).lower()
            if 'localtunnel' in cmd_str or 'lt' in cmd_str:
                proc.terminate()
        except Exception:
            pass

@app.post("/api/shutdown")
def shutdown_endpoint(token: str = Depends(verify_token)):
    loop = asyncio.get_event_loop()
    loop.call_later(0.5, kill_tunnel)
    return {"status": "success", "message": "Tunnel shutting down..."}

@app.post("/api/agent/prompt")
def post_remote_prompt(req: PromptRequest, token: str = Depends(verify_token)):
    global REMOTE_PROMPT, REMOTE_PROMPTS_HISTORY, REMOTE_TARGET
    
    final_prompt = req.prompt
    # Automatically inject the critical workspace override if we have a target workspace
    if 'REMOTE_TARGET' in globals() and REMOTE_TARGET.get("workspace_path"):
        target_ws = REMOTE_TARGET["workspace_path"]
        prefix = f"[CRITICAL: YOU MUST PERFORM THIS TASK IN THE FOLLOWING DIRECTORY: {target_ws}]\n"
        if "[CRITICAL: YOU MUST PERFORM THIS TASK" not in final_prompt:
            final_prompt = prefix + final_prompt
            
    REMOTE_PROMPT = {
        "id": str(uuid.uuid4())[:8],
        "prompt": final_prompt,
        "status": "pending",
        "response": ""
    }
    
    # Append user prompt to history
    REMOTE_PROMPTS_HISTORY.append({
        "sender": "user",
        "text": req.prompt
    })
    
    return REMOTE_PROMPT

@app.get("/api/agent/prompt/check")
def check_remote_prompt(token: str = Depends(verify_token)):
    global REMOTE_PROMPT
    # Idempotent: return the active prompt as long as it's pending or executing
    # The daemon polls this repeatedly; don't lose the prompt on first read
    if REMOTE_PROMPT["status"] in ("pending", "executing"):
        if REMOTE_PROMPT["status"] == "pending":
            REMOTE_PROMPT["status"] = "executing"
        resp = dict(REMOTE_PROMPT)
        resp["workspace_path"] = get_workspace_root()
        return resp
    return {"status": "idle"}

@app.post("/api/agent/response")
def post_agent_response(req: ResponseRequest, token: str = Depends(verify_token)):
    global REMOTE_PROMPT, REMOTE_PROMPTS_HISTORY
    if REMOTE_PROMPT["status"] != "executing":
        raise HTTPException(status_code=400, detail="No active executing prompt")
    REMOTE_PROMPT["status"] = req.status
    REMOTE_PROMPT["response"] = req.output
    
    # Append agent response to history
    REMOTE_PROMPTS_HISTORY.append({
        "sender": "agent",
        "text": req.output
    })
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
        response_file = os.path.join(get_workspace_root(), "agent_approval_response.json")
        with open(response_file, "w", encoding="utf-8") as f:
            json.dump({"status": decision}, f)
    except Exception:
        pass
        
    return {"status": "success", "decision": decision}

# AG2R Extended API Routes

# 1. Plan Management Endpoints
@app.get("/api/agent/plan")
def get_plan(token: str = Depends(verify_token)):
    return REMOTE_PLAN

@app.post("/api/agent/plan")
def set_plan(req: PlanRequestModel, token: str = Depends(verify_token)):
    global REMOTE_PLAN
    REMOTE_PLAN = {
        "id": f"plan_{int(time.time())}",
        "title": req.title,
        "goal": req.goal,
        "status": req.status,
        "steps": req.steps
    }
    return REMOTE_PLAN

@app.post("/api/agent/plan/approve")
def approve_plan_step(req: PlanStepApproveModel, token: str = Depends(verify_token)):
    global REMOTE_PLAN
    for step in REMOTE_PLAN.get("steps", []):
        if step["id"] == req.step_id:
            step["status"] = "completed" if req.approved else "failed"
            return {"status": "success", "step": step}
    if req.step_id == "all":
        REMOTE_PLAN["status"] = "approved" if req.approved else "rejected"
        decision = "approved" if req.approved else "rejected"
        try:
            response_file = os.path.join(get_workspace_root(), "agent_approval_response.json")
            with open(response_file, "w", encoding="utf-8") as f:
                json.dump({"status": decision}, f)
        except Exception:
            pass
        return {"status": "success", "plan_status": REMOTE_PLAN["status"]}
    raise HTTPException(status_code=404, detail="Step not found")

@app.post("/api/agent/plan/comment")
def comment_plan_step(req: PlanCommentModel, token: str = Depends(verify_token)):
    global REMOTE_PLAN
    for step in REMOTE_PLAN.get("steps", []):
        if step["id"] == req.step_id:
            if "comments" not in step or not isinstance(step["comments"], list):
                step["comments"] = []
            step["comments"].append({
                "author": "mobile_user",
                "text": req.comment,
                "timestamp": int(time.time() * 1000)
            })
            
            # Write comment to implementation_plan.md in workspace root
            try:
                plan_file = os.path.join(get_workspace_root(), "implementation_plan.md")
                if os.path.exists(plan_file):
                    file_encoding = "utf-8"
                    try:
                        with open(plan_file, "r", encoding="utf-8") as f:
                            f.read(1)
                    except UnicodeDecodeError:
                        file_encoding = "utf-16"
                    with open(plan_file, "a", encoding=file_encoding) as f:
                        f.write(f"\n\n> [!NOTE]\n> **Mobile Feedback on '{step['text']}'**: {req.comment}\n")
            except Exception:
                pass
                
            return {"status": "success", "comments": step["comments"]}
    raise HTTPException(status_code=404, detail="Step not found")

# 2. Interactive Questions Endpoints
@app.get("/api/agent/questions")
def get_questions(token: str = Depends(verify_token)):
    return {"questions": REMOTE_QUESTIONS}

@app.post("/api/agent/questions/request")
def post_question_request(req: QuestionRequestModel, token: str = Depends(verify_token)):
    global REMOTE_QUESTIONS
    q_id = req.id or f"q_{int(time.time()*1000)}"
    q_obj = {
        "id": q_id,
        "question": req.question,
        "options": req.options,
        "is_multi_select": req.is_multi_select,
        "status": "pending",
        "answers": []
    }
    REMOTE_QUESTIONS.append(q_obj)
    return q_obj

@app.post("/api/agent/questions/response")
def post_question_response(req: QuestionResponseModel, token: str = Depends(verify_token)):
    global REMOTE_QUESTIONS
    for q in REMOTE_QUESTIONS:
        if q["id"] == req.question_id:
            q["status"] = "answered"
            q["answers"] = req.answers
                
            # Write to agent_question_response.json in workspace
            try:
                response_file = os.path.join(get_workspace_root(), "agent_question_response.json")
                with open(response_file, "w", encoding="utf-8") as f:
                    json.dump({
                        "question_id": req.question_id,
                        "answers": req.answers
                    }, f)
            except Exception:
                pass
                
            return {"status": "success", "question": q}
    raise HTTPException(status_code=404, detail="Question not found")

# 3. BTW (By The Way) Panel Endpoints
@app.get("/api/agent/btw")
def get_btw(token: str = Depends(verify_token)):
    return {"btw": REMOTE_BTW}

@app.post("/api/agent/btw")
def post_btw(req: BTWRequestModel, token: str = Depends(verify_token)):
    global REMOTE_BTW
    item = {
        "id": f"btw_{int(time.time()*1000)}",
        "category": req.category,
        "title": req.title,
        "content": req.content,
        "timestamp": int(time.time() * 1000)
    }
    REMOTE_BTW.insert(0, item)
    return item

@app.delete("/api/agent/btw/{btw_id}")
def delete_btw(btw_id: str, token: str = Depends(verify_token)):
    global REMOTE_BTW
    REMOTE_BTW = [b for b in REMOTE_BTW if b["id"] != btw_id]
    return {"status": "success"}

# 4. Actions Panel Endpoints
@app.get("/api/agent/actions")
def get_actions(token: str = Depends(verify_token)):
    return {
        "available_actions": [
            {"id": "run_pytest", "name": "Run Pytest Suite", "cmd": "python -m pytest"},
            {"id": "git_status", "name": "Git Status", "cmd": "git status"},
            {"id": "git_diff", "name": "Git Diff Summary", "cmd": "git diff --stat"},
            {"id": "check_server", "name": "Check Server Status", "cmd": "python -m antigravity_remote remote --status"},
            {"id": "custom", "name": "Run Custom Command", "cmd": ""}
        ]
    }

@app.post("/api/agent/actions/run")
def run_action(req: ActionRunModel, token: str = Depends(verify_token)):
    cmd = req.command
    if not cmd:
        if req.action_name == "run_pytest":
            cmd = "python -m pytest"
        elif req.action_name == "git_status":
            cmd = "git status"
        elif req.action_name == "git_diff":
            cmd = "git diff --stat"
        else:
            raise HTTPException(status_code=400, detail="Command not specified")
    task = runner.start_task(cmd)
    return {"status": "started", "task_id": task.id, "command": cmd}

_SIBLING_PROJECTS_CACHE = {"time": 0, "data": [], "root": ""}

def get_sibling_projects():
    """Discover project directories that contain .git — real git repositories only."""
    global _SIBLING_PROJECTS_CACHE
    current_root = get_workspace_root()
    if time.time() - _SIBLING_PROJECTS_CACHE["time"] < 10 and _SIBLING_PROJECTS_CACHE["root"] == current_root:
        return _SIBLING_PROJECTS_CACHE["data"]
        
    projects = []
    seen_paths = set()
    ignored = {"$recycle.bin", "$sysreset", "documents and settings", "program files",
                "program files (x86)", "programdata", "recovery", "system volume information",
                "windows", "node_modules", ".git", ".github", ".agents", "__pycache__",
                "venv", ".venv", "env", "logs", "dist", "build"}
    
    def add_if_git(dir_path, display_name=None):
        """Add directory to projects list only if it contains .git"""
        if not dir_path or not os.path.exists(dir_path):
            return
        norm = os.path.normpath(dir_path).lower()
        if norm in seen_paths:
            return
        git_dir = os.path.join(dir_path, ".git")
        if os.path.exists(git_dir):
            branch = get_git_branch(dir_path)
            name = display_name or os.path.basename(dir_path)
            projects.append({
                "name": name,
                "path": dir_path.replace("\\", "/"),
                "has_git": True,
                "branch": branch
            })
            seen_paths.add(norm)
    
    current_root = get_workspace_root()
    
    # 1. Current workspace itself (if it has .git)
    add_if_git(current_root)
    
    # 2. Sibling directories of the workspace root
    try:
        parent = os.path.dirname(current_root)
        if len(parent) > 3:  # Don't scan root directory
            for entry in os.scandir(parent):
                if entry.is_dir() and not entry.name.startswith("."):
                    if entry.name.lower() not in ignored:
                        add_if_git(entry.path)
    except Exception:
        pass
    
    # 3. Subdirectories of the workspace root (one level deep)
    try:
        for entry in os.scandir(current_root):
            if entry.is_dir() and not entry.name.startswith("."):
                if entry.name.lower() not in ignored:
                    add_if_git(entry.path, f"{os.path.basename(current_root)}/{entry.name}")
    except Exception:
        pass

    # 4. Check C:\tempo explicitly if it exists
    if os.path.exists("C:/tempo"):
        add_if_git("C:/tempo")

    _SIBLING_PROJECTS_CACHE = {"time": time.time(), "data": projects, "root": current_root}
    return projects

def browse_directory(path: str = None) -> Dict[str, Any]:
    """Browse a directory and return all subdirectories with metadata.
    Used for the full directory browser on the mobile UI."""
    ignored = {"$recycle.bin", "$sysreset", "documents and settings", "program files",
               "program files (x86)", "programdata", "recovery", "system volume information",
               "windows", "windows.old", "perflogs", "msocache",
               "node_modules", ".git", ".github", "__pycache__",
               "venv", ".venv", "env", ".env"}
    
    if not path:
        # Default: start from where the server was launched (cwd)
        path = os.path.abspath(os.getcwd())
    
    path = os.path.abspath(path)
    if not os.path.exists(path) or not os.path.isdir(path):
        return {"error": "Path not found", "path": path, "entries": [], "parent": None}
    
    parent = os.path.dirname(path)
    # Don't allow going above root
    if parent == path:
        parent = None
    
    entries = []
    try:
        for entry in os.scandir(path):
            if not entry.is_dir():
                continue
            name_lower = entry.name.lower()
            if name_lower in ignored:
                continue
            # Skip hidden directories (starting with .)
            if entry.name.startswith(".") and entry.name not in (".agents",):
                continue
            
            entry_info = {
                "name": entry.name,
                "path": entry.path.replace("\\", "/"),
                "has_git": os.path.exists(os.path.join(entry.path, ".git")),
                "has_agents": os.path.exists(os.path.join(entry.path, ".agents")),
            }
            if entry_info["has_git"]:
                entry_info["branch"] = get_git_branch(entry.path)
            entries.append(entry_info)
    except PermissionError:
        return {"path": path.replace("\\", "/"), "parent": parent.replace("\\", "/") if parent else None, "entries": [], "error": "Permission denied"}
    except Exception as e:
        return {"path": path.replace("\\", "/"), "parent": parent.replace("\\", "/") if parent else None, "entries": [], "error": str(e)}
    
    # Sort: git repos first, then alphabetically
    entries.sort(key=lambda x: (not x["has_git"], x["name"].lower()))
    
    return {
        "path": path.replace("\\", "/"),
        "parent": parent.replace("\\", "/") if parent else None,
        "entries": entries
    }

# 5. Target Selection Endpoints
@app.get("/api/agent/targets")
def get_targets(token: str = Depends(verify_token)):
    return {
        "workspace_path": REMOTE_TARGET.get("workspace_path"),
        "active_file": REMOTE_TARGET.get("active_file"),
        "projects": get_sibling_projects()
    }

@app.get("/api/browse")
def browse_endpoint(path: Optional[str] = None, token: str = Depends(verify_token)):
    """Browse directories for the full directory browser. Returns subdirectories with metadata."""
    return browse_directory(path)

def get_ide_executable() -> str:
    if os.name == "nt":
        user_profile = os.environ.get("USERPROFILE") or os.environ.get("HOME")
        if user_profile:
            cmd_path = os.path.join(user_profile, "AppData", "Local", "Programs", "Antigravity IDE", "bin", "antigravity-ide.cmd")
            if os.path.exists(cmd_path):
                return cmd_path
            exe_path = os.path.join(user_profile, "AppData", "Local", "Programs", "Antigravity IDE", "Antigravity IDE.exe")
            if os.path.exists(exe_path):
                return exe_path
    return "antigravity-ide"

def do_switch_workspace(target_path: str) -> bool:
    global REMOTE_TARGET, REMOTE_PROMPT, REMOTE_PROMPTS_HISTORY
    norm_path = target_path.replace("\\", "/")
    if os.path.exists(norm_path) and os.path.isdir(norm_path):
        # Reset prompt and prompt history so new prompts in new workspace start cleanly
        REMOTE_PROMPT = {"id": "", "prompt": "", "status": "idle", "response": ""}
        REMOTE_PROMPTS_HISTORY = []

        # Remove stale remote_prompt.json in current and target directories
        current_root = get_workspace_root()
        for root_dir in [current_root, norm_path]:
            p_file = os.path.join(root_dir, "remote_prompt.json")
            if os.path.exists(p_file):
                try:
                    os.remove(p_file)
                except Exception:
                    pass

        # Copy config.json, remote_mode.json, and ide_limits.json to target workspace
        for filename in ["config.json", "remote_mode.json", "ide_limits.json"]:
            src = os.path.join(current_root, filename)
            dst = os.path.join(norm_path, filename)
            if os.path.exists(src) and src != dst:
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass
        
        # Copy .agents directory (AGENTS.md + skills) so remote control works in the new workspace
        src_agents = os.path.join(current_root, ".agents")
        dst_agents = os.path.join(norm_path, ".agents")
        if os.path.exists(src_agents) and not os.path.exists(dst_agents):
            try:
                shutil.copytree(src_agents, dst_agents)
            except Exception:
                pass
        elif os.path.exists(src_agents) and os.path.exists(dst_agents):
            # Copy individual files that are missing (don't overwrite existing)
            for item in ["AGENTS.md"]:
                src_item = os.path.join(current_root, item)
                dst_item = os.path.join(norm_path, item)
                if os.path.exists(src_item) and not os.path.exists(dst_item):
                    try:
                        shutil.copy2(src_item, dst_item)
                    except Exception:
                        pass
            # Copy skills directory if missing
            src_skills = os.path.join(src_agents, "skills")
            dst_skills = os.path.join(dst_agents, "skills")
            if os.path.exists(src_skills) and not os.path.exists(dst_skills):
                try:
                    shutil.copytree(src_skills, dst_skills)
                except Exception:
                    pass
        
        # Also copy root-level AGENTS.md if it exists
        src_agents_md = os.path.join(current_root, ".agents", "AGENTS.md")
        dst_agents_md = os.path.join(norm_path, ".agents", "AGENTS.md")
        if os.path.exists(src_agents_md):
            os.makedirs(os.path.dirname(dst_agents_md), exist_ok=True)
            if not os.path.exists(dst_agents_md):
                try:
                    shutil.copy2(src_agents_md, dst_agents_md)
                except Exception:
                    pass
        
        # Ensure remote_mode.json exists and is enabled in target workspace
        dst_mode = os.path.join(norm_path, "remote_mode.json")
        if not os.path.exists(dst_mode):
            try:
                with open(dst_mode, "w") as f:
                    json.dump({"enabled": True}, f)
            except Exception:
                pass

        # Copy antigravity_remote package so the daemon can run locally
        src_remote = os.path.join(current_root, "antigravity_remote")
        dst_remote = os.path.join(norm_path, "antigravity_remote")
        if os.path.exists(src_remote) and not os.path.exists(dst_remote):
            try:
                shutil.copytree(src_remote, dst_remote)
            except Exception:
                pass
        elif os.path.exists(src_remote) and os.path.exists(dst_remote):
            # Update files if they exist
            for item in os.listdir(src_remote):
                if item.endswith(".py"):
                    try:
                        shutil.copy2(os.path.join(src_remote, item), os.path.join(dst_remote, item))
                    except Exception:
                        pass

        # Kill any running agent_daemon processes to prevent them from stealing prompts in the new workspace
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline') or []
                    cmd_str = " ".join(cmdline).lower()
                    if 'antigravity_remote.agent_daemon' in cmd_str:
                        proc.terminate()
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Failed to kill daemon: {e}")

        # Open the target workspace in a new IDE window
        try:
            ide_cmd = get_ide_executable()
            # On Windows, we use a list without shell=True to avoid cmd.exe quote stripping issues
            subprocess.Popen([ide_cmd, norm_path])
        except Exception as e:
            logger.error(f"Failed to open IDE: {e}")
            
        REMOTE_TARGET["workspace_path"] = norm_path
        
        # Inject the initialization prompt directly into the target workspace
        # This wakes up the agent in the new IDE so it can start the daemon server
        prefix = f"[CRITICAL: YOU MUST PERFORM THIS TASK IN THE FOLLOWING DIRECTORY: {norm_path}]\n"
        final_prompt = prefix + "start the daemon server"
        
        try:
            with open(os.path.join(norm_path, "remote_prompt.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "id": "init_" + str(uuid.uuid4())[:8],
                    "prompt": final_prompt,
                    "token": "local_init"
                }, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to inject init prompt: {e}")
            
        # Automate typing the prompt in the new IDE window and hitting Enter
        try:
            prefix = f"[CRITICAL: YOU MUST PERFORM THIS TASK IN THE FOLLOWING DIRECTORY: {norm_path}] "
            # Escape characters that have special meaning in SendKeys
            safe_text = (prefix + "start the daemon server").replace('"', '\\"').replace('[', '{[}').replace(']', '{]}')
            folder_name = os.path.basename(norm_path)
            
            vbs_script = (
                'On Error Resume Next\n'
                'Set WshShell = WScript.CreateObject("WScript.Shell")\n'
                'success = False\n'
                'For i = 1 To 20\n'
                f'    If WshShell.AppActivate("{folder_name} - Antigravity IDE") Then\n'
                '        success = True\n'
                '        Exit For\n'
                '    End If\n'
                f'    If WshShell.AppActivate("{folder_name} - Visual Studio Code") Then\n'
                '        success = True\n'
                '        Exit For\n'
                '    End If\n'
                '    WScript.Sleep 500\n'
                'Next\n'
                'If success Then\n'
                '    WScript.Sleep 1000\n'
                f'    WshShell.SendKeys "{safe_text}{{ENTER}}"\n'
                'End If\n'
                'Set objFSO = CreateObject("Scripting.FileSystemObject")\n'
                'objFSO.DeleteFile WScript.ScriptFullName\n'
            )
            
            import tempfile
            fd, vbs_path = tempfile.mkstemp(suffix=".vbs", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs_script)
                
            subprocess.Popen(["cscript.exe", "//Nologo", vbs_path])
        except Exception as e:
            logger.error(f"Failed to run VBScript for auto-start: {e}")
            
        try:
            with open(os.path.join(norm_path, "agent_response.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "status": "completed",
                    "output": "Directory has been switched successfully. You can continue your work."
                }, f)
        except Exception as e:
            logger.error(f"Failed to write agent response: {e}")
        
        # Add a note to chat history
        REMOTE_PROMPTS_HISTORY = [{
            "sender": "user",
            "text": "Switched workspace. Launched new IDE window and starting daemon..."
        }]
        return True
    return False

@app.post("/api/agent/targets")
def set_targets(req: TargetModel, token: str = Depends(verify_token)):
    global REMOTE_TARGET
    if req.workspace_path:
        do_switch_workspace(req.workspace_path)
    if req.active_file:
        REMOTE_TARGET["active_file"] = req.active_file.replace("\\", "/")
    return REMOTE_TARGET

# 6. Project Explorer & Code Review Endpoints
@app.get("/api/project/tree")
def get_tree(path: Optional[str] = None, token: str = Depends(verify_token)):
    target_path = path or get_workspace_root()
    return {"children": get_workspace_tree(target_path)}

@app.get("/api/project/file")
def get_project_file_content(path: str, token: str = Depends(verify_token)):
    try:
        if not os.path.isabs(path):
            full_path = os.path.join(get_workspace_root(), path)
        else:
            full_path = os.path.abspath(path)
        if not os.path.exists(full_path):
            raise HTTPException(status_code=404, detail="File not found")
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(50000)
        return {
            "path": path,
            "filename": os.path.basename(full_path),
            "content": content,
            "truncated": os.path.getsize(full_path) > 50000
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/project/diff")
def get_file_diff(path: str, token: str = Depends(verify_token)):
    try:
        norm_path = path.replace("\\", "/")
        status_res = subprocess.run(["git", "status", "--porcelain", "--", norm_path], capture_output=True, text=True, cwd=get_workspace_root(), timeout=3)
        is_untracked = status_res.stdout.startswith("??")
        
        if is_untracked:
            null_device = "NUL" if os.name == "nt" else "/dev/null"
            res = subprocess.run(["git", "diff", "--no-index", "--", null_device, norm_path], capture_output=True, text=True, cwd=get_workspace_root(), timeout=5)
            diff_output = res.stdout
        else:
            res = subprocess.run(["git", "diff", "HEAD", "--", norm_path], capture_output=True, text=True, cwd=get_workspace_root(), timeout=5)
            diff_output = res.stdout
            if not diff_output.strip():
                res = subprocess.run(["git", "diff", "--", norm_path], capture_output=True, text=True, cwd=get_workspace_root(), timeout=5)
                diff_output = res.stdout
                
        return {"path": norm_path, "diff": diff_output}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/project/diffs")
def get_diffs(token: str = Depends(verify_token)):
    return get_git_diffs()

@app.get("/api/project/diffs/detailed")
def get_detailed_diffs(token: str = Depends(verify_token)):
    """Get per-file status and diff content for code review."""
    diff_info = get_git_diffs()
    if not diff_info.get("is_git"):
        return {"is_git": False, "files": []}
    
    root = get_workspace_root()
    files_diffs = []
    
    for f_obj in diff_info.get("modified_files", [])[:30]:  # limit to 30 files max
        fpath = f_obj["file"]
        state_code = f_obj.get("state", "M")
        state_label = f_obj.get("state_label", "Modified")
        
        # Get diff for this file
        try:
            norm_path = fpath.replace("\\", "/")
            if state_code == 'U':
                null_device = "NUL" if os.name == "nt" else "/dev/null"
                res = subprocess.run(["git", "diff", "--no-index", "--", null_device, norm_path], capture_output=True, text=True, cwd=root, timeout=4)
                diff_text = res.stdout
                if not diff_text and os.path.exists(os.path.join(root, norm_path)):
                    try:
                        with open(os.path.join(root, norm_path), "r", encoding="utf-8", errors="ignore") as uf:
                            lines = uf.readlines()[:500]  # max 500 lines
                            diff_text = f"--- /dev/null\n+++ b/{norm_path}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join("+" + l for l in lines)
                    except Exception:
                        pass
            else:
                res = subprocess.run(["git", "diff", "HEAD", "--", norm_path], capture_output=True, text=True, cwd=root, timeout=4)
                diff_text = res.stdout
                if not diff_text.strip():
                    res = subprocess.run(["git", "diff", "--", norm_path], capture_output=True, text=True, cwd=root, timeout=4)
                    diff_text = res.stdout
            
            files_diffs.append({
                "file": fpath,
                "state": state_code,
                "state_label": state_label,
                "diff": diff_text or "No text changes detected."
            })
        except Exception as e:
            files_diffs.append({
                "file": fpath,
                "state": state_code,
                "state_label": state_label,
                "diff": f"Error loading diff: {str(e)}"
            })
            
    return {
        "is_git": True,
        "branch": diff_info.get("branch", "main"),
        "files": files_diffs
    }

# WebSockets
@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket, token: str = Query(...)):
    if not verify_ws_token(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
        
    await websocket.accept()
    try:
        while True:
            status_data = await asyncio.to_thread(get_system_status)
            status_data["tasks"] = [t.to_dict() for t in runner.list_tasks()]
            
            current_model = get_antigravity_model()
            status_data["agent_info"] = {
                "current_model": current_model,
                "model_details": MODEL_LIMITS.get(current_model, {"name": current_model, "category": "gemini"}),
                "all_models": MODEL_LIMITS,
                "ide_limits": get_system_limits(),
                "remote_prompt": REMOTE_PROMPT,
                "remote_approval": REMOTE_APPROVAL
            }
            status_data["chat_info"] = {
                "chat": get_all_chat_turns()
            }
            status_data["plan"] = REMOTE_PLAN
            status_data["questions"] = REMOTE_QUESTIONS
            status_data["btw"] = REMOTE_BTW
            
            target_projects = await asyncio.to_thread(get_sibling_projects)
            status_data["target"] = {
                "workspace_path": REMOTE_TARGET.get("workspace_path"),
                "active_file": REMOTE_TARGET.get("active_file"),
                "projects": target_projects
            }
            diffs = await asyncio.to_thread(get_git_diffs)
            status_data["diffs_summary"] = diffs
            status_data["git_branch"] = diffs.get("branch", "unknown")
            
            # Include latest brain plan info
            brain_plan = await asyncio.to_thread(get_latest_brain_plan)
            if brain_plan:
                status_data["brain_plan"] = {
                    "session_id": brain_plan.get("session_id"),
                    "title": brain_plan.get("title"),
                    "age": brain_plan.get("age"),
                    "has_content": True
                }
            else:
                status_data["brain_plan"] = None

            # Include latest brain walkthrough info
            brain_walkthrough = await asyncio.to_thread(get_latest_brain_walkthrough)
            if brain_walkthrough:
                status_data["brain_walkthrough"] = {
                    "session_id": brain_walkthrough.get("session_id"),
                    "title": brain_walkthrough.get("title"),
                    "age": brain_walkthrough.get("age"),
                    "has_content": True
                }
            else:
                status_data["brain_walkthrough"] = None
            
            await websocket.send_json(status_data)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        import traceback
        traceback.print_exc()
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

# 7. Brain Plan API Endpoints — real implementation plans from IDE brain directory
class BrainPlanCommentModel(BaseModel):
    session_id: str
    section_text: str
    comment: str

@app.get("/api/brain/plan")
def get_brain_plan_endpoint(token: str = Depends(verify_token)):
    """Get the latest implementation plan from the brain directory."""
    plan = get_latest_brain_plan()
    if not plan:
        return {"found": False}
    return {"found": True, **plan}

@app.get("/api/brain/plan/{session_id}")
def get_brain_plan_by_session(session_id: str, token: str = Depends(verify_token)):
    """Get implementation plan for a specific session."""
    content = read_brain_artifact(session_id, "implementation_plan.md")
    if not content:
        raise HTTPException(status_code=404, detail="Plan not found")
    # Extract title
    title = "Implementation Plan"
    for line in content.splitlines():
        if line.strip().startswith("# "):
            title = line.strip()[2:].strip()
            break
    plan_path = os.path.join(BRAIN_DIR, session_id, "implementation_plan.md")
    mtime = os.path.getmtime(plan_path) if os.path.exists(plan_path) else 0
    age_secs = time.time() - mtime
    if age_secs < 60:
        age_str = f"{int(age_secs)} seconds ago"
    elif age_secs < 3600:
        age_str = f"{int(age_secs / 60)} minutes ago"
    elif age_secs < 86400:
        age_str = f"{int(age_secs / 3600)} hours ago"
    else:
        age_str = f"{int(age_secs / 86400)} days ago"
    return {
        "found": True,
        "session_id": session_id,
        "title": title,
        "content": content,
        "age": age_str
    }

@app.get("/api/brain/sessions")
def get_brain_sessions_endpoint(token: str = Depends(verify_token)):
    """List all brain sessions that have artifacts."""
    return {"sessions": get_brain_sessions()}

@app.post("/api/brain/plan/comment")
def add_brain_plan_comment(req: BrainPlanCommentModel, token: str = Depends(verify_token)):
    """Add a comment to the implementation plan file in the brain directory."""
    plan_path = os.path.join(BRAIN_DIR, req.session_id, "implementation_plan.md")
    if not os.path.exists(plan_path):
        raise HTTPException(status_code=404, detail="Plan not found")
    try:
        file_encoding = "utf-8"
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                f.read(1)
        except UnicodeDecodeError:
            file_encoding = "utf-16"
        with open(plan_path, "a", encoding=file_encoding) as f:
            f.write(f"\n\n> [!NOTE]\n> **Mobile Review on '{req.section_text[:80]}'**: {req.comment}\n")
        return {"status": "success", "comment": req.comment}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/brain/plan/proceed")
def proceed_brain_plan(token: str = Depends(verify_token)):
    """Approve/proceed with the implementation plan — writes approval to workspace."""
    global REMOTE_APPROVAL
    # Write approval file to workspace for file-based protocol
    try:
        response_file = os.path.join(get_workspace_root(), "agent_approval_response.json")
        with open(response_file, "w", encoding="utf-8") as f:
            json.dump({"status": "approved"}, f)
    except Exception:
        pass
    REMOTE_APPROVAL = {"status": "approved", "type": "plan", "target": "implementation_plan", "decision": "approved"}
    return {"status": "success", "decision": "approved"}

@app.get("/api/brain/walkthrough")
def get_brain_walkthrough(token: str = Depends(verify_token)):
    """Get the latest walkthrough from the brain directory."""
    sessions = get_brain_sessions()
    for session in sessions:
        if session.get("has_walkthrough"):
            content = read_brain_artifact(session["id"], "walkthrough.md")
            if content:
                title = "Walkthrough"
                for line in content.splitlines():
                    if line.strip().startswith("# "):
                        title = line.strip()[2:].strip()
                        break
                return {"found": True, "session_id": session["id"], "title": title, "content": content}
    return {"found": False}

@app.get("/api/brain/walkthrough/{session_id}")
def get_brain_walkthrough_by_session(session_id: str, token: str = Depends(verify_token)):
    """Get walkthrough for a specific session."""
    content = read_brain_artifact(session_id, "walkthrough.md")
    if not content:
        raise HTTPException(status_code=404, detail="Walkthrough not found")
    title = "Walkthrough"
    for line in content.splitlines():
        if line.strip().startswith("# "):
            title = line.strip()[2:].strip()
            break
    return {"found": True, "session_id": session_id, "title": title, "content": content}

# 8. Conversation History Endpoints
@app.get("/api/history/sessions")
def get_history_sessions_endpoint(token: str = Depends(verify_token)):
    """List all past conversation sessions from brain directory with summary metadata."""
    sessions = get_conversation_sessions()
    return {"sessions": sessions}

@app.get("/api/history/session/{session_id}")
def get_history_transcript_endpoint(session_id: str, token: str = Depends(verify_token)):
    """Get the full parsed chat transcript for a specific past session."""
    turns = get_session_transcript(session_id)
    if not turns:
        raise HTTPException(status_code=404, detail="Session transcript not found")
    return {"session_id": session_id, "turns": turns}

@app.post("/api/history/session/{session_id}/continue")
def continue_history_session(session_id: str, token: str = Depends(verify_token)):
    """Restore the project workspace of a past conversation session to continue it.
    Auto-detects which directory the conversation belongs to and switches both IDE and mobile."""
    workspace_path = find_session_workspace(session_id)
    
    if not workspace_path:
        # Fallback: try to use the current workspace
        workspace_path = get_workspace_root()
    
    # Normalize the path
    workspace_path = workspace_path.replace("\\", "/")
    
    success = do_switch_workspace(workspace_path)
    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to switch to the session's workspace path: {workspace_path}"
        )
    
    # Load the conversation history from the brain transcript into the mobile chat
    turns = get_session_transcript(session_id)
    global REMOTE_PROMPTS_HISTORY
    REMOTE_PROMPTS_HISTORY = []
    for turn in turns:
        REMOTE_PROMPTS_HISTORY.append({
            "sender": turn["sender"],
            "text": turn["text"]
        })
    
    return {
        "status": "success",
        "workspace_path": workspace_path,
        "session_id": session_id,
        "turns_loaded": len(turns)
    }

# UI static file routes
@app.get("/")
def get_dashboard():
    dashboard_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(dashboard_path):
        return HTMLResponse(content=open(dashboard_path, "r", encoding="utf-8").read())
    return HTMLResponse(content="<h1>Dashboard index.html not found.</h1>")

@app.get("/favicon.ico")
def favicon():
    """Return empty favicon to prevent 404 spam in logs."""
    favicon_path = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    # Return an empty 204 No Content to silence browser favicon requests
    from fastapi.responses import Response
    return Response(status_code=204)

# Serve static folder
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

