import os
import sys
import json
import time
import urllib.request
import urllib.error
import argparse

def get_workspace_root() -> str:
    cwd = os.path.abspath(os.getcwd())
    if os.path.exists(os.path.join(cwd, ".agents")) or os.path.exists(os.path.join(cwd, ".git")):
        return cwd
    parent = os.path.dirname(cwd)
    if os.path.exists(os.path.join(parent, ".agents")) or os.path.exists(os.path.join(parent, ".git")):
        return parent
    return cwd

def get_auth_token(base_url: str, pin: str) -> str:
    login_url = f"{base_url}/api/login"
    data = json.dumps({"pin": pin}).encode("utf-8")
    req = urllib.request.Request(
        login_url, 
        data=data, 
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            res = json.loads(response.read().decode("utf-8"))
            return res.get("token", "")
    except Exception as e:
        print(f"Error logging in to local server: {e}")
        return ""

def poll_prompts(base_url: str, token: str, exit_on_prompt: bool = False):
    """Polling loop that listens for remote prompts.
    
    When exit_on_prompt=True (IDE mode):
    1. Poll /api/agent/prompt/check until a pending prompt is found
    2. Write remote_prompt.json for the IDE agent to pick up
    3. EXIT immediately — the IDE wakes up when this background task finishes
    
    When exit_on_prompt=False (persistent/external mode):
    1. Poll /api/agent/prompt/check until a pending prompt is found
    2. Write remote_prompt.json for the IDE agent to pick up
    3. Wait for the IDE agent to consume the prompt (remote_prompt.json becomes {} or is deleted)
    4. Wait for agent_response.json to appear (agent finished execution)
    5. Post the response back to the server via /api/agent/response
    6. Clean up and resume polling (back to step 1)
    """
    check_url = f"{base_url}/api/agent/prompt/check"
    headers = {"Authorization": f"Bearer {token}"}
    
    mode_label = "exit-on-prompt (IDE)" if exit_on_prompt else "persistent (external)"
    print(f"Agent Daemon: Polling loop started in {mode_label} mode. Listening for remote commands...")
    
    while True:
        # ── PHASE 0: Check remote_mode.json ──
        current_workspace = get_workspace_root()
        mode_file = os.path.join(current_workspace, ".agents", "config", "remote_mode.json")
        try:
            if os.path.exists(mode_file):
                with open(mode_file, "r", encoding="utf-8") as f:
                    mode_data = json.load(f)
                    if not mode_data.get("enabled", False):
                        print("Agent Daemon: remote_mode is disabled in this workspace. Shutting down automatically.")
                        sys.exit(0)
        except Exception:
            pass

        # ── PHASE 1: Poll for a new prompt ──
        try:
            req = urllib.request.Request(check_url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
                
                if data.get("status") in ("pending", "executing") and "prompt" in data:
                    prompt = data["prompt"]
                    prompt_id = data["id"]
                    print(f"\n{'='*60}")
                    print(f"[PROMPT RECEIVED] ID: {prompt_id}")
                    print(f"  Content: {prompt}")
                    print(f"{'='*60}")
                    
                    target_workspace = data.get("workspace_path")
                    current_workspace = get_workspace_root()
                    
                    if target_workspace:
                        # Normalize paths (resolve slashes, etc.) and lower-case them for robust Windows comparison
                        target_norm = os.path.normpath(target_workspace).lower()
                        current_norm = os.path.normpath(current_workspace).lower()
                        
                        if target_norm != current_norm:
                            print(f"  [INFO] Target workspace override active: {target_workspace}")
                            # We deliberately do not exit here so the current daemon can pass the prefix to the current IDE agent
                    
                    # Write prompt info to local workspace file for the agent to read
                    prompt_file = os.path.join(current_workspace, ".agents", "state", "remote_prompt.json")
                    prompt_info = {
                        "id": prompt_id,
                        "prompt": prompt,
                        "token": token
                    }
                    with open(prompt_file, "w", encoding="utf-8") as f:
                        json.dump(prompt_info, f, indent=4)
                    
                    print(f"  Wrote remote_prompt.json to {current_workspace}")
                    
                    # In IDE mode, exit immediately so the background task finishes
                    # and the IDE agent wakes up to process the prompt
                    if exit_on_prompt:
                        print("  Exit-on-prompt mode: exiting so IDE agent can wake up.")
                        sys.exit(0)
                    
                    # ── PHASE 2: Wait for IDE agent to consume the prompt ──
                    print("  Waiting for IDE agent to consume prompt...")
                    consumed = False
                    consume_start = time.time()
                    consume_timeout = 600  # 10 minutes max wait for agent to pick it up
                    
                    while time.time() - consume_start < consume_timeout:
                        time.sleep(2)
                        
                        # Check if remote_prompt.json was cleared/emptied
                        if not os.path.exists(prompt_file):
                            consumed = True
                            print("  > Prompt file deleted - agent consumed it.")
                            break
                        
                        try:
                            with open(prompt_file, "r", encoding="utf-8") as f:
                                content = f.read().strip()
                                if not content or content == "{}":
                                    consumed = True
                                    print("  > Prompt file cleared - agent consumed it.")
                                    break
                                # Also check if prompt field was removed
                                pdata = json.loads(content)
                                if not pdata.get("prompt"):
                                    consumed = True
                                    print("  > Prompt field cleared - agent consumed it.")
                                    break
                        except (json.JSONDecodeError, IOError):
                            # File might be mid-write, wait
                            pass
                    
                    if not consumed:
                        print("  WARNING: Timeout waiting for agent to consume prompt. Resuming poll...")
                        # Clear the stale prompt file so we don't re-process it
                        try:
                            with open(prompt_file, "w", encoding="utf-8") as f:
                                json.dump({}, f)
                        except Exception:
                            pass
                        continue
                    
                    # ── PHASE 3: Wait for agent to finish execution ──
                    print("  Waiting for agent to complete execution...")
                    
                    # Determine where we expect the agent to write the response
                    expected_workspace = target_workspace if (target_workspace and target_norm != current_norm) else current_workspace
                    response_file = os.path.join(expected_workspace, ".agents", "state", "agent_response.json")
                    
                    response_timeout = 900  # 15 minutes max for agent to complete task
                    response_start = time.time()
                    agent_response = None
                    
                    while time.time() - response_start < response_timeout:
                        time.sleep(2)
                        
                        if os.path.exists(response_file):
                            try:
                                with open(response_file, "r", encoding="utf-8") as f:
                                    agent_response = json.load(f)
                                print(f"  > Agent response received: {agent_response.get('status', 'unknown')}")
                                break
                            except (json.JSONDecodeError, IOError):
                                # File might be mid-write
                                pass
                    
                    # ── PHASE 4: Post response back to server ──
                    if agent_response:
                        try:
                            resp_url = f"{base_url}/api/agent/response"
                            resp_data = json.dumps({
                                "status": agent_response.get("status", "completed"),
                                "output": agent_response.get("output", "Task completed.")
                            }).encode("utf-8")
                            resp_req = urllib.request.Request(
                                resp_url,
                                data=resp_data,
                                headers={
                                    "Content-Type": "application/json",
                                    "Authorization": f"Bearer {token}"
                                }
                            )
                            with urllib.request.urlopen(resp_req, timeout=5) as resp:
                                print(f"  > Response posted to server successfully.")
                        except Exception as e:
                            print(f"  WARNING: Failed to post response to server: {e}")
                        
                        # Clean up response file
                        try:
                            os.remove(response_file)
                        except Exception:
                            pass
                    else:
                        print("  WARNING: Timeout waiting for agent response. Resuming poll...")
                    
                    # ── PHASE 5: Resume polling ──
                    print(f"  Resuming prompt polling...\n")
                    continue
                    
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("Unauthorized. Token may have expired. Re-authenticating...")
                # Try to re-authenticate
                config_path = os.path.join(get_workspace_root(), ".agents", "config", "config.json")
                try:
                    with open(config_path, "r") as f:
                        config = json.load(f)
                        pin = config.get("pin")
                    if pin:
                        new_token = get_auth_token(base_url, pin)
                        if new_token:
                            token = new_token
                            headers = {"Authorization": f"Bearer {token}"}
                            print("  > Re-authenticated successfully.")
                            continue
                except Exception:
                    pass
                print("  ERROR: Re-authentication failed. Exiting.")
                sys.exit(2)
            else:
                # Transient server error, keep polling
                pass
        except Exception:
            # Silence connection errors during local restart/transient issues
            pass
            
        time.sleep(2)

def start_daemon(url: str = "http://127.0.0.1:8000", exit_on_prompt: bool = False):
    # Check if remote mode is enabled
    mode_path = os.path.join(get_workspace_root(), ".agents", "config", "remote_mode.json")
    while True:
        if os.path.exists(mode_path):
            try:
                with open(mode_path, "r") as f:
                    if not json.load(f).get("enabled", True):
                        time.sleep(5)
                        continue
            except Exception:
                pass
        break

    # Read config.json to get PIN
    config_path = os.path.join(get_workspace_root(), ".agents", "config", "config.json")
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found.")
        sys.exit(1)

    try:
        with open(config_path, "r") as f:
            config = json.load(f)
            pin = config.get("pin")
    except Exception as e:
        print(f"Error reading config: {e}")
        sys.exit(1)

    if not pin:
        print("Error: No access PIN configured in config.json")
        sys.exit(1)

    # Login to get session token
    token = get_auth_token(url, pin)
    if not token:
        print("Failed to authenticate with local FastAPI server.")
        sys.exit(1)

    print(f"Daemon authenticated successfully. Starting {'exit-on-prompt' if exit_on_prompt else 'persistent'} poll loop.")
    poll_prompts(url, token, exit_on_prompt=exit_on_prompt)

def main():
    parser = argparse.ArgumentParser(description="Antigravity Remote Agent Daemon")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="FastAPI Server URL")
    parser.add_argument("--exit-on-prompt", action="store_true", default=False,
                        help="Exit after writing the first prompt (for IDE background task mode)")
    args = parser.parse_args()
    start_daemon(args.url, exit_on_prompt=args.exit_on_prompt)

if __name__ == "__main__":
    main()
