# Minimal End-To-End Test

The smallest useful end-to-end test right now is:

```text
Telegram message -> backend polling -> LLM classification -> persisted task -> admin UI visibility -> Telegram /tasks response
```

This proves Telegram auth, polling, orchestrator LLM classification, persistence, audit logging, and admin monitoring are connected.

## Prerequisites

- Backend dependencies installed.
- A Telegram bot token from `BotFather`.
- Your Telegram user ID and chat ID.
- A configured orchestrator LLM profile that supports structured JSON output.

## 1. Add The Telegram Token

Create or update `.env` in the repo root:

```powershell
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

## 2. Start Backend And Admin UI

Terminal 1:

```powershell
.\scripts\init_db.ps1
.\scripts\run_backend.ps1
```

Open:

```text
http://127.0.0.1:8765/admin
```

## 3. Configure The Orchestrator LLM

In the Orchestrator LLM panel, configure either a cloud or local OpenAI-compatible endpoint and click `Test`.

The test must pass before Telegram text can spawn tasks, because text messages are classified by the orchestrator LLM.

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

## 5. Start Telegram Polling

Terminal 2:

```powershell
.\scripts\run_telegram_polling.ps1
```

## 6. Send A Task From Telegram

Send this to your bot:

```text
minimal e2e test: create a task from Telegram
```

Then send:

```text
/tasks
```

Expected result:

- `/tasks` replies with the new task.
- The first message receives `Task spawned: <task_id>`.
- The admin UI Tasks section shows the task.
- The admin UI Audit section shows raw message, classification, and spawned task events.

## 7. Check From PowerShell

```powershell
Invoke-RestMethod http://127.0.0.1:8765/admin/api/summary | ConvertTo-Json -Depth 8
```

Look for:

```text
tasks[0].objective
integrations.telegram.enabled
integrations.telegram.token_present
```

## 8. Stop The Test

Stop polling and backend with `Ctrl+C` in their terminals.

If the backend was launched in the background, stop the Python process that is running uvicorn.
