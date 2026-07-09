# Antigravity Remote Monitoring and Control Rules

Before performing any action, check if remote mode is enabled by reading `remote_mode.json` in the workspace root.
- **If `"enabled": false`**: Run normally and bypass all remote features (do NOT write status files, do NOT update log files, and do NOT use mobile confirmations).
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

2. **Always Route Confirmation Dialogs to Mobile (File-Based Protocol)**:
   - Before executing any command (via the `run_command` tool) that modifies state or runs scripts, you MUST obtain user confirmation from the mobile device.
   - **EXEMPTION**: Spawning the background listener daemon (`antigravity_remote.agent_daemon`) is exempt from this rule and MUST be executed immediately without requesting confirmation.
   - To avoid triggering PC desktop dialogs, use the **silent file-based approval protocol**:
     1. Write the approval request to `agent_approval_request.json` in the workspace root:
        ```json
        {"type": "command", "target": "[Command or action to execute]"}
        ```
     2. Wait and poll for the user's decision by checking if the file `agent_approval_response.json` exists in the workspace. Read/check it every 1.5 seconds (up to 300 seconds timeout).
     3. If `agent_approval_response.json` is detected:
        - Read the decision status: `{"status": "approved"}` or `{"status": "rejected"}`.
        - Delete `agent_approval_response.json` from the workspace.
        - If approved, proceed with the command. If rejected, abort and report the rejection.
     4. If the server is offline or the file-based handshake fails, run the CLI helper fallback (which may prompt on the desktop):
        ```powershell
        python -m antigravity_remote.agent_approve --type command --target "[Command to execute]"
        ```

3. **Always Start the Daemon Listener if Idle**:
   - If `remote_prompt.json` does not exist or is empty, and you are not currently executing a remote task, you MUST start the daemon listener in the background immediately:
     ```powershell
     antigravity-mobile daemon
     ```
   - Once launched, do not call any more tools and end your turn.
