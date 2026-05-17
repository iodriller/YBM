# Getting Started

Use one command for the normal local workflow:

```powershell
.\scripts\start_stack.ps1
```

That command:

- starts `C:\for fun\LocalDeploy\api_server.py` if the local LLM API is not already listening on `127.0.0.1:8000`
- initializes `agent_control.db`
- starts the FastAPI backend on `127.0.0.1:8765`
- starts Telegram polling
- starts the task worker

Open the admin UI:

```text
http://127.0.0.1:8765/admin
```

Stop the YBM processes started by the stack script:

```powershell
.\scripts\stop_stack.ps1
```

## Required Local Config

Telegram still needs a bot token and allowlist:

```powershell
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

Set `allowed_user_ids` and `allowed_chat_ids` in the admin UI or `config/config.yaml`.

The default orchestrator profile is local:

```text
Profile: localdeploy_gemma3_4b
Model: gemma3_4b_ollama_safe
Base URL: http://127.0.0.1:8000/v1
API Key Env: blank
```

Keep `OPENAI_API_KEY` in `.env` if you want it saved for fallback. It is not used by the default local profile.

## Telegram Gateway Behavior

Telegram text is now handled as a gateway:

- Plain `status` and `/status` return deterministic task status.
- `/tasks`, `/task <id>`, `/logs <id>`, `/pause <id>`, `/resume <id>`, and `/cancel <id>` are command routes.
- Non-task messages such as `what can you do?` get a direct local LLM answer with current capability and task context.
- Messages classified as executable tasks are persisted and picked up by the worker.
- Development tasks use the VS Code/GitHub Copilot terminal route when VS Code write access is enabled.
- Worker completion, failure, blocked, cancelled, and approval-needed states are sent back to the source Telegram chat.

## Copilot Route

For the minimal Copilot path:

1. Keep the backend running from `start_stack.ps1`.
2. Install/run the VS Code bridge extension.
3. Enable the VS Code adapter and `vscode.write_files` capability in local config or admin access modes.
4. Ensure GitHub CLI Copilot works in a local terminal:

```powershell
gh copilot -p "Write a small Python hello world script"
```

The worker first tries the VS Code bridge. If the bridge has no active state, it falls back to the local Copilot CLI and still returns captured output to Telegram.

The default command is:

```text
gh copilot -p '<task prompt>'
```

When the WinGet Copilot CLI path is available, the backend uses the full `copilot.exe` path so it does not depend on a freshly restarted shell `PATH`.

VS Code terminal output capture depends on VS Code shell integration. If shell integration is unavailable but the local Copilot CLI is installed, the fallback captures stdout/stderr directly.

## Useful Debug Commands

```powershell
.\scripts\run_tests.ps1
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8000/health
```

Logs from the one-command stack are written under:

```text
.agent_control/logs
```

## Where To Look

- Telegram intake/classification: `backend/src/agent_control/channels/telegram.py`
- Direct Telegram LLM answers: `backend/src/agent_control/channels/responder.py`
- Telegram task completion messages: `backend/src/agent_control/channels/telegram_notifications.py`
- Worker pickup loop: `backend/src/agent_control/orchestration/worker.py`
- Default VS Code development plan: `backend/src/agent_control/orchestration/default_plans.py`
- VS Code bridge API and adapter: `backend/src/agent_control/tools/vscode_bridge.py`
- Flow diagram: `docs/TASK_FLOW.md`
