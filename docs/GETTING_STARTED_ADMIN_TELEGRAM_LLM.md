# Getting Started

This project now defaults to the local LocalDeploy Gemma 3 4B profile. Keep `OPENAI_API_KEY` in `.env` if you want it saved for fallback, but the active profile should be:

```text
Profile: localdeploy_gemma3_4b
Model: gemma3_4b_ollama_safe
Base URL: http://127.0.0.1:8000/v1
API Key Env: blank
```

## 1. Start The Local Stack

From `C:\for fun\YBM`:

```powershell
.\scripts\init_db.ps1
.\scripts\run_backend.ps1
```

`run_backend.ps1` starts `C:\for fun\LocalDeploy\api_server.py` first if `http://127.0.0.1:8000/health` is not responding.

Open:

```text
http://127.0.0.1:8765/admin
```

## 2. Start Telegram Intake

In another terminal:

```powershell
.\scripts\run_telegram_polling.ps1
```

Telegram setup still needs `TELEGRAM_BOT_TOKEN`, `allowed_user_ids`, and `allowed_chat_ids` in the admin UI or config.

## 3. Start The Worker

In a third terminal:

```powershell
.\scripts\run_worker.ps1
```

The worker is the process that picks up persisted tasks. Without it, Telegram messages can create tasks, but the tasks remain queued in the database.

## 4. Optional VS Code Task Handoff

Install/run the VS Code bridge extension, then enable VS Code bridge access in the admin UI. For the minimal Telegram to VS Code path, set the VS Code access group to `Full write`.

When a Telegram message is classified as a development task and VS Code write access is enabled, the worker creates a one-step plan that queues the objective to the VS Code bridge terminal. Terminal output is captured when VS Code shell integration is available; otherwise the bridge records that the command was dispatched but output capture was unavailable.

## 5. Where To Look

- Telegram intake and classification: `backend/src/agent_control/channels/telegram.py`
- LLM classifier: `backend/src/agent_control/llm/classifier.py`
- Task statuses and task types: `backend/src/agent_control/schemas.py`
- Worker pickup loop: `backend/src/agent_control/orchestration/worker.py`
- Default VS Code development plan: `backend/src/agent_control/orchestration/default_plans.py`
- VS Code bridge API and adapter: `backend/src/agent_control/tools/vscode_bridge.py`
- Admin UI controls: `backend/src/agent_control/admin.py`
- Flow diagram: `docs/TASK_FLOW.md`
