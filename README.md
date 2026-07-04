# Antigravity Remote Monitor & Task Scheduler

`antigravity-remote` is a lightweight command-line tool and FastAPI-powered web application designed for the **Antigravity 2.0 IDE and CLI**. It enables you to securely monitor your development workflow, inspect system performance, and schedule tasks directly from a mobile device or remote web browser while you are away from your workstation.

---

## Features

- 📱 **Mobile-Optimized Dashboard**: Responsive, glassmorphic UI styled in a beautiful dark theme with real-time stats.
- 💻 **Live Terminal Streaming**: A real-time stdout/stderr log viewer built using WebSockets, supporting ANSI color escape codes.
- ⚙️ **Remote Task Scheduling**: Submit command line tasks (e.g. tests, builds, linting) from your mobile device.
- 🛡️ **Secure Execution**: Tasks queue up in a "Pending" state and require a secure confirmation code or local CLI approval before they are allowed to execute.
- 🔒 **PIN Authentication**: Generates a secure authorization PIN on first start, requiring authentication on the mobile client.
- 📊 **Resource Monitoring**: Live metrics of CPU, RAM, and Disk space.

---

## Installation

To install `antigravity-remote` in development/editable mode:

```bash
# Clone the repository (if downloaded from GitHub) and navigate to directory
cd "d:\Remote Antigravity"

# Install dependencies and CLI tool in editable mode
pip install -e .
```

## CLI Usage

The tool can be executed directly as a script (if the Python scripts directory is on your `PATH`) or as a Python module:

### 1. Initialize Configuration
```bash
python -m antigravity_remote.cli init
# OR if scripts folder is on your PATH:
antigravity-remote init
```
This generates a `config.json` with a secure randomized PIN for mobile authentication.

### 2. Start the Monitor Server
```bash
python -m antigravity_remote.cli start --host 0.0.0.0 --port 8000
# OR if scripts folder is on your PATH:
antigravity-remote start --host 0.0.0.0 --port 8000
```
- `--host 0.0.0.0` allows devices on your local network (e.g., your mobile phone via local Wi-Fi) to access the server.
- `--port 8000` is the default port (customizable).

---

## Project Structure

```
d:/Remote Antigravity/
├── antigravity_remote/
│   ├── __init__.py
│   ├── cli.py             # CLI Command handling
│   ├── server.py          # FastAPI server with WebSocket logging
│   ├── task_runner.py     # Background subprocess execution manager
│   ├── templates/
│   │   └── index.html     # Mobile-responsive glassmorphic dashboard
│   └── static/
│       ├── css/
│       │   └── styles.css # UI design styles
│       └── js/
│           └── main.js    # Real-time WebSocket connection handling
├── tests/
│   └── test_server.py     # Integration tests
├── pyproject.toml         # Build system & metadata
├── README.md              # Project documentation
└── .gitignore             # Git ignore file
```

---

## Security Model

1. **Authentication Token**: The server issues a JWT/secure token to clients that input the correct PIN generated during `antigravity-remote init`.
2. **Access Control**: Remote clients cannot run commands immediately. When a command is submitted:
   - It is marked as `Pending`.
   - The user must explicitly approve the command on the authenticated dashboard or through the terminal.
   - Only confirmed commands are spawned as subprocesses.
