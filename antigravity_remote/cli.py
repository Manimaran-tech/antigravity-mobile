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
    # Force a new config with a fresh PIN every time the server starts
    config = generate_config(force=True)
    
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

VERSION = "0.2.10"

# ANSI color codes
BLUE = "\033[38;5;39m"
CYAN = "\033[38;5;51m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"
GREEN = "\033[38;5;82m"
YELLOW = "\033[38;5;220m"
WHITE = "\033[97m"
MAGENTA = "\033[38;5;171m"

def print_banner():
    """Print the Antigravity Mobile welcome banner in blue."""
    banner = f"""{BLUE}
     █████╗ ███╗   ██╗████████╗██╗ ██████╗ ██████╗  █████╗ ██╗   ██╗██╗████████╗██╗   ██╗
    ██╔══██╗████╗  ██║╚══██╔══╝██║██╔════╝ ██╔══██╗██╔══██╗██║   ██║██║╚══██╔══╝╚██╗ ██╔╝
    ███████║██╔██╗ ██║   ██║   ██║██║  ███╗██████╔╝███████║██║   ██║██║   ██║    ╚████╔╝
    ██╔══██║██║╚██╗██║   ██║   ██║██║   ██║██╔══██╗██╔══██║╚██╗ ██╔╝██║   ██║     ╚██╔╝
    ██║  ██║██║ ╚████║   ██║   ██║╚██████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║   ██║      ██║
    ╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝   ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝   ╚═╝      ╚═╝
{CYAN}
    ███╗   ███╗ ██████╗ ██████╗ ██╗██╗     ███████╗
    ████╗ ████║██╔═══██╗██╔══██╗██║██║     ██╔════╝
    ██╔████╔██║██║   ██║██████╔╝██║██║     █████╗
    ██║╚██╔╝██║██║   ██║██╔══██╗██║██║     ██╔══╝
    ██║ ╚═╝ ██║╚██████╔╝██████╔╝██║███████╗███████╗
    ╚═╝     ╚═╝ ╚═════╝ ╚═════╝ ╚═╝╚══════╝╚══════╝{RESET}

    {WHITE}{BOLD}Antigravity Mobile{RESET} {DIM}v{VERSION}{RESET}
    {DIM}Control your AI coding agent from your phone.{RESET}
    {DIM}Monitor tasks, approve commands, switch models -- remotely.{RESET}
"""
    print(banner)

def print_quickstart():
    """Print quick-start guide after the banner."""
    guide = f"""
    {YELLOW}━━━ Quick Start ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}
    {DIM}If 'antigravity-mobile' is not in your PATH, you can run all commands{RESET}
    {DIM}by prefixing with 'python -m antigravity_remote' (e.g. python -m antigravity_remote setup).{RESET}

    {GREEN}1.{RESET} {WHITE}Setup{RESET}          {DIM}Generate config & security PIN{RESET}
       {CYAN}$ antigravity-mobile setup{RESET}

    {GREEN}2.{RESET} {WHITE}Start Server{RESET}   {DIM}Launch the mobile dashboard{RESET}
       {CYAN}$ antigravity-mobile start --host 0.0.0.0 --port 8000{RESET}

    {GREEN}3.{RESET} {WHITE}Get Public URL{RESET}  {DIM}Expose your server so your phone can reach it{RESET}
       {CYAN}$ npx localtunnel --port 8000{RESET}
       {DIM}Copy the URL it gives you (e.g. https://xyz.loca.lt){RESET}

    {GREEN}4.{RESET} {WHITE}Open on Phone{RESET}  {DIM}Visit the URL on your mobile browser & enter your PIN{RESET}

    {YELLOW}━━━ All Commands ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}

    {CYAN}setup{RESET}              {DIM}Interactive first-time setup wizard{RESET}
    {CYAN}init{RESET}               {DIM}Generate config & security PIN{RESET}
    {CYAN}start{RESET}              {DIM}Start the FastAPI dashboard server{RESET}
    {CYAN}remote --on{RESET}        {DIM}Enable remote monitoring mode{RESET}
    {CYAN}remote --off{RESET}       {DIM}Disable remote monitoring (saves tokens){RESET}
    {CYAN}remote --status{RESET}    {DIM}Check if remote mode is on or off{RESET}

    {YELLOW}━━━ Examples ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}

    {DIM}# Turn off remote mode when coding locally (saves tokens){RESET}
    {CYAN}$ antigravity-mobile remote --off{RESET}

    {DIM}# Turn it back on when leaving your desk{RESET}
    {CYAN}$ antigravity-mobile remote --on{RESET}

    {YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}
    {DIM}PyPI:   pip install antigravity-mobile{RESET}
    {DIM}GitHub: https://github.com/Manimaran-tech/antigravity-mobile{RESET}
"""
    print(guide)


def configure_windows_path():
    """Automatically add python scripts folder containing antigravity-mobile to user's system PATH on Windows."""
    if sys.platform != "win32":
        return False, "PATH configuration only supported on Windows"
        
    try:
        import winreg
        import ctypes
        import sysconfig
        
        # Get python scripts directory (where pip installs entry points)
        scripts_dir = os.path.abspath(sysconfig.get_path('scripts'))
        if not os.path.exists(scripts_dir):
            return False, f"Scripts directory not found: {scripts_dir}"
            
        # Open user environment key in registry
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
        
        try:
            current_path, data_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current_path = ""
            data_type = winreg.REG_EXPAND_SZ
            
        # Check if already exists in PATH
        paths = [p.strip() for p in current_path.split(';') if p.strip()]
        if any(os.path.abspath(p) == scripts_dir for p in paths):
            winreg.CloseKey(key)
            return True, "Scripts directory already in PATH"
            
        # Append and save
        new_path = current_path + (";" if current_path and not current_path.endswith(";") else "") + scripts_dir
        winreg.SetValueEx(key, "Path", 0, data_type, new_path)
        winreg.CloseKey(key)
        
        # Broadcast environment change message so active shell launchers pick it up (like explorer)
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment")
        
        return True, f"Added to PATH: {scripts_dir}"
    except Exception as e:
        return False, str(e)


def cmd_setup(args):
    """Interactive setup wizard that walks the user through first-time configuration."""
    print_banner()

    print(f"    {YELLOW}━━━ Setup Wizard ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print()

    # Step 1: Generate config
    print(f"    {GREEN}Step 1/4{RESET} {WHITE}Generating secure access PIN...{RESET}")
    config = generate_config(force=args.force if hasattr(args, 'force') else False)
    pin = config.get("pin")
    print(f"    {GREEN}✓{RESET} Config saved to {CYAN}{os.path.abspath(CONFIG_FILE)}{RESET}")
    print(f"    {GREEN}✓{RESET} Your access PIN: {YELLOW}{BOLD}{pin}{RESET}")
    print()

    # Step 2: Create .agents directory
    print(f"    {GREEN}Step 2/4{RESET} {WHITE}Setting up agent rules...{RESET}")
    agents_dir = os.path.join(os.getcwd(), ".agents", "skills", "remote_control")
    if os.path.exists(os.path.join(os.getcwd(), ".agents", "AGENTS.md")):
        print(f"    {GREEN}✓{RESET} Agent rules already configured in {CYAN}.agents/AGENTS.md{RESET}")
    else:
        os.makedirs(agents_dir, exist_ok=True)
        # Create full AGENTS.md with file-based approvals
        agents_md = os.path.join(os.getcwd(), ".agents", "AGENTS.md")
        with open(agents_md, "w", encoding="utf-8") as f:
            f.write("# Antigravity Remote Monitoring and Control Rules\n\n")
            f.write("Before performing any action, check if remote mode is enabled by reading `remote_mode.json` in the workspace root.\n")
            f.write('- **If `"enabled": false`**: Run normally and bypass all remote features (do NOT write status files, do NOT update log files, and do NOT use mobile confirmations).\n')
            f.write('- **If `"enabled": true`** (or if the file is missing/unreadable): Adhere to the following rules at all times:\n\n')
            f.write("1. **Always Stream Execution Logs to Mobile**:\n")
            f.write("   - At the start of **every** agent turn, write the current active task description to `agent_status.json`:\n")
            f.write('     `{"status": "working", "task": "[Brief description of what you are doing right now]"}`\n')
            f.write("   - Write or append your step-by-step developer thoughts and execution progress to `agent_execution.log`.\n")
            f.write("   - When a turn completes successfully, update `agent_status.json` to: `{\"status\": \"idle\", \"task\": \"\"}`.\n\n")
            f.write("2. **Always Route Confirmation Dialogs to Mobile (File-Based Protocol)**:\n")
            f.write("   - Before executing any command (via the `run_command` tool) that modifies state or runs scripts, you MUST obtain user confirmation from the mobile device.\n")
            f.write("   - To avoid triggering PC desktop dialogs, use the **silent file-based approval protocol**:\n")
            f.write("     1. Write the approval request to `agent_approval_request.json` in the workspace root:\n")
            f.write('        `{"type": "command", "target": "[Command or action to execute]"}`\n')
            f.write("     2. Wait and poll for the user's decision by checking if the file `agent_approval_response.json` exists in the workspace. Read/check it every 1.5 seconds (up to 300 seconds timeout).\n")
            f.write("     3. If `agent_approval_response.json` is detected:\n")
            f.write("        - Read the decision status: `{\"status\": \"approved\"}` or `{\"status\": \"rejected\"}`.\n")
            f.write("        - Delete `agent_approval_response.json` from the workspace.\n")
            f.write("        - If approved, proceed with the command. If rejected, abort and report the rejection.\n")
            f.write("     4. If the server is offline or the file-based handshake fails, run the CLI helper fallback:\n")
            f.write('        `python -m antigravity_remote.agent_approve --type command --target \"[Command to execute]\"`\n')
        print(f"    {GREEN}✓{RESET} Created {CYAN}.agents/AGENTS.md{RESET} with remote monitoring rules")
    print()

    # Step 3: Create remote_mode.json
    print(f"    {GREEN}Step 3/4{RESET} {WHITE}Enabling remote monitoring mode...{RESET}")
    with open("remote_mode.json", "w") as f:
        json.dump({"enabled": True}, f)
    print(f"    {GREEN}✓{RESET} Remote mode {GREEN}ENABLED{RESET}")
    print()

    # Step 4: Configure system PATH for CLI commands
    print(f"    {GREEN}Step 4/4{RESET} {WHITE}Configuring Windows User PATH...{RESET}")
    success, msg = configure_windows_path()
    if success:
        print(f"    {GREEN}✓{RESET} {msg}")
    else:
        print(f"    {YELLOW}⚠{RESET} Skip PATH config: {msg}")
    print()

    # Summary
    print(f"    {YELLOW}━━━ Setup Complete! ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print()
    print(f"    {WHITE}Next steps:{RESET}")
    print()
    print(f"    {GREEN}1.{RESET} {WHITE}Copy & Paste this prompt into your Antigravity IDE Chat panel:{RESET}")
    print(f"       {CYAN}──────────────────────────────────────────────────────────────────────────{RESET}")
    print(f"       Please check if my Python scripts directory (where antigravity-mobile is")
    print(f"       installed) is in my system/user PATH environment variable. If it is not,")
    print(f"       add it automatically so that I can run the antigravity-mobile command")
    print(f"       directly. After doing this, please remind me to start the FastAPI server")
    print(f"       and localtunnel manually in my terminal.")
    print(f"       ──────────────────────────────────────────────────────────────────────────{RESET}")
    print()
    print(f"    {GREEN}2.{RESET} Start the server manually in your PC terminal:")
    print(f"       {CYAN}$ antigravity-mobile start --host 0.0.0.0 --port 8000{RESET}")
    print()
    print(f"    {GREEN}3.{RESET} Expose to internet (so your phone can reach it):")
    print(f"       {CYAN}$ npx localtunnel --port 8000{RESET}")
    print()
    print(f"    {GREEN}4.{RESET} Open the URL on your phone & enter PIN: {YELLOW}{BOLD}{pin}{RESET}")
    print()


def main():
    # Enable ANSI colors and UTF-8 output on Windows
    if sys.platform == "win32":
        os.system("")  # Enables ANSI escape sequences in Windows terminal
        import io
        if hasattr(sys.stdout, 'buffer') and sys.stdout.buffer is not None:
            try:
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            except Exception:
                pass

    parser = argparse.ArgumentParser(
        description="Antigravity Mobile — Control your AI coding agent from your phone.",
        add_help=False
    )
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # setup command
    setup_parser = subparsers.add_parser("setup", help="Interactive first-time setup wizard")
    setup_parser.add_argument("--force", action="store_true", help="Force overwrite existing configuration")

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

    # help command
    subparsers.add_parser("help", help="Show this help message")

    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup(args)
    elif args.command == "init":
        cmd_init(args)
    elif args.command == "start":
        cmd_start(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "remote":
        cmd_remote(args)
    elif args.command == "help":
        print_banner()
        print_quickstart()
    else:
        # No command given — show the beautiful welcome banner
        print_banner()
        print_quickstart()

if __name__ == "__main__":
    main()
