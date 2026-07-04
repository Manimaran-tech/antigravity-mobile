# Antigravity Remote Monitoring and Control Rules

Before performing any action, check if remote mode is enabled by reading `remote_mode.json` in the workspace root.
- **If `"enabled": false`**: Run normally and bypass all remote features (do NOT write status files, do NOT update log files, and do NOT use `agent_approve.py` for confirmations).
- **If `"enabled": true`** (or if the file is missing/unreadable): Adhere to the following rules at all times:

1. **Always Stream Execution Logs to Mobile**:
   - At the start of **every** agent turn (including desktop chat sessions), write the current active task description to `agent_status.json` in the workspace:
     ```json
     {"status": "working", "task": "[Brief description of what you are doing right now]"}
     ```
   - Write or append your step-by-step developer thoughts, planned tool calls, and execution progress to `agent_execution.log` so the mobile user can monitor your work in real-time.
   - When a turn completes successfully, update `agent_status.json` to indicate you are idle:
     ```json
     {"status": "idle", "task": ""}
     ```
     And append a completion log to `agent_execution.log` (e.g., `Task execution complete. Agent is now idle.`).

2. **Always Route Confirmation Dialogs to Mobile**:
   - Before executing any command (via the `run_command` tool) that modifies state or runs scripts, always run the approval script first to display the confirmation popup on the user's mobile screen:
     ```powershell
     python -m antigravity_remote.agent_approve --type command --target "[Command to execute]"
     ```
   - If the helper exits with code `0` (APPROVED), proceed to execute the command.
   - If the helper exits with code `1` (REJECTED), skip the command and report the rejection.
   - If the helper exits with code `3` (OFFLINE), the mobile connection is offline. Bypass the helper and execute the command directly via `run_command` (this will fallback to prompting the user on the PC desktop).
