# Local Setup

## 1. Configure Environment

Copy `.env.example` to `.env` and fill only the secrets you need.

Required for Telegram:

```powershell
TELEGRAM_BOT_TOKEN=...
```

Optional for VS Code bridge auth:

```powershell
VSCODE_BRIDGE_TOKEN=...
```

## 2. Configure The App

Start from `config/config.example.yaml`.

Safe defaults keep terminal execution, filesystem access, VS Code access, desktop screenshots, browser automation, dependency installation, and Git pushes disabled. The workspace adapter itself is available by default, but it only executes when `filesystem.write` is enabled for task workspaces and generated files.

## 3. Start The Stack

```powershell
.\scripts\start_stack.ps1
```

This initializes the database and starts LocalDeploy, backend, Telegram polling, and worker. Generated task workspaces default to `.agent_control/workspaces/task_<id>`.

Launchable app requests use Copilot first when VS Code write access is enabled, then the workspace adapter serves the result locally. Generated adapter proposals, when requested, are cached under `.agent_control/adapters` and are not loaded into runtime automatically.

## 4. Stop The Stack

```powershell
.\scripts\stop_stack.ps1
```

## 5. Lower-Level Commands

Use these only when debugging individual processes.

Initialize local database:

```powershell
.\scripts\init_db.ps1
```

Run backend:

```powershell
.\scripts\run_backend.ps1
```

This also starts LocalDeploy from `C:\for fun\LocalDeploy` when the local LLM API is not already running.

Backend health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

Run Telegram polling:

```powershell
.\scripts\run_telegram_polling.ps1
```

Run task worker:

```powershell
.\scripts\run_worker.ps1
```

## 6. Run Tests

```powershell
.\scripts\run_tests.ps1
```

## 7. Package VS Code Extension

```powershell
.\scripts\package_vscode_extension.ps1
```

The extension sends workspace state to the local backend and polls for queued terminal commands.

## 8. Current Limits

- Screenshot capture uses Pillow and is disabled by default.
- VS Code terminal stdout capture depends on VS Code shell integration. Without it, the bridge records dispatch completion only.
- Direct Copilot Chat panel scraping is not implemented; Copilot routing uses VS Code terminal command dispatch or the local Copilot CLI fallback.
