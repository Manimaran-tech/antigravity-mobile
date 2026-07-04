# Antigravity Mobile

```
     █████╗ ███╗   ██╗████████╗██╗ ██████╗ ██████╗  █████╗ ██╗   ██╗██╗████████╗██╗   ██╗
    ██╔══██╗████╗  ██║╚══██╔══╝██║██╔════╝ ██╔══██╗██╔══██╗██║   ██║██║╚══██╔══╝╚██╗ ██╔╝
    ███████║██╔██╗ ██║   ██║   ██║██║  ███╗██████╔╝███████║██║   ██║██║   ██║    ╚████╔╝
    ██╔══██║██║╚██╗██║   ██║   ██║██║   ██║██╔══██╗██╔══██║╚██╗ ██╔╝██║   ██║     ╚██╔╝
    ██║  ██║██║ ╚████║   ██║   ██║╚██████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║   ██║      ██║
    ╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝   ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝   ╚═╝      ╚═╝

    ███╗   ███╗ ██████╗ ██████╗ ██╗██╗     ███████╗
    ████╗ ████║██╔═══██╗██╔══██╗██║██║     ██╔════╝
    ██╔████╔██║██║   ██║██████╔╝██║██║     █████╗
    ██║╚██╔╝██║██║   ██║██╔══██╗██║██║     ██╔══╝
    ██║ ╚═╝ ██║╚██████╔╝██████╔╝██║███████╗███████╗
    ╚═╝     ╚═╝ ╚═════╝ ╚═════╝ ╚═╝╚══════╝╚══════╝
```

**Control your AI coding agent from your phone.**

Monitor tasks, approve commands, switch models — remotely.

[![PyPI](https://img.shields.io/pypi/v/antigravity-mobile)](https://pypi.org/project/antigravity-mobile/)
[![Python](https://img.shields.io/pypi/pyversions/antigravity-mobile)](https://pypi.org/project/antigravity-mobile/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

---

## ✨ Features

- 📱 **Mobile Dashboard** — Responsive, glassmorphic dark-themed UI with real-time system stats (CPU, RAM, Disk)
- 🤖 **Model Switcher** — Change active AI models (Gemini 3.5 Flash, Claude 4.5 Sonnet, GPT-OSS) from your phone
- 📊 **Quota Tracking** — Live progress bars for Gemini & Claude/GPT weekly and 5-hour request limits
- 💻 **Live Agent Console** — Stream step-by-step agent thoughts and logs to your phone in real-time
- 🛡️ **Mobile Approvals** — Approve or reject terminal commands via interactive popups on your phone
- 🔌 **Offline Fallback** — Automatically falls back to PC desktop prompts if the server is offline
- ⚡ **CLI Run Wrapper** — Execute commands silently on your PC and stream output to your phone
- 🎛️ **Remote Toggle** — Turn remote mode on/off to save tokens when coding locally

---

## 🚀 Installation

```bash
pip install antigravity-mobile
```

That's it! Now run `antigravity-mobile` to see the welcome screen.

---

## 📋 Quick Start

### Step 1: Setup
Run the interactive setup wizard to generate your secure access PIN and configure agent rules:
```bash
antigravity-mobile setup
```

### Step 2: Start the Server
Launch the FastAPI dashboard server on all interfaces:
```bash
antigravity-mobile start --host 0.0.0.0 --port 8000
```

### Step 3: Get a Public URL
Expose your local server to the internet so your phone can reach it (requires Node.js):
```bash
npx localtunnel --port 8000
```
This will give you a public URL like `https://xyz.loca.lt`.

### Step 4: Open on Your Phone
1. Open the localtunnel URL on your mobile browser
2. Enter your access PIN (shown during setup)
3. You're in! Start monitoring and controlling your agent remotely 🎉

---

## 📖 All Commands

| Command | Description |
|---------|-------------|
| `antigravity-mobile` | Show welcome banner & quick-start guide |
| `antigravity-mobile setup` | Interactive first-time setup wizard |
| `antigravity-mobile init` | Generate config & security PIN |
| `antigravity-mobile start` | Start the FastAPI dashboard server |
| `antigravity-mobile run <cmd>` | Execute a command & stream logs to phone |
| `antigravity-mobile remote --on` | Enable remote monitoring mode |
| `antigravity-mobile remote --off` | Disable remote monitoring (saves tokens) |
| `antigravity-mobile remote --status` | Check if remote mode is on or off |

---

## 💡 Usage Examples

### Run a command and monitor it from your phone
```bash
antigravity-mobile run "pytest tests/"
```
The command runs silently on your PC. Output streams live to your phone's console.

### Turn off remote mode when coding locally
```bash
antigravity-mobile remote --off
```
This disables all remote logging, status updates, and mobile approval prompts — saving tokens and resources.

### Turn it back on when leaving your desk
```bash
antigravity-mobile remote --on
```

### Start everything in one go
```bash
# Terminal 1: Start the server
antigravity-mobile start --host 0.0.0.0 --port 8000

# Terminal 2: Expose to internet
npx localtunnel --port 8000
```

---

## ⚙️ IDE Configuration (Optional)

To enable silent command execution on your PC (so approvals go to your phone instead of popping up on the desktop), update your **Antigravity IDE User Settings**:

1. Open: `C:\Users\<YOU>\AppData\Roaming\Antigravity IDE\User\settings.json`
2. Add:
   ```json
   {
       "antigravity.autoApprove": true,
       "antigravity.autonomyLevel": "full"
   }
   ```
3. Press `Ctrl + Shift + P` → `Developer: Reload Window`

---

## 🔒 Security

- **PIN Authentication** — A secure 6-digit PIN is generated on first setup. Mobile clients must authenticate with this PIN before accessing any features.
- **Token-Based Sessions** — After PIN login, all API requests use a session token.
- **Command Approval** — Remote commands require explicit user confirmation before execution.
- **Offline Fallback** — If the server goes offline, the agent automatically falls back to local PC prompts instead of hanging.

---

## 📁 Project Structure

```
antigravity-mobile/
├── antigravity_remote/
│   ├── cli.py              # CLI with banner, setup wizard, and commands
│   ├── server.py           # FastAPI server with WebSocket dashboard
│   ├── agent_daemon.py     # Background polling daemon for remote commands
│   ├── agent_approve.py    # Mobile approval helper with offline fallback
│   ├── task_runner.py      # Background subprocess runner
│   └── templates/
│       └── index.html      # Mobile-responsive glassmorphic dashboard
├── .agents/
│   ├── AGENTS.md           # Agent logging & mobile approval rules
│   └── skills/
│       └── remote_control/
│           └── SKILL.md    # Remote loop skill for AI agents
├── pyproject.toml          # Package metadata & CLI entrypoints
└── README.md
```

---

## 📦 Publishing Updates

```bash
# Install build tools
pip install build twine

# Build distribution
python -m build

# Upload to PyPI
python -m twine upload dist/antigravity_mobile-*
```

---

## License

MIT
