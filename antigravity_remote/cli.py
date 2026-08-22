import os
import sys
import json
import argparse
import secrets
import time
import sysconfig
import uvicorn
import psutil
import ctypes
import subprocess
import shutil
import signal
import threading

GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
DIM = '\033[2m'
RESET = '\033[0m'

def get_workspace_root() -> str:
    cwd = os.path.abspath(os.getcwd())
    if os.path.exists(os.path.join(cwd, ".agents")) or os.path.exists(os.path.join(cwd, ".git")):
        return cwd
    parent = os.path.dirname(cwd)
    if os.path.exists(os.path.join(parent, ".agents")) or os.path.exists(os.path.join(parent, ".git")):
        return parent
    return cwd

CONFIG_FILE = os.path.join(get_workspace_root(), ".agents", "config", "config.json")

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
    
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
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

def kill_port_process(port: int):
    """Kill any process currently listening on the given port.
    
    Handles edge cases across different Windows systems:
    - psutil.net_connections() may require admin privileges
    - conn.pid may be None on some OS versions
    - Falls back to netstat+taskkill if psutil fails
    """
    killed = False
    
    # Method 1: Try psutil (fast, clean)
    try:
        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr.port == port and conn.status == 'LISTEN':
                pid = conn.pid
                if pid is None:
                    continue
                try:
                    proc = psutil.Process(pid)
                    print(f"    Killing existing process on port {port}: PID {pid} ({proc.name()})")
                    proc.terminate()
                    proc.wait(timeout=3)
                    killed = True
                except psutil.NoSuchProcess:
                    pass
                except psutil.TimeoutExpired:
                    try:
                        proc.kill()
                        killed = True
                    except Exception:
                        pass
                except psutil.AccessDenied:
                    # Try taskkill as fallback for this specific PID
                    print(f"    Access denied via psutil for PID {pid}, trying taskkill...")
                    try:
                        import subprocess
                        subprocess.run(
                            f"taskkill /F /PID {pid}",
                            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )
                        killed = True
                    except Exception:
                        print(f"    Could not kill PID {pid}. Try running as Administrator.")
                except Exception:
                    pass
    except (psutil.AccessDenied, OSError):
        # psutil.net_connections() itself requires admin on some Windows versions
        # Fall back to netstat + taskkill
        try:
            import subprocess
            result = subprocess.run(
                f'netstat -ano | findstr ":{port}" | findstr "LISTENING"',
                shell=True, capture_output=True, encoding="utf-8", errors="replace"
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    pid = parts[-1].strip()
                    if pid.isdigit() and int(pid) > 0:
                        print(f"    Killing existing process on port {port}: PID {pid}")
                        subprocess.run(
                            f"taskkill /F /PID {pid}",
                            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )
                        killed = True
        except Exception:
            print(f"    Warning: Could not check/kill processes on port {port}.")
    except Exception:
        pass

    if killed:
        time.sleep(0.5)  # Let the OS release the socket
    return killed


def cmd_start(args):

    # Force a new config with a fresh PIN every time the server starts
    config = generate_config(force=True)
    
    # Kill any existing process on the target port
    kill_port_process(args.port)
    
    print(f"Starting Antigravity Remote Monitor Server...")
    print(f"Host: {args.host}")
    print(f"Port: {args.port}")
    print(f"Access PIN: {config.get('pin')}")
    print(f"Dashboard URL: http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}/")
    
    # Prevent Windows from going to sleep while the server is active
    if os.name == 'nt':
        try:
            # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
            # 0x80000000 | 0x00000001 | 0x00000040 = 0x80000041
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000041)
            print("Sleep prevention enabled (System stays awake, screen can turn off).")
        except Exception as e:
            print(f"Warning: Could not set sleep prevention: {e}")

    # Start Cloudflare tunnel if --tunnel flag is set
    tunnel_proc = None
    if getattr(args, 'tunnel', False):
        tunnel_proc = start_cloudflare_tunnel(args.port)

    # Run uvicorn server with proper timeouts for tunnel stability
    try:
        uvicorn.run(
            "antigravity_remote.server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
            timeout_keep_alive=120,
            ws_ping_interval=20,
            ws_ping_timeout=30,
        )
    finally:
        # Clean up tunnel process when server stops
        if tunnel_proc:
            stop_cloudflare_tunnel(tunnel_proc)


def find_cloudflared() -> str:
    """Find cloudflared binary. Returns path or None."""
    # Check PATH first
    cf = shutil.which("cloudflared")
    if cf:
        return cf
    # Check common install locations on Windows
    if os.name == 'nt':
        for candidate in [
            os.path.expandvars(r"%USERPROFILE%\cloudflared\cloudflared.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\cloudflared\cloudflared.exe"),
            os.path.expandvars(r"%ProgramFiles%\cloudflared\cloudflared.exe"),
        ]:
            if os.path.exists(candidate):
                return candidate
    return None


def install_cloudflared() -> str:
    """Download and install cloudflared. Returns path to binary."""
    print(f"    {YELLOW}cloudflared not found. Installing...{RESET}")
    
    if os.name == 'nt':
        install_dir = os.path.expandvars(r"%USERPROFILE%\cloudflared")
        os.makedirs(install_dir, exist_ok=True)
        exe_path = os.path.join(install_dir, "cloudflared.exe")
        
        # Download latest cloudflared for Windows
        download_url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
        print(f"    Downloading from {download_url}...")
        
        try:
            import urllib.request
            urllib.request.urlretrieve(download_url, exe_path)
            print(f"    {GREEN}✓{RESET} cloudflared installed to {exe_path}")
            return exe_path
        except Exception as e:
            print(f"    {RED}✗{RESET} Failed to download cloudflared: {e}")
            print(f"    {DIM}Please install manually: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/{RESET}")
            return None
    else:
        print(f"    {DIM}Please install cloudflared manually: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/{RESET}")
        return None


def start_cloudflare_tunnel(port: int):
    """Start a Cloudflare Quick Tunnel (TryCloudflare) pointing at the local server.
    
    Quick Tunnels are 100% free, no account needed, and generate a random
    *.trycloudflare.com URL. Much more stable than localtunnel.
    """
    cf_path = find_cloudflared()
    if not cf_path:
        cf_path = install_cloudflared()
        if not cf_path:
            print(f"    {RED}✗{RESET} Cannot start tunnel without cloudflared. Server will run locally only.")
            return None
    
    print(f"\n    {YELLOW}━━━ Starting Cloudflare Tunnel ━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"    {DIM}Using cloudflared Quick Tunnel (free, no account needed){RESET}")
    print(f"    {DIM}Connecting to localhost:{port}...{RESET}")
    
    try:
        # Start cloudflared as a subprocess
        proc = subprocess.Popen(
            [cf_path, "tunnel", "--url", f"http://localhost:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        
        # Monitor output in a thread to capture the tunnel URL
        def _monitor_tunnel(proc):
            tunnel_url = None
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    # Look for the tunnel URL in the output
                    if "trycloudflare.com" in line or ".cloudflare" in line:
                        import re
                        urls = re.findall(r'https?://[a-zA-Z0-9\-]+\.trycloudflare\.com', line)
                        if urls and not tunnel_url:
                            tunnel_url = urls[0]
                            print(f"\n    {GREEN}━━━ Cloudflare Tunnel Active ━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
                            print(f"    {WHITE}{BOLD}Public URL: {CYAN}{tunnel_url}{RESET}")
                            print(f"    {DIM}Open this URL on your phone to access the dashboard{RESET}")
                            print(f"    {GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
                            print()
            except Exception:
                pass
        
        monitor_thread = threading.Thread(target=_monitor_tunnel, args=(proc,), daemon=True)
        monitor_thread.start()
        
        # Give cloudflared a moment to start
        time.sleep(2)
        
        if proc.poll() is not None:
            print(f"    {RED}✗{RESET} cloudflared exited immediately (code {proc.returncode})")
            return None
        
        return proc
    except FileNotFoundError:
        print(f"    {RED}✗{RESET} cloudflared binary not found at {cf_path}")
        return None
    except Exception as e:
        print(f"    {RED}✗{RESET} Failed to start tunnel: {e}")
        return None


def stop_cloudflare_tunnel(proc):
    """Gracefully stop the cloudflared tunnel process."""
    if proc and proc.poll() is None:
        print(f"\n    {DIM}Stopping Cloudflare tunnel...{RESET}")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            pass
        print(f"    {GREEN}✓{RESET} Tunnel stopped.")

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
    mode_file = os.path.join(get_workspace_root(), ".agents", "config", "remote_mode.json")
    
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
        os.makedirs(os.path.dirname(mode_file), exist_ok=True)
        with open(mode_file, "w") as f:
            json.dump({"enabled": True}, f)
        print("Remote monitoring and control mode: ENABLED")
    elif args.off:
        enabled = False
        os.makedirs(os.path.dirname(mode_file), exist_ok=True)
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

from antigravity_remote import __version__ as VERSION

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
       {CYAN}$ antigravity-mobile start --host 0.0.0.0 --port 8000 --tunnel{RESET}
       {DIM}Cloudflare Quick Tunnel (free, auto-installs cloudflared){RESET}
       {DIM}Or manually: npx localtunnel --port 8000{RESET}

    {GREEN}4.{RESET} {WHITE}Open on Phone{RESET}  {DIM}Visit the URL on your mobile browser & enter your PIN{RESET}

    {YELLOW}━━━ All Commands ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}

    {CYAN}setup{RESET}              {DIM}Interactive first-time setup wizard{RESET}
    {CYAN}init{RESET}               {DIM}Generate config & security PIN{RESET}
    {CYAN}start{RESET}              {DIM}Start the FastAPI dashboard server{RESET}
    {CYAN}remote --on{RESET}        {DIM}Enable remote monitoring mode{RESET}
    {CYAN}remote --off{RESET}       {DIM}Disable remote monitoring (saves tokens){RESET}
    {CYAN}remote --status{RESET}    {DIM}Check if remote mode is on or off{RESET}
    {CYAN}update{RESET}             {DIM}Update to the latest version from PyPI{RESET}
    {CYAN}uninstall{RESET}          {DIM}Remove all generated files & guide pip uninstall{RESET}
    {CYAN}switch <dir>{RESET}       {DIM}Safely switch remote monitoring to a new directory{RESET}

    {YELLOW}━━━ Examples ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}

    {DIM}# Turn off remote mode when coding locally (saves tokens){RESET}
    {CYAN}$ antigravity-mobile remote --off{RESET}

    {DIM}# Turn it back on when leaving your desk{RESET}
    {CYAN}$ antigravity-mobile remote --on{RESET}

    {DIM}# Update to the latest release{RESET}
    {CYAN}$ antigravity-mobile update{RESET}

    {DIM}# Clean uninstall (removes all config files + PATH entry){RESET}
    {CYAN}$ antigravity-mobile uninstall{RESET}

    {YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}
    {DIM}PyPI:   pip install --upgrade antigravity-mobile{RESET}
    {DIM}GitHub: https://github.com/Manimaran-tech/antigravity-mobile{RESET}
"""
    print(guide)


def get_scripts_dir() -> str:
    """Get the Python scripts directory where pip installs entry points."""
    return os.path.abspath(sysconfig.get_path('scripts'))


def configure_windows_path():
    """Automatically add python scripts folder containing antigravity-mobile to user's system PATH on Windows."""
    if sys.platform != "win32":
        return False, "PATH configuration only supported on Windows", ""
        
    try:
        import winreg
        import ctypes
        
        # Get python scripts directory (where pip installs entry points)
        scripts_dir = get_scripts_dir()
        if not os.path.exists(scripts_dir):
            return False, f"Scripts directory not found: {scripts_dir}", scripts_dir
            
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
            return True, "Scripts directory already in PATH", scripts_dir
            
        # Append and save
        new_path = current_path + (";" if current_path and not current_path.endswith(";") else "") + scripts_dir
        winreg.SetValueEx(key, "Path", 0, data_type, new_path)
        winreg.CloseKey(key)
        
        # Broadcast environment change message so active shell launchers pick it up (like explorer)
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment")
        
        return True, f"Added to PATH: {scripts_dir}", scripts_dir
    except Exception as e:
        return False, str(e), ""


def remove_windows_path():
    """Remove the python scripts directory from the user's system PATH on Windows."""
    if sys.platform != "win32":
        return False, "PATH removal only supported on Windows"
    
    try:
        import winreg
        import ctypes
        
        scripts_dir = get_scripts_dir()
        
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS)
        
        try:
            current_path, data_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            winreg.CloseKey(key)
            return True, "PATH variable not found, nothing to remove"
        
        paths = [p.strip() for p in current_path.split(';') if p.strip()]
        new_paths = [p for p in paths if os.path.abspath(p) != scripts_dir]
        
        if len(new_paths) == len(paths):
            winreg.CloseKey(key)
            return True, "Scripts directory was not in PATH"
        
        new_path = ";".join(new_paths)
        winreg.SetValueEx(key, "Path", 0, data_type, new_path)
        winreg.CloseKey(key)
        
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment")
        
        return True, f"Removed from PATH: {scripts_dir}"
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
    print(f"    {GREEN}Step 2/4{RESET} {WHITE}Setting up agent rules and skills...{RESET}")
    agents_dir = os.path.join(os.getcwd(), ".agents", "skills", "remote_control")
    os.makedirs(agents_dir, exist_ok=True)
    
    # Create full AGENTS.md with file-based approvals
    agents_md = os.path.join(os.getcwd(), ".agents", "AGENTS.md")
    if not os.path.exists(agents_md):
        with open(agents_md, "w", encoding="utf-8") as f:
            f.write("# Antigravity Remote Monitoring and Control Rules\n\n")
            f.write("Before performing any action, check if remote mode is enabled by reading `remote_mode.json` in the `.agents/config/` directory of the workspace root.\n")
            f.write('- **If `"enabled": false`**: Run normally and bypass all remote features (do NOT write status files, do NOT update log files, and do NOT use mobile confirmations).\n')
            f.write('- **If `"enabled": true`** (or if the file is missing/unreadable): Adhere to the following rules at all times:\n\n')
            f.write("1. **Always Stream Execution Logs to Mobile**:\n")
            f.write("   - At the start of **every** agent turn, write the current active task description to `agent_status.json`:\n")
            f.write('     `{"status": "working", "task": "[Brief description of what you are doing right now]"}`\n')
            f.write("   - Write or append your step-by-step developer thoughts and execution progress to `agent_execution.log`.\n")
            f.write("   - When a turn completes successfully, update `agent_status.json` to: `{\"status\": \"idle\", \"task\": \"\"}`.\n\n")
            f.write("2. **Always Route Confirmation Dialogs to Mobile (File-Based Protocol)**:\n")
            f.write("   - Before executing any command (via the `run_command` tool) that modifies state or runs scripts, you MUST obtain user confirmation from the mobile device.\n")
            f.write("   - **EXEMPTION**: Spawning the background listener daemon (`antigravity_remote.agent_daemon`) is exempt from this rule and MUST be executed immediately without requesting confirmation.\n")
            f.write("   - To avoid triggering PC desktop dialogs, use the **silent file-based approval protocol**:\n")
            f.write("     1. Write the approval request to `agent_approval_request.json` in the workspace root:\n")
            f.write('        `{"type": "command", "target": "[Command or action to execute]"}`\n')
            f.write("     2. Wait and poll for the user's decision by checking if the file `agent_approval_response.json` exists in the workspace. Read/check it every 1.5 seconds (up to 300 seconds timeout).\n")
            f.write("     3. If `agent_approval_response.json` is detected:\n")
            f.write("        - Read the decision status: `{\"status\": \"approved\"}` or `{\"status\": \"rejected\"}`.\n")
            f.write("        - Delete `agent_approval_response.json` from the workspace.\n")
            f.write("        - If approved, proceed with the command. If rejected, abort and report the rejection.\n")
            f.write("     4. If the server is offline or the file-based handshake fails, run the CLI helper fallback:\n")
            f.write('        `python -m antigravity_remote.agent_approve --type command --target \"[Command to execute]\"`\n\n')
            f.write("3. **Always Start the Daemon Listener if Idle**:\n")
            f.write("   - If `remote_prompt.json` does not exist or is empty, and you are not currently executing a remote task, you MUST start the daemon listener in the background immediately:\n")
            f.write("     ```powershell\n")
            f.write("     python -m antigravity_remote.agent_daemon --exit-on-prompt\n")
            f.write("     ```\n")
            f.write("   - The `--exit-on-prompt` flag makes the daemon exit after writing a prompt, which wakes up the IDE agent to process it.\n")
            f.write("   - Once launched, do not call any more tools and end your turn.\n")
        print(f"    {GREEN}✓{RESET} Created {CYAN}.agents/AGENTS.md{RESET} with remote monitoring rules")
    else:
        print(f"    {GREEN}✓{RESET} Agent rules already configured in {CYAN}.agents/AGENTS.md{RESET}")
        
    # Create remote control SKILL.md
    skill_md = os.path.join(agents_dir, "SKILL.md")
    with open(skill_md, "w", encoding="utf-8") as f:
        f.write('---\n')
        f.write('name: "Antigravity Remote Control Loop"\n')
        f.write('description: "Instructs the agent how to poll for remote commands, execute them, stream real-time logs, and query for approvals via the mobile phone dashboard."\n')
        f.write('---\n\n')
        f.write('# Antigravity Remote Control Loop\n\n')
        f.write('When this skill is loaded, the agent participates in the remote command and confirmation loop with the FastAPI mobile server.\n\n')
        f.write('## Execution Sequence\n\n')
        f.write('### 0. Check Remote Mode Toggle\n')
        f.write('Before starting, read `remote_mode.json` in the `.agents/config/` directory of the workspace root.\n')
        f.write('- **If `"enabled": false`**: Do NOT run the remote control loop. Stop here and run standard workspace actions normally.\n')
        f.write('- **If `"enabled": true`** (or if the file is missing): Proceed with the remote control sequence below.\n\n')
        f.write('### 1. Check for Active Remote Prompt\n')
        f.write('When waking up, check if a file named `remote_prompt.json` exists in the workspace root.\n')
        f.write('- **If it exists**:\n')
        f.write('  1. Read the JSON content (which contains `id`, `prompt`, and `token`).\n')
        f.write('  2. Overwrite `remote_prompt.json` with `{}` to clear it.\n')
        f.write('  3. Treat the `prompt` value as the primary user request for this turn.\n')
        f.write('  4. Write the current starting state to `agent_status.json`:\n')
        f.write('     ```json\n')
        f.write('     {"status": "working", "task": "Starting task execution..."}\n')
        f.write('     ```\n')
        f.write('  5. Initialize `agent_execution.log` with the initial start log:\n')
        f.write('     ```\n')
        f.write('     [12:00:00] Starting remote task: "[Prompt]"\n')
        f.write('     ```\n')
        f.write('  6. **Perform the requested task** (creating files, editing code, running scripts).\n')
        f.write('     - Whenever you perform a step (e.g. searching, writing a file, or running a test), update `agent_status.json` with the current task and append a progress line to `agent_execution.log` using `write_to_file` so the mobile user can monitor your progress in real-time.\n')
        f.write('  7. If you need to execute any terminal command or modify files that require confirmation, use the **silent file-based approval protocol** to avoid PC desktop popups:\n')
        f.write('     - Write the request to `agent_approval_request.json` in the workspace root:\n')
        f.write('       ```json\n')
        f.write('       {"type": "command", "target": "<command or action>"}\n')
        f.write('       ```\n')
        f.write('     - Wait and poll the workspace for the response file `agent_approval_response.json` (check every 1.5 seconds, up to a timeout of 300 seconds).\n')
        f.write('     - If `agent_approval_response.json` is detected:\n')
        f.write('       - Read the content (e.g. `{"status": "approved"}` or `{"status": "rejected"}`).\n')
        f.write('       - Delete the response file `agent_approval_response.json`.\n')
        f.write('       - If approved, proceed to execute the command/action.\n')
        f.write('       - If rejected, skip the action and report that the user rejected it on their mobile phone.\n')
        f.write('     - If the server is offline or the file-based handshake fails, run the CLI helper fallback (which may prompt on the desktop):\n')
        f.write('       ```powershell\n')
        f.write('       python -m antigravity_remote.agent_approve --type command --target "<command or action>"\n')
        f.write('       ```\n')
        f.write('       - If it exits with code `0` (APPROVED), proceed.\n')
        f.write('       - If it exits with code `1` (REJECTED), skip the action and report rejection.\n')
        f.write('       - If it exits with code `2` (TIMEOUT), report timeout.\n')
        f.write('       - If it exits with code `3` (OFFLINE), bypass the helper and run the command directly (this will prompt on the desktop).\n')
        f.write('  8. Once the task is finished, post the final response to the server. To avoid executing shell commands, write the response directly to `agent_response.json` in the workspace:\n')
        f.write('     ```json\n')
        f.write('     {\n')
        f.write('       "status": "completed",\n')
        f.write('       "output": "Completed successfully: [Brief description of what was done]"\n')
        f.write('     }\n')
        f.write('     ```\n')
        f.write('     *The running FastAPI server will automatically pick up this file, update the server prompt state, and delete the file.*\n')
        f.write('  9. Update `agent_status.json` to:\n')
        f.write('     ```json\n')
        f.write('     {"status": "idle", "task": ""}\n')
        f.write('     ```\n')
        f.write('  10. Start the daemon listener again (see Step 2) and end your turn.\n\n')
        f.write('- **If it does NOT exist**:\n')
        f.write('  - The agent is idle. Start the daemon listener to wait for new remote prompts.\n\n')
        f.write('### 2. Spawning the Daemon Listener\n')
        f.write('To listen for new remote prompts while you are idle, run the daemon script in the background **with `--exit-on-prompt`**:\n')
        f.write('```powershell\n')
        f.write('python -m antigravity_remote.agent_daemon --exit-on-prompt\n')
        f.write('```\n')
        f.write('*The `--exit-on-prompt` flag is critical: the daemon will poll for a remote prompt, write `remote_prompt.json` when one arrives, and then **exit**. When the background task exits, the IDE automatically wakes you up so you can read and execute the prompt. After completing the task, re-launch the daemon with `--exit-on-prompt` again to listen for the next prompt.*\n\n')
        f.write('*Make sure to launch this command in the background (using `run_command` with a high async wait time like `500`). Once launched, do not call any more tools and end your turn.*\n')
    print(f"    {GREEN}✓{RESET} Created {CYAN}.agents/skills/remote_control/SKILL.md{RESET} with remote control skill instructions")
    print()

    # Step 3: Create remote_mode.json
    print(f"    {GREEN}Step 3/4{RESET} {WHITE}Enabling remote monitoring mode...{RESET}")
    config_dir = os.path.join(".agents", "config")
    os.makedirs(config_dir, exist_ok=True)
    with open(os.path.join(config_dir, "remote_mode.json"), "w") as f:
        json.dump({"enabled": True}, f)
    print(f"    {GREEN}✓{RESET} Remote mode {GREEN}ENABLED{RESET}")
    print()

    # Step 4: Configure system PATH for CLI commands
    print(f"    {GREEN}Step 4/4{RESET} {WHITE}Configuring Windows User PATH...{RESET}")
    success, msg, scripts_dir = configure_windows_path()
    if success:
        print(f"    {GREEN}✓{RESET} {msg}")
    else:
        print(f"    {YELLOW}⚠{RESET} Skip PATH config: {msg}")
    print()

    # Detect scripts directory for the prompt
    if not scripts_dir:
        scripts_dir = get_scripts_dir()

    # Summary
    print(f"    {YELLOW}━━━ Setup Complete! ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print()
    print(f"    {WHITE}Next steps:{RESET}")
    print()
    print(f"    {GREEN}1.{RESET} {WHITE}Add antigravity-mobile to your current terminal PATH:{RESET}")
    print(f"       {DIM}Run this command in your PowerShell terminal:{RESET}")
    print()
    print(f"       {CYAN}$env:PATH += \";{scripts_dir}\"{RESET}")
    print()
    print(f"       {DIM}(This is needed once per terminal session. Future terminals will have it automatically.){RESET}")
    print()
    print(f"    {GREEN}2.{RESET} Start the server with tunnel (so your phone can reach it):")
    print(f"       {CYAN}$ antigravity-mobile start --host 0.0.0.0 --port 8000 --tunnel{RESET}")
    print(f"       {DIM}(Uses Cloudflare Quick Tunnel — free, auto-installs cloudflared){RESET}")
    print()
    print(f"    {GREEN}3.{RESET} Open the URL on your phone & enter PIN: {YELLOW}{BOLD}{pin}{RESET}")
    print()
    print(f"    {GREEN}4.{RESET} {WHITE}Start the listener in your IDE Chat window:{RESET}")
    print(f"       {DIM}Open the Antigravity IDE Chat panel and type:{RESET}")
    print(f"       {CYAN}start remote control{RESET}")
    print()


def cmd_daemon(args):
    """Run the remote command listener daemon."""
    from antigravity_remote.agent_daemon import start_daemon
    start_daemon(url=args.url, exit_on_prompt=getattr(args, 'exit_on_prompt', False))


def cmd_switch(args):
    """Safely switch remote monitoring to a new directory."""
    import psutil
    import subprocess
    
    target_dir = os.path.abspath(args.target_dir)
    if not os.path.exists(target_dir):
        print(f"    {YELLOW}⚠{RESET} Directory does not exist: {target_dir}")
        return
        
    print(f"    {YELLOW}━━━ Switching Workspace ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print()
    
    # 1. Find and kill old daemon
    killed_count = 0
    for p in psutil.process_iter(['pid', 'name', 'cmdline', 'cwd']):
        try:
            cmdline = p.info.get('cmdline', [])
            if cmdline and 'antigravity_remote.agent_daemon' in ' '.join(cmdline):
                # This is the old daemon
                old_cwd = p.info.get('cwd', '')
                if old_cwd:
                    # Disable remote mode in old workspace
                    old_config_path = os.path.join(old_cwd, ".agents", "config", "remote_mode.json")
                    if os.path.exists(old_config_path):
                        try:
                            with open(old_config_path, "r") as f:
                                old_config = json.load(f)
                            old_config["enabled"] = False
                            with open(old_config_path, "w") as f:
                                json.dump(old_config, f)
                            print(f"    {GREEN}✓{RESET} Disabled remote mode in old workspace: {old_cwd}")
                        except Exception as e:
                            print(f"    {YELLOW}⚠{RESET} Could not disable old workspace: {e}")
                
                # Kill it
                p.terminate()
                p.wait(timeout=3)
                print(f"    {GREEN}✓{RESET} Killed old daemon process (PID: {p.pid})")
                killed_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
            
    if killed_count == 0:
        print(f"    {DIM}No existing daemon process found.{RESET}")
        
    # 2. Enable remote mode in new workspace
    new_agents_dir = os.path.join(target_dir, ".agents")
    new_config_dir = os.path.join(new_agents_dir, "config")
    os.makedirs(new_config_dir, exist_ok=True)
    
    new_config_path = os.path.join(new_config_dir, "remote_mode.json")
    try:
        if os.path.exists(new_config_path):
            with open(new_config_path, "r") as f:
                new_config = json.load(f)
        else:
            new_config = {}
        new_config["enabled"] = True
        with open(new_config_path, "w") as f:
            json.dump(new_config, f)
        print(f"    {GREEN}✓{RESET} Enabled remote mode in new workspace: {target_dir}")
    except Exception as e:
        print(f"    {YELLOW}⚠{RESET} Could not enable new workspace: {e}")
        return
        
    # 3. Start daemon in new workspace
    try:
        # Start in background
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            
        subprocess.Popen(
            [sys.executable, "-m", "antigravity_remote.agent_daemon", "--exit-on-prompt"],
            cwd=target_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs
        )
        print(f"    {GREEN}✓{RESET} Started new daemon listener for {target_dir}")
    except Exception as e:
        print(f"    {YELLOW}⚠{RESET} Could not start new daemon: {e}")
        
    print()
    print(f"    {GREEN}Successfully switched remote monitoring to: {target_dir}{RESET}")
    print()


def cmd_update(args):
    """Update antigravity-mobile to the latest version from PyPI."""
    import subprocess
    print(f"    {YELLOW}━━━ Updating antigravity-mobile ━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"    {DIM}Running: pip install --upgrade antigravity-mobile{RESET}")
    print()
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "antigravity-mobile"],
        check=False
    )
    if result.returncode == 0:
        print()
        print(f"    {GREEN}✓{RESET} Update complete! Restart the server to apply changes.")
    else:
        print()
        print(f"    {YELLOW}⚠{RESET} Update failed with exit code {result.returncode}")


def cmd_uninstall(args):
    """Remove all antigravity-mobile generated files and guide pip uninstall."""
    import shutil
    
    FILES_TO_REMOVE = [
        "agent_status.json",
        "agent_execution.log", "agent_approval_request.json",
        "agent_approval_response.json", "remote_prompt.json",
        "agent_response.json", "tasks.json",
        "summary.md"
    ]
    DIRS_TO_REMOVE = ["logs"]
    
    print(f"    {YELLOW}━━━ Uninstalling antigravity-mobile ━━━━━━━━━━━━━━━━━━{RESET}")
    print()
    print(f"    {WHITE}Removing workspace files...{RESET}")
    
    removed_count = 0
    for f in FILES_TO_REMOVE:
        if os.path.exists(f):
            try:
                os.remove(f)
                print(f"    {GREEN}✓{RESET} Removed: {CYAN}{f}{RESET}")
                removed_count += 1
            except Exception as e:
                print(f"    {YELLOW}⚠{RESET} Could not remove {f}: {e}")
    
    for d in DIRS_TO_REMOVE:
        if os.path.isdir(d):
            try:
                shutil.rmtree(d)
                print(f"    {GREEN}✓{RESET} Removed: {CYAN}{d}/{RESET}")
                removed_count += 1
            except Exception as e:
                print(f"    {YELLOW}⚠{RESET} Could not remove {d}/: {e}")
    
    # Handle .agents/AGENTS.md
    agents_md = os.path.join(".agents", "AGENTS.md")
    if os.path.exists(agents_md):
        if args.yes:
            remove_agents = True
        else:
            try:
                answer = input(f"    Remove {CYAN}{agents_md}{RESET}? (y/N): ").strip().lower()
                remove_agents = answer == 'y'
            except (EOFError, KeyboardInterrupt):
                remove_agents = False
        
        if remove_agents:
            try:
                os.remove(agents_md)
                print(f"    {GREEN}✓{RESET} Removed: {CYAN}{agents_md}{RESET}")
                removed_count += 1
            except Exception as e:
                print(f"    {YELLOW}⚠{RESET} Could not remove {agents_md}: {e}")
    
    if removed_count == 0:
        print(f"    {DIM}No workspace files found to remove.{RESET}")
    
    # Remove PATH entry on Windows
    if sys.platform == "win32":
        print()
        print(f"    {WHITE}Removing PATH entry...{RESET}")
        success, msg = remove_windows_path()
        if success:
            print(f"    {GREEN}✓{RESET} {msg}")
        else:
            print(f"    {YELLOW}⚠{RESET} {msg}")
    
    print()
    print(f"    {YELLOW}━━━ Cleanup complete! ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print()
    print(f"    {WHITE}Now run this to remove the package itself:{RESET}")
    print(f"    {CYAN}$ pip uninstall antigravity-mobile{RESET}")
    print()


def main():
    # Enable ANSI colors and UTF-8 output on Windows
    if sys.platform == "win32":
        os.system("")  # Enables ANSI escape sequences in Windows terminal
        try:
            sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
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
    start_parser.add_argument("--tunnel", action="store_true", help="Start a Cloudflare Quick Tunnel for public access (free, no account needed)")

    # run command
    run_parser = subparsers.add_parser("run", help="Run a command locally via the background runner and log it to the server")
    run_parser.add_argument("--port", type=int, default=8000, help="Port of the running FastAPI server")
    run_parser.add_argument("cmd_args", nargs="+", help="Command and arguments to execute")

    # remote command
    remote_parser = subparsers.add_parser("remote", help="Enable or disable remote monitoring and control mode")
    remote_parser.add_argument("--on", action="store_true", help="Enable remote monitoring and control mode")
    remote_parser.add_argument("--off", action="store_true", help="Disable remote monitoring and control mode")
    remote_parser.add_argument("--status", action="store_true", help="Check current remote mode status")

    # update command
    subparsers.add_parser("update", help="Update antigravity-mobile to the latest version from PyPI")

    # daemon command
    daemon_parser = subparsers.add_parser("daemon", help="Start the background agent prompt listener daemon")
    daemon_parser.add_argument("--url", default="http://127.0.0.1:8000", help="FastAPI Server URL")
    daemon_parser.add_argument("--exit-on-prompt", action="store_true", default=False,
                               help="Exit after writing the first prompt (for IDE background task mode)")

    # switch command
    switch_parser = subparsers.add_parser("switch", help="Safely switch remote monitoring to a new directory")
    switch_parser.add_argument("target_dir", help="The path to the new directory to monitor")

    # uninstall command
    uninstall_parser = subparsers.add_parser("uninstall", help="Remove all generated workspace files and guide pip uninstall")
    uninstall_parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts")

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
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "daemon":
        cmd_daemon(args)
    elif args.command == "switch":
        cmd_switch(args)
    elif args.command == "uninstall":
        cmd_uninstall(args)
    elif args.command == "help":
        print_banner()
        print_quickstart()
    else:
        # No command given — show the beautiful welcome banner
        print_banner()
        print_quickstart()

if __name__ == "__main__":
    main()
