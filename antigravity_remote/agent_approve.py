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
        print(f"Error logging in: {e}")
        return ""

def request_approval(base_url: str, token: str, action_type: str, target: str) -> bool:
    approve_req_url = f"{base_url}/api/agent/approve/request"
    data = json.dumps({"type": action_type, "target": target}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    req = urllib.request.Request(approve_req_url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status == 200
    except Exception as e:
        print(f"Failed to post approval request: {e}")
        return False

def poll_approval_decision(base_url: str, token: str, timeout_seconds: int = 300) -> int:
    check_url = f"{base_url}/api/agent/approve/check"
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(check_url, headers=headers)
    
    start_time = time.time()
    print(f"Polling for mobile confirmation (Timeout: {timeout_seconds}s)...")
    
    while time.time() - start_time < timeout_seconds:
        # 1. Check local file fallback first
        resp_file = "agent_approval_response.json"
        if os.path.exists(resp_file):
            try:
                with open(resp_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    status = data.get("status")
                
                # Small delay to ensure server finished writing/closing before delete
                time.sleep(0.1)
                try:
                    os.remove(resp_file)
                except Exception:
                    pass
                    
                if status == "approved":
                    print("MOBILE_CONFIRMATION (FILE): APPROVED")
                    return 0
                elif status == "rejected":
                    print("MOBILE_CONFIRMATION (FILE): REJECTED")
                    return 1
            except Exception as e:
                print(f"Error reading file fallback: {e}")

        # 2. Check HTTP API fallback
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode("utf-8"))
                status = data.get("status")
                
                if status == "approved":
                    print("MOBILE_CONFIRMATION (API): APPROVED")
                    return 0
                elif status == "rejected":
                    print("MOBILE_CONFIRMATION (API): REJECTED")
                    return 1
        except Exception:
            pass # Suppress network errors during polling
            
        time.sleep(1.5)
        
    print("MOBILE_CONFIRMATION: TIMEOUT")
    return 2

def main():
    parser = argparse.ArgumentParser(description="Antigravity Remote Agent Command Approval Helper")
    parser.add_argument("--type", required=True, choices=["command", "edit", "create", "delete"], help="Type of action requiring approval")
    parser.add_argument("--target", required=True, help="Target action/command details")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="FastAPI Server URL")
    parser.add_argument("--timeout", type=int, default=300, help="Approval timeout in seconds")
    args = parser.parse_args()

    # Always write file-based request fallback first
    try:
        with open("agent_approval_request.json", "w", encoding="utf-8") as f:
            json.dump({"type": args.type, "target": args.target}, f)
    except Exception as e:
        print(f"Warning: Failed to write file-based fallback: {e}")

    # Read config.json to get PIN
    config_path = "config.json"
    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found.")
        sys.exit(3)

    try:
        with open(config_path, "r") as f:
            config = json.load(f)
            pin = config.get("pin")
    except Exception as e:
        print(f"Error reading config: {e}")
        sys.exit(3)

    # Login to get token
    token = get_auth_token(args.url, pin)
    
    if token:
        # Post approval request over API
        request_approval(args.url, token, args.type, args.target)

    # Poll for user decision
    exit_code = poll_approval_decision(args.url, token or "", args.timeout)
    
    # Cleanup request file if it exists
    if os.path.exists("agent_approval_request.json"):
        try:
            os.remove("agent_approval_request.json")
        except Exception:
            pass
            
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
