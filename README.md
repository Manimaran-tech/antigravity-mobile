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

**Control your Antigravity IDE desktop coding assistant from your phone.**

Monitor agent tasks, approve commands, and switch active models — remotely.

[![PyPI](https://img.shields.io/pypi/v/antigravity-mobile)](https://pypi.org/project/antigravity-mobile/)
[![Python](https://img.shields.io/pypi/pyversions/antigravity-mobile)](https://pypi.org/project/antigravity-mobile/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/antigravity-mobile)](https://pypistats.org/packages/antigravity-mobile)
[![Total Downloads](https://static.pepy.tech/badge/antigravity-mobile)](https://pepy.tech/project/antigravity-mobile)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Current Version: 0.4.0**

---

## ✨ Features

- **Mobile Dashboard** — Responsive, glassmorphic dark-themed UI with real-time system stats (CPU, RAM, Disk)
- **Model Switcher** — Switch the active AI model (Gemini, Claude, GPT) in your Antigravity IDE from your phone
- **Live Agent Console** — Stream step-by-step IDE agent execution thoughts and logs to your phone in real-time
- **Mobile Approvals** — Approve or reject terminal commands requested by your IDE agent directly from your phone screen
- **Offline Fallback** — Automatically falls back to local PC desktop prompts if the mobile server is offline
- **Remote Toggle** — Turn remote mode on/off to save tokens when coding locally

---

## Installation

```bash
pip install antigravity-mobile
```

That's it! Now run `antigravity-mobile` to see the welcome screen.

> [!TIP]
> **Windows Path Issue:** If you receive an error saying `'antigravity-mobile' is not recognized as an internal or external command`, it means your Python scripts directory is not in your system PATH.
> You can bypass this by running the tool directly as a Python module:
> ```bash
> python -m antigravity_remote
> ```
> *(You can replace `antigravity-mobile` with `python -m antigravity_remote` for all commands below).*

### Uninstallation

If you wish to completely remove the tool, you can uninstall it via pip:
```bash
pip uninstall antigravity-mobile -y
```

---

## Quick Start

### Step 1: Install the Package
First, install the package using pip:
```bash
pip install antigravity-mobile
```

### Step 2: Start the Server
Open a **separate CMD terminal** and run the server with a public Cloudflare tunnel:
```bash
antigravity-mobile start --host 0.0.0.0 --port 8000 --tunnel
```
*(This will give you a public `trycloudflare.com` URL to open on your phone, along with a secure PIN to log in).*

### Step 3: Enable "Always Proceed" Mode
Go to your Antigravity IDE and make sure it is set to **"Always Proceed" mode** (Full Autonomy) so it can automatically route commands to your phone without pausing for desktop approvals.

### Step 4: Connect the Agent
In your Antigravity IDE chat, simply type:
```text
start the daemon server
```

### Step 5: Start Working!
That's it! Once it connects, open the tunnel URL on your mobile browser, enter your PIN, and you can start working in remote mode and monitoring your agent directly from your phone.

---

## 📖 All Commands

| Command | Description |
|---------|-------------|
| `antigravity-mobile` | Show welcome banner & quick-start guide |
| `antigravity-mobile setup` | Interactive first-time setup wizard |
| `antigravity-mobile init` | Generate config & security PIN |
| `antigravity-mobile start` | Start the FastAPI dashboard server |
| `antigravity-mobile start --tunnel` | Start the server and create a public Cloudflare tunnel |
| `antigravity-mobile remote --on` | Enable remote monitoring mode |
| `antigravity-mobile remote --off` | Disable remote monitoring (saves tokens) |
| `antigravity-mobile remote --status` | Check if remote mode is on or off |

---

##  Usage Examples

### Turn off remote mode when coding locally
```bash
antigravity-mobile remote --off
```
This disables all remote logging, status updates, and mobile approval prompts — saving tokens and resources.

### Turn it back on when leaving your desk
```bash
antigravity-mobile remote --on
```

### Start server with a public internet tunnel
```bash
antigravity-mobile start --host 0.0.0.0 --port 8000 --tunnel
```
This uses Cloudflare Quick Tunnels to provide a stable, free HTTPS URL for your phone instantly.

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

##  Security

- **PIN Authentication** — A secure 6-digit PIN is generated on first setup. Mobile clients must authenticate with this PIN before accessing any features.
- **Token-Based Sessions** — After PIN login, all API requests use a session token.
- **Command Approval** — Remote commands require explicit user confirmation before execution.
- **Offline Fallback** — If the server goes offline, the agent automatically falls back to local PC prompts instead of hanging.

---

##  Project Structure

```
antigravity-mobile/
├── antigravity_remote/
│   ├── cli.py              # CLI with welcome screen, setup wizard, and server commands
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

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Manimaran-tech/antigravity-mobile&type=Date)](https://star-history.com/#Manimaran-tech/antigravity-mobile&Date)

---

## License

MIT
