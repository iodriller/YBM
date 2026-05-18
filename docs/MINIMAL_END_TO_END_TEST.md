# Minimal End-To-End Test

The smallest useful end-to-end test right now is:

```text
Telegram web-app request -> backend polling -> local LLM classification -> conversation memory update -> persisted task -> worker pickup -> Copilot creator step when enabled -> generated workspace -> localhost preview URL -> Telegram result
```

This proves Telegram auth, polling, local LLM classification, persistence, audit logging, worker execution, admin monitoring, and visible local output are connected.

## Prerequisites

- Backend dependencies installed.
- A Telegram bot token from `BotFather`.
- Your Telegram user ID and chat ID.
- LocalDeploy running or startable from `C:\for fun\LocalDeploy`.

## 1. Add The Telegram Token

Create or update `.env` in the repo root:

```powershell
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

## 2. Start Backend And Admin UI

```powershell
.\scripts\start_stack.ps1
```

Open:

```text
http://127.0.0.1:8501
```

The legacy FastAPI admin page remains available at `http://127.0.0.1:8765/admin`.

## 3. Confirm The Local Orchestrator LLM

The default profile is `localdeploy_gemma3_4b`, pointing at `http://127.0.0.1:8000/v1`. In the Orchestrator LLM panel, click `Test`.

The test must pass before Telegram text can spawn tasks, because text messages are classified by the local orchestrator LLM.

## 4. Configure Telegram In Admin

In the Telegram panel:

```text
Enabled: checked
Token Env: TELEGRAM_BOT_TOKEN
User IDs: your Telegram message.from.id
Chat IDs: your Telegram message.chat.id
Polling: checked
```

Click `Save`.

## 5. Send A Task From Telegram

Send this to your bot:

```text
create a modern web app about ferrets and launch it
```

Then send:

```text
/tasks
```

Expected result:

- `/tasks` replies with the new task.
- The first message receives `Task spawned: <task_id>`.
- The admin UI Tasks section shows the task activity moving from queued to active or completed.
- When the worker completes, Telegram receives a localhost URL and workspace path.
- The generated files are under `.agent_control/workspaces/task_<id>`.
- If VS Code/Copilot write access is enabled, the plan includes a Copilot creator step before workspace serving.
- The admin UI Audit section shows raw message, classification, and spawned task events.

## 6. Check From PowerShell

```powershell
Invoke-RestMethod http://127.0.0.1:8765/admin/api/summary | ConvertTo-Json -Depth 8
```

Look for:

```text
tasks[0].objective
integrations.telegram.enabled
integrations.telegram.token_present
```

## 7. Stop The Test

```powershell
.\scripts\stop_stack.ps1
```
