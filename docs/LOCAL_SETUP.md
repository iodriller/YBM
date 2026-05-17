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

Safe defaults keep terminal execution, filesystem access, VS Code access, desktop screenshots, browser automation, dependency installation, and Git pushes disabled.

## 3. Initialize Local Database

```powershell
.\scripts\init_db.ps1
```

## 4. Run Backend

```powershell
.\scripts\run_backend.ps1
```

This also starts LocalDeploy from `C:\for fun\LocalDeploy` when the local LLM API is not already running.

Backend health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

## 5. Run Tests

```powershell
.\scripts\run_tests.ps1
```

## 6. Package VS Code Extension

```powershell
.\scripts\package_vscode_extension.ps1
```

The extension sends workspace state to the local backend and polls for queued terminal commands.

## 7. Run Telegram Polling

After setting `TELEGRAM_BOT_TOKEN` and enabling/configuring Telegram allowlists:

```powershell
.\scripts\run_telegram_polling.ps1
```

## 8. Run Task Worker

```powershell
.\scripts\run_worker.ps1
```

The worker is required for persisted tasks to be planned and executed.

## 9. Current Limits

- Screenshot capture uses Pillow and is disabled by default.
- VS Code terminal stdout capture depends on VS Code shell integration. Without it, the bridge records dispatch completion only.
