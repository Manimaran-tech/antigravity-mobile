import os
import json
import uuid
import datetime
import queue
import subprocess
import threading
import time
from typing import Dict, List, Optional


class TaskQueueFullError(RuntimeError):
    pass

class Task:
    def __init__(self, command: str, id: Optional[str] = None, status: str = "pending", 
                 created_at: Optional[str] = None, started_at: Optional[str] = None, 
                 completed_at: Optional[str] = None, exit_code: Optional[int] = None, 
                 created_by: str = "remote"):
        self.id = id or str(uuid.uuid4())[:8]
        self.command = command
        self.status = status  # pending, running, completed, failed, cancelled
        self.created_at = created_at or datetime.datetime.now().isoformat()
        self.started_at = started_at
        self.completed_at = completed_at
        self.exit_code = exit_code
        self.created_by = created_by

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "exit_code": self.exit_code,
            "created_by": self.created_by
        }

class TaskRunner:
    """A bounded local runner: API calls enqueue; fixed workers execute."""
    def __init__(self, db_path: str = "tasks.json", logs_dir: str = "logs"):
        self.db_path = os.path.abspath(db_path)
        self.logs_dir = os.path.abspath(logs_dir)
        self.lock = threading.RLock()
        self.running_processes: Dict[str, subprocess.Popen] = {}
        self.max_concurrent_tasks = max(1, int(os.getenv("ANTIGRAVITY_MAX_CONCURRENT_TASKS", "1")))
        self.max_queued_tasks = max(1, int(os.getenv("ANTIGRAVITY_MAX_QUEUED_TASKS", "20")))
        self.max_log_read_bytes = max(1, int(os.getenv("ANTIGRAVITY_MAX_LOG_READ_BYTES", str(64 * 1024))))
        self._queue = queue.Queue(maxsize=self.max_queued_tasks)
        
        # Ensure directories exist
        os.makedirs(self.logs_dir, exist_ok=True)
        
        # Load or initialize DB
        self._load_db()
        for index in range(self.max_concurrent_tasks):
            threading.Thread(target=self._worker_loop, name=f"task-runner-{index}", daemon=True).start()

    def _load_db(self):
        with self.lock:
            if os.path.exists(self.db_path):
                try:
                    with open(self.db_path, "r") as f:
                        self.tasks_data = json.load(f)
                except Exception:
                    self.tasks_data = {}
            else:
                self.tasks_data = {}
                self._save_db_unlocked()

    def _save_db_unlocked(self):
        with open(self.db_path, "w") as f:
            json.dump(self.tasks_data, f, indent=2)

    def _save_db(self):
        with self.lock:
            self._save_db_unlocked()

    def get_task(self, task_id: str) -> Optional[Task]:
        with self.lock:
            data = self.tasks_data.get(task_id)
            if data:
                return Task(**data)
            return None

    def list_tasks(self) -> List[Task]:
        with self.lock:
            return [Task(**data) for data in self.tasks_data.values()]

    def add_task(self, command: str, created_by: str = "remote") -> Task:
        # Security: validate command input
        if not command or not command.strip():
            raise ValueError("Command cannot be empty")
        if len(command) > 2000:
            raise ValueError("Command exceeds maximum length of 2000 characters")
        # Reject null bytes and most ASCII control characters (allow \t, \n, \r)
        for ch in command:
            if ch == '\x00':
                raise ValueError("Command contains null bytes")
            if ord(ch) < 32 and ch not in ('\t', '\n', '\r'):
                raise ValueError("Command contains invalid control characters")

        task = Task(command=command, created_by=created_by)
        with self.lock:
            self.tasks_data[task.id] = task.to_dict()
            self._save_db_unlocked()
        return task

    def confirm_and_run(self, task_id: str) -> bool:
        with self.lock:
            task_data = self.tasks_data.get(task_id)
            if not task_data or task_data.get("status") != "pending":
                return False
            try:
                self._queue.put_nowait(task_id)
            except queue.Full:
                raise TaskQueueFullError(f"Task queue is full (maximum {self.max_queued_tasks} queued tasks).")
            task_data["status"] = "queued"
            self._save_db_unlocked()
        return True

    def _worker_loop(self):
        while True:
            task_id = self._queue.get()
            try:
                with self.lock:
                    task_data = self.tasks_data.get(task_id)
                    if not task_data or task_data.get("status") != "queued":
                        continue
                    task_data["status"] = "running"
                    task_data["started_at"] = datetime.datetime.now().isoformat()
                    self._save_db_unlocked()
                    command = task_data["command"]
                self._run_process(task_id, command)
            finally:
                self._queue.task_done()

    def queue_stats(self) -> dict:
        with self.lock:
            running = sum(1 for task in self.tasks_data.values() if task.get("status") == "running")
            queued = sum(1 for task in self.tasks_data.values() if task.get("status") == "queued")
        return {"running": running, "queued": queued, "max_concurrent": self.max_concurrent_tasks, "max_queued": self.max_queued_tasks}

    def _run_process(self, task_id: str, command: str):
        log_file_path = os.path.join(self.logs_dir, f"{task_id}.log")
        
        try:
            # Use shell=True for running arbitrary CLI / batch tools.
            # On Windows, we use cmd.exe or powershell. Let's make sure it handles stdout redirection.
            # Using Popen to capture logs continuously.
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=os.getcwd()
            )
            
            with self.lock:
                self.running_processes[task_id] = process

            # Write stdout in real-time to the task log file
            with open(log_file_path, "w", encoding="utf-8") as log_file:
                log_file.write(f"--- Task Execution Started: {datetime.datetime.now()} ---\n")
                log_file.write(f"Command: {command}\n\n")
                
                for line in process.stdout:
                    log_file.write(line)
                    log_file.flush()
            
            process.wait()
            exit_code = process.returncode
            status = "completed" if exit_code == 0 else "failed"
            
        except Exception as e:
            exit_code = -1
            status = "failed"
            with open(log_file_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"\nExecution Error: {str(e)}\n")
        
        # Clean up process reference and update task status
        with self.lock:
            if task_id in self.running_processes:
                del self.running_processes[task_id]
                
            task_data = self.tasks_data.get(task_id)
            if task_data:
                task = Task(**task_data)
                task.status = status
                task.completed_at = datetime.datetime.now().isoformat()
                task.exit_code = exit_code
                self.tasks_data[task.id] = task.to_dict()
                self._save_db_unlocked()

        with open(log_file_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n--- Task Execution Finished with exit code {exit_code} ---\n")

    def cancel_task(self, task_id: str) -> bool:
        with self.lock:
            task_data = self.tasks_data.get(task_id)
            if not task_data:
                return False
            
            task = Task(**task_data)
            
            if task.status == "pending":
                task.status = "cancelled"
                self.tasks_data[task.id] = task.to_dict()
                self._save_db_unlocked()
                return True
                
            elif task.status == "running":
                # Kill running process
                process = self.running_processes.get(task_id)
                if process:
                    try:
                        # Kill process tree on Windows
                        subprocess.run(f"taskkill /F /T /PID {process.pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        process.kill()
                
                task.status = "cancelled"
                task.completed_at = datetime.datetime.now().isoformat()
                self.tasks_data[task.id] = task.to_dict()
                self._save_db_unlocked()
                return True
                
            return False

    def get_task_logs(self, task_id: str) -> str:
        log_file_path = os.path.join(self.logs_dir, f"{task_id}.log")
        if os.path.exists(log_file_path):
            try:
                with open(log_file_path, "rb") as f:
                    f.seek(max(0, os.path.getsize(log_file_path) - self.max_log_read_bytes))
                    return f.read(self.max_log_read_bytes).decode("utf-8", errors="replace")
            except Exception as e:
                return f"Error reading logs: {str(e)}"
        return "No logs available or task has not started yet."

    def start_task(self, command: str, created_by: str = "remote") -> Task:
        task = self.add_task(command, created_by)
        self.confirm_and_run(task.id)
        return task

