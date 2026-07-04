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

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "start":
        cmd_start(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
