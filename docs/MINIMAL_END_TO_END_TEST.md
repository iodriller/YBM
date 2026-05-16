# Minimal End-To-End Test

The smallest useful end-to-end test right now is:

```text
Telegram message -> backend polling -> persisted task -> admin UI visibility -> Telegram /tasks response
```

This proves Telegram auth, polling, persistence, audit logging, and admin monitoring are connected.

## Prerequisites

- Backend dependencies installed.
- A Telegram bot token from `BotFather`.
- Your Telegram user ID and chat ID.

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

## 3. Configure Telegram In Admin

In the Telegram panel:

```text
Enabled: checked
Token Env: TELEGRAM_BOT_TOKEN
User IDs: your Telegram message.from.id
Chat IDs: your Telegram message.chat.id
Polling: checked
```

Click `Save`.

## 4. Start Telegram Polling

Terminal 2:

```powershell
.\scripts\run_telegram_polling.ps1
```

## 5. Send A Task From Telegram

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
- The admin UI Tasks section shows the task.
- The admin UI Audit section shows message/task events.

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

## 7. Optional LLM Smoke Test

In the admin UI, configure the Orchestrator LLM section.

For local LLM:

```text
Provider: openai_compatible
Base URL: http://127.0.0.1:<your-port>/v1
Model: exact model name from your local server
API Key Env: blank unless required
```

Click `Save`, then `Test`.

Expected result:

```text
ok
```

This validates LLM connectivity only. It does not yet prove full autonomous coding execution.

## 8. Stop The Test

Stop polling and backend with `Ctrl+C` in their terminals.

If the backend was launched in the background, stop the Python process that is running uvicorn.
