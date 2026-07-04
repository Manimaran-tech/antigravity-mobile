import os
import sys
import json
import time
import urllib.request
import urllib.error
import argparse

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

def poll_prompts(base_url: str, token: str):
    check_url = f"{base_url}/api/agent/prompt/check"
    # Note: /api/agent/prompt/check is public in our FastAPI routing,
    # but we can pass the auth header anyway to ensure standard endpoint access
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(check_url, headers=headers)
    
    print("Agent Daemon: Polling for remote commands...")
    
    while True:
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                status_code = response.status
                data = json.loads(response.read().decode("utf-8"))
                
                if data.get("status") == "pending" or "prompt" in data:
                    prompt = data["prompt"]
                    prompt_id = data["id"]
                    print(f"Detected remote command prompt [{prompt_id}]: {prompt}")
                    
                    # Write prompt info to local workspace file for the agent to read
                    prompt_info = {
                        "id": prompt_id,
                        "prompt": prompt,
                        "token": token
                    }
                    with open("remote_prompt.json", "w", encoding="utf-8") as f:
                        json.dump(prompt_info, f, indent=4)
                        
                    print("Wrote remote_prompt.json. Exiting daemon to trigger agent execution.")
                    sys.exit(0)
                    
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("Unauthorized. Token may have expired.")
                sys.exit(2)  # Exit code 2 indicates auth failure
            else:
                print(f"Server error: {e}")
        except Exception as e:
            # Silence connection errors during local restart
            pass
            
        time.sleep(2)

def main():
    parser = argparse.ArgumentParser(description="Antigravity Remote Agent Daemon")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="FastAPI Server URL")
    args = parser.parse_args()

    # Check if remote mode is enabled
    mode_path = "remote_mode.json"
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
    config_path = "config.json"
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found in workspace.")
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
    token = get_auth_token(args.url, pin)
    if not token:
        print("Failed to authenticate with local FastAPI server.")
        sys.exit(1)

    poll_prompts(args.url, token)

if __name__ == "__main__":
    main()
