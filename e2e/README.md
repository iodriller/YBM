# Live Telegram E2E Tests

These tests are intentionally separate from `backend/tests`. They send real Telegram user messages to the bot, wait for the backend to create and finish tasks, query the admin trace, and write full case logs.

## Why Telethon Is Required

Telegram Bot API cannot create an inbound user message to your bot. A true Telegram E2E test needs a user session through MTProto. This harness uses Telethon for that part and still uses the backend admin API for trace evidence.

## Setup

Start the stack first:

```powershell
.\scripts\start_stack.ps1
```

Install the optional E2E dependency:

```powershell
pip install -e .\backend[e2e]
```

Set these environment variables:

```powershell
$env:TELEGRAM_API_ID="your Telegram API id"
$env:TELEGRAM_API_HASH="your Telegram API hash"
$env:TELEGRAM_BOT_USERNAME="@your_bot_username"
$env:TELEGRAM_USER_SESSION=".agent_control/telegram_e2e_user"
```

If admin auth is enabled:

```powershell
$env:AGENT_ADMIN_TOKEN="your admin token"
```

On first run, Telethon may ask for your phone number and Telegram login code. That creates the local session file.

You can create the session explicitly:

```powershell
.\scripts\login_telegram_e2e.ps1
```

After the session file exists, the live E2E runner can run unattended.

## Run

List cases:

```powershell
python e2e/live_telegram_e2e.py --list-cases
```

List extended development cases:

```powershell
python e2e/live_telegram_e2e.py --cases e2e/extended_cases.json --list-cases
```

Dry-run resolved messages:

```powershell
.\scripts\run_live_e2e.ps1 -Case desktop_observation -DryRun
```

Dry-run an extended case:

```powershell
python e2e/live_telegram_e2e.py --cases e2e/extended_cases.json --case folder_mixed_file_explanation --dry-run
```

Run a safe real case:

```powershell
.\scripts\run_live_e2e.ps1 -Case desktop_observation
```

Run all non-guarded cases:

```powershell
.\scripts\run_live_e2e.ps1 -All
```

Run guarded Codex/Copilot/long/limit cases:

```powershell
.\scripts\run_live_e2e.ps1 -All -IncludeGuarded
```

## Logs

Every run writes:

```text
.agent_control/live_e2e_runs/<timestamp>/
  preflight.json
  <case_id>.json
  summary.json
  summary.md
```

Each case log includes:

- Resolved Telegram message.
- Telegram bot replies and whether media was sent.
- Task ID and terminal status.
- Plan steps and route decision.
- Tool invocations and outputs.
- Artifacts, local paths, URLs, schedules, and timeline.
- Assertion failures explaining what evidence was missing.

These logs are the debugging contract. A failure should identify whether the break was Telegram intake, classification/routing, policy, tool execution, artifact delivery, notification, or validation.
