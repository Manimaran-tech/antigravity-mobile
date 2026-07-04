import os
import sys
import json
import argparse
import secrets
import uvicorn

CONFIG_FILE = "config.json"

def generate_config(force=False) -> dict:
    if os.path.exists(CONFIG_FILE) and not force:
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass

    # Generate new secure PIN and secret key
    pin = "".join(secrets.choice("0123456789") for _ in range(6))
    secret_key = secrets.token_hex(32)
    
    config = {
        "pin": pin,
        "secret_key": secret_key
    }
    
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
        
    print(f"Generated new configuration in {os.path.abspath(CONFIG_FILE)}")
    print(f"----------------------------------------")
    print(f"Access PIN: {pin}")
    print(f"----------------------------------------")
    print(f"Please use this PIN to authenticate your mobile device.")
    return config

def get_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return generate_config()
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return generate_config(force=True)

def cmd_init(args):
    generate_config(force=args.force)

def cmd_start(args):
    # Ensure config exists
    config = get_config()
    
    print(f"Starting Antigravity Remote Monitor Server...")
    print(f"Host: {args.host}")
    print(f"Port: {args.port}")
    print(f"Access PIN: {config.get('pin')}")
    print(f"Dashboard URL: http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}/")
    
    # Run uvicorn server
    # We pass the module path as a string to support reloading
    uvicorn.run(
        "antigravity_remote.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    )

def cmd_run(args):
    # Get config PIN
    config = get_config()
    pin = config.get("pin")
    
    base_url = f"http://127.0.0.1:{args.port}"
    
    import urllib.request
    import urllib.error
    
    # 1. Login
    login_url = f"{base_url}/api/login"
    login_data = json.dumps({"pin": pin}).encode("utf-8")
    req = urllib.request.Request(login_url, data=login_data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            res = json.loads(response.read().decode("utf-8"))
            token = res.get("token", "")
    except Exception as e:
        print(f"Error: Server not running or unreachable at {base_url}. Make sure to start the server first.")
        print(f"Details: {e}")
        sys.exit(1)
        
    if not token:
        print("Error: Authentication failed.")
        sys.exit(1)
        
    # 2. Schedule command
    cmd = " ".join(args.cmd_args)
    schedule_url = f"{base_url}/api/schedule"
    schedule_data = json.dumps({"command": cmd}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    req = urllib.request.Request(schedule_url, data=schedule_data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            task = json.loads(response.read().decode("utf-8"))
            task_id = task.get("id")
    except Exception as e:
        print(f"Error scheduling command: {e}")
        sys.exit(1)
        
    # 3. Confirm and start command
    confirm_url = f"{base_url}/api/tasks/{task_id}/confirm"
    req = urllib.request.Request(confirm_url, data=b"", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            pass
    except Exception as e:
        print(f"Error starting command: {e}")
        sys.exit(1)
        
    # 4. Stream logs to PC console until complete
    print(f"Task {task_id} running: '{cmd}'")
    
    logs_url = f"{base_url}/api/tasks/{task_id}/logs"
    req = urllib.request.Request(logs_url, headers=headers)
    
    import time
    printed_offset = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                log_data = json.loads(response.read().decode("utf-8"))
                content = log_data.get("logs", "")
                status = log_data.get("status", "pending")
                exit_code = log_data.get("exit_code")
                
                if len(content) > printed_offset:
                    print(content[printed_offset:], end="", flush=True)
                    printed_offset = len(content)
                    
                if status in ("completed", "failed", "cancelled"):
                    if exit_code is not None:
                        sys.exit(exit_code)
                    sys.exit(0)
        except Exception:
            pass
        time.sleep(0.5)

def cmd_remote(args):
    mode_file = "remote_mode.json"
    
    # Load current status
    enabled = True
    if os.path.exists(mode_file):
        try:
            with open(mode_file, "r") as f:
                enabled = json.load(f).get("enabled", True)
        except Exception:
            pass
            
    if args.on:
        enabled = True
        with open(mode_file, "w") as f:
            json.dump({"enabled": True}, f)
        print("Remote monitoring and control mode: ENABLED")
    elif args.off:
        enabled = False
        with open(mode_file, "w") as f:
            json.dump({"enabled": False}, f)
        # Clear files so they are not left behind
        for fn in ("agent_status.json", "agent_execution.log", "agent_approval_request.json", "agent_approval_response.json"):
            if os.path.exists(fn):
                try:
                    os.remove(fn)
                except Exception:
                    pass
        print("Remote monitoring and control mode: DISABLED")
    elif args.status:
        print(f"Remote monitoring and control mode is currently: {'ENABLED' if enabled else 'DISABLED'}")
    else:
        print(f"Remote monitoring and control mode is currently: {'ENABLED' if enabled else 'DISABLED'}")

def main():
    parser = argparse.ArgumentParser(
        description="Antigravity Remote Monitor and Task Scheduler CLI Tool"
    )
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize configuration and security PIN")
    init_parser.add_argument("--force", action="store_true", help="Force overwrite existing configuration")

    # start command
    start_parser = subparsers.add_parser("start", help="Start the FastAPI dashboard server")
    start_parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (e.g., 0.0.0.0 for local network access)")
    start_parser.add_argument("--port", type=int, default=8000, help="Port to run the web server on")
    start_parser.add_argument("--reload", action="store_true", help="Enable hot reloading for development")

    # run command
    run_parser = subparsers.add_parser("run", help="Run a command locally via the background runner and log it to the server")
    run_parser.add_argument("--port", type=int, default=8000, help="Port of the running FastAPI server")
    run_parser.add_argument("cmd_args", nargs="+", help="Command and arguments to execute")

    # remote command
    remote_parser = subparsers.add_parser("remote", help="Enable or disable remote monitoring and control mode")
    remote_parser.add_argument("--on", action="store_true", help="Enable remote monitoring and control mode")
    remote_parser.add_argument("--off", action="store_true", help="Disable remote monitoring and control mode")
    remote_parser.add_argument("--status", action="store_true", help="Check current remote mode status")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "start":
        cmd_start(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "remote":
        cmd_remote(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
