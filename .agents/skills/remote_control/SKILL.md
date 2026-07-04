---
name: "Antigravity Remote Control Loop"
description: "Instructs the agent how to poll for remote commands, execute them, stream real-time logs, and query for approvals via the mobile phone dashboard."
---

# Antigravity Remote Control Loop

When this skill is loaded, the agent participates in the remote command and confirmation loop with the FastAPI mobile server.

## Execution Sequence

### 0. Check Remote Mode Toggle
Before starting, read `remote_mode.json` in the workspace root.
- **If `"enabled": false`**: Do NOT run the remote control loop. Stop here and run standard workspace actions normally.
- **If `"enabled": true`** (or if the file is missing): Proceed with the remote control sequence below.

### 1. Check for Active Remote Prompt
When waking up, check if a file named `remote_prompt.json` exists in the workspace root.
- **If it exists**:
  1. Read the JSON content (which contains `id`, `prompt`, and `token`).
  2. Overwrite `remote_prompt.json` with `{}` to clear it.
  3. Treat the `prompt` value as the primary user request for this turn.
  4. Write the current starting state to `agent_status.json`:
     ```json
     {"status": "working", "task": "Starting task execution..."}
     ```
  5. Initialize `agent_execution.log` with the initial start log:
     ```
     [12:00:00] Starting remote task: "[Prompt]"
     ```
  6. **Perform the requested task** (creating files, editing code, running scripts).
     - Whenever you perform a step (e.g. searching, writing a file, or running a test), update `agent_status.json` with the current task and append a progress line to `agent_execution.log` using `write_to_file` so the mobile user can monitor your progress in real-time.
  7. If you need to execute any terminal command or modify files that require confirmation:
     - Run the approval script first:
       ```powershell
       python -m antigravity_remote.agent_approve --type command --target "<command or action>"
       ```
     - *Note: Since the local PC is configured to auto-approve commands, this script will run autonomously on the PC and display the approval overlay on the user's mobile screen!*
     - If the command exits with code `0` (APPROVED), proceed to execute the command/action.
     - If it exits with code `1` (REJECTED), skip the action and report that the user rejected it on their mobile phone.
     - If it exits with code `2` (TIMEOUT), report that the request timed out.
     - If it exits with code `3` (OFFLINE), the mobile server is offline. Bypass the helper and execute the command directly via `run_command` (which will fallback to prompting the user on the PC desktop).
  8. Once the task is finished, post the final response to the server. To avoid executing shell commands, write the response directly to `agent_response.json` in the workspace:
     ```json
     {
       "status": "completed",
       "output": "Completed successfully: [Brief description of what was done]"
     }
     ```
     *The running FastAPI server will automatically pick up this file, update the server prompt state, and delete the file.*
  9. Update `agent_status.json` to:
     ```json
     {"status": "idle", "task": ""}
     ```
  10. Start the daemon listener again (see Step 2) and end your turn.

- **If it does NOT exist**:
  - The agent is idle. Start the daemon listener to wait for new remote prompts.

### 2. Spawning the Daemon Listener
To listen for new remote prompts while you are idle, run the daemon script in the background:
```powershell
python -m antigravity_remote.agent_daemon
```
*Note: Make sure to launch this command in the background (using `run_command` with high async wait time). Once launched, do not call any more tools and end your turn. The IDE will automatically wake you up when the daemon detects a prompt and exits.*
