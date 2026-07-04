---
name: "Antigravity Remote Control Loop"
description: "Instructs the agent how to poll for remote commands, execute them, and query for approvals via the mobile phone dashboard."
---

# Antigravity Remote Control Loop

When this skill is loaded, the agent participates in the remote command and confirmation loop with the FastAPI mobile server.

## Execution Sequence

### 1. Check for Active Remote Prompt
When waking up, check if a file named `remote_prompt.json` exists in the workspace root.
- **If it exists**:
  1. Read the JSON content (which contains `id`, `prompt`, and `token`).
  2. Delete the `remote_prompt.json` file.
  3. Treat the `prompt` value as the primary user request for this turn.
  4. Perform the requested task (creating files, editing code, running scripts).
  5. If you need to execute any terminal command or modify files that require confirmation:
     - Run the approval script first:
       ```powershell
       python -m antigravity_remote.agent_approve --type command --target "<command or action>"
       ```
     - If the command exits with code `0` (APPROVED), proceed to execute the command/action.
     - If it exits with code `1` (REJECTED), skip the action and report that the user rejected it on their mobile phone.
     - If it exits with code `2` (TIMEOUT), report that the request timed out.
  6. Once the task is finished, post the final summary to the server:
     - Use a Python script or raw HTTP POST to `/api/agent/response` with headers `Authorization: Bearer <token>` and body:
       ```json
       {
         "status": "completed",
         "output": "Task finished successfully: [Brief description of what was done]"
       }
       ```
  7. Start the daemon listener again (see Step 2) and end your turn.

- **If it does NOT exist**:
  - The agent is idle. Start the daemon listener to wait for new remote prompts.

### 2. Spawning the Daemon Listener
To listen for new remote prompts while you are idle, run the daemon script in the background:
```powershell
python -m antigravity_remote.agent_daemon
```
*Note: Make sure to launch this command in the background (using `run_command` with high async wait time). Once launched, do not call any more tools and end your turn. The IDE will automatically wake you up when the daemon detects a prompt and exits.*
