# Antigravity Mobile Monitor & Task Scheduler

`antigravity-mobile` is a lightweight command-line tool and FastAPI-powered web application designed for the **Google Antigravity IDE and CLI**. It enables you to securely monitor your development workflow, switch AI models, track usage quotas, view real-time log outputs, and confirm or reject commands directly from your mobile device while you are away from your workstation.

---

## Key Features

- 📱 **Mobile-Optimized Dashboard**: Responsive, glassmorphic UI styled in a beautiful dark theme with real-time stats.
- 🤖 **Model Control**: Switch active models (Gemini 3.5 Flash, Claude 4.5 Sonnet, GPT-OSS, etc.) directly from your phone.
- 📊 **Quota & Limit Progress**: Live tracking of request limits (Gemini Weekly/5-Hr and Claude/GPT Weekly/5-Hr limits).
- 💻 **Live Agent Thought Output Console**: A real-time console streaming step-by-step developer thoughts, planned tools, and logs from the agent to your phone.
- 🛡️ **Interactive Mobile Approvals**: Run terminal commands and get interruptive Yes/No popup prompts on your phone instead of the PC desktop.
- 🔌 **Automatic Offline Fallback**: Automatically bypasses the mobile check and falls back to standard PC desktop prompts if your FastAPI server is offline.
- ⚡ **PC CLI Run Wrapper (`run`)**: Execute commands directly on your PC that log to the server and appear on your phone, bypassing all sandbox desktop security prompts.
- 🎛️ **Remote Toggle Switch (`remote`)**: Turn remote mode ON or OFF at any time to save tokens and resources when coding locally.
- 🔒 **PIN Authentication**: Secure authorization PIN generated on first start, requiring authentication on the mobile client.

---

## Installation

### From Source (Editable Mode)
```bash
# Clone the repository and navigate to the folder
cd "d:\Remote Antigravity"

# Install dependencies and CLI tool in editable mode
pip install -e .
```

### Packaging & Publishing to PyPI
To build and publish the package so others can install it via `pip install antigravity-mobile`:
1. **Install Build Tools**:
   ```bash
   pip install --upgrade build twine
   ```
2. **Build Distribution Archives**:
   ```bash
   python -m build
   ```
   *(This generates `.whl` and `.tar.gz` files in the `dist/` directory.)*
3. **Upload to PyPI**:
   ```bash
   python -m twine upload dist/antigravity_mobile-*
   ```

---

## Setup & IDE Configuration

To enable silent executions on your PC and redirect confirmations to your phone, update your **Antigravity IDE User Settings**:

1. Open your settings file: `C:\Users\K\AppData\Roaming\Antigravity IDE\User\settings.json`
2. Add the following options to allow automatic local command approvals:
   ```json
   {
       "antigravity.autoApprove": true,
       "antigravity.autonomyLevel": "full"
   }
   ```
3. Press **`Ctrl + Shift + P`** in the IDE, type **`Developer: Reload Window`**, and press **Enter** to apply changes.

---

## Usage

### 1. Initialize Configuration
```bash
antigravity-mobile init
```
Generates a randomized access PIN for mobile login (stored in `config.json`).

### 2. Start the Server & Tunnel
Start the server and expose it using localtunnel:
```bash
# Start FastAPI server on all interfaces
antigravity-mobile start --host 0.0.0.0 --port 8000

# Expose it to the internet so you can access it on your phone
npx localtunnel --port 8000
```
Open the localtunnel URL on your mobile phone browser and enter your access PIN!

### 3. CLI Command Execution
To execute commands on your PC, log them to the server, and monitor them live on your phone with **no desktop prompts**:
```bash
antigravity-mobile run "pytest tests/test_server.py"
```

### 4. Remote Mode Toggle
Turn remote monitoring and control on/off:
```bash
# Enable remote mode (wakes up daemon, streams logs)
antigravity-mobile remote --on

# Disable remote mode (puts daemon to sleep, stops logging)
antigravity-mobile remote --off

# Check status
antigravity-mobile remote --status
```

---

## Project Structure

```
d:/Remote Antigravity/
├── .agents/
│   ├── AGENTS.md          # Global agent logging & mobile approval rules
│   └── skills/
│       └── remote_control/
│           └── SKILL.md   # Remote loop instruction skill for AI agents
├── antigravity_remote/
│   ├── __init__.py
│   ├── cli.py             # CLI Command handling
│   ├── server.py          # FastAPI server with WebSocket logging & dashboard
│   ├── agent_daemon.py    # Background sidecar polling daemon
│   ├── agent_approve.py   # Mobile approval helper script
│   ├── task_runner.py     # Background subprocess runner
│   ├── templates/
│   │   └── index.html     # Mobile-responsive glassmorphic dashboard
│   └── static/
│       ├── css/
│       │   └── styles.css # Custom dashboard styles
│       └── js/
│           └── main.js    # Client-side WebSocket & model switcher logic
├── pyproject.toml         # Packaging metadata & entrypoints
└── README.md              # Documentation
```
