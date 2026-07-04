import os
import json
import uuid
import datetime
import subprocess
import threading
from typing import Dict, List, Optional

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
    def __init__(self, db_path: str = "tasks.json", logs_dir: str = "logs"):
        self.db_path = os.path.abspath(db_path)
        self.logs_dir = os.path.abspath(logs_dir)
        self.lock = threading.Lock()
        self.running_processes: Dict[str, subprocess.Popen] = {}
        
        # Ensure directories exist
        os.makedirs(self.logs_dir, exist_ok=True)
        
        # Load or initialize DB
        self._load_db()

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
        task = Task(command=command, created_by=created_by)
        with self.lock:
            self.tasks_data[task.id] = task.to_dict()
            self._save_db_unlocked()
        return task

    def confirm_and_run(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task or task.status != "pending":
            return False

        task.status = "running"
        task.started_at = datetime.datetime.now().isoformat()
        with self.lock:
            self.tasks_data[task.id] = task.to_dict()
            self._save_db_unlocked()

        # Start execution in background thread
        thread = threading.Thread(target=self._run_process, args=(task.id, task.command))
        thread.daemon = True
        thread.start()
        return True

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
                text=True,
                bufsize=1,
                universal_newlines=True
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
                with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception as e:
                return f"Error reading logs: {str(e)}"
        return "No logs available or task has not started yet."
