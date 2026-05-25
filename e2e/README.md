# Live Telegram E2E Tests

These tests send real Telegram user messages to the bot, wait for the backend
to create and finish tasks, query the admin trace, and write full case logs.

## Why Telethon Is Required

Telegram Bot API can't *create* an inbound user message to your bot. A true
Telegram E2E test needs a user session through MTProto. This harness uses
Telethon for that and still uses the backend admin API for trace evidence.

## Setup

Start the stack first:

```powershell
.\scripts\start_stack.ps1
```

Install the optional E2E dependency:

```powershell
pip install -e .\backend[e2e]
```

Set these environment variables (or put them in `.env` at the repo root):

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

On first run, Telethon may ask for your phone number and Telegram login code.
That creates the local session file. You can also create the session
explicitly:

```powershell
.\scripts\login_telegram_e2e.ps1
```

After the session file exists, the runner can run unattended.

## Run

Run all non-guarded cases from the consolidated catalogue:

```powershell
python scripts/run_all_e2e_tests.py
```

Filter by case id:

```powershell
python scripts/run_all_e2e_tests.py --only browser_dizibox_new_shows,desktop_inspection
```

Filter by size (`small`, `medium`, `long-running`):

```powershell
python scripts/run_all_e2e_tests.py --sizes small,medium
```

Include guarded cases (codex / copilot / external quota — usually need
credentials we don't have locally):

```powershell
python scripts/run_all_e2e_tests.py --include-guarded
```

## Case catalogue

`all_cases.json` is the single source of truth. Each case declares its message,
required fixtures, expected behavior, pass criteria, and any follow-up turns.
Add new cases there; the runner picks them up automatically.

`fixtures.py` builds the Desktop folders, mock documents, and optional local
static-file web server that the case templates reference (e.g.
`{{documents_folder}}`, `{{episode_url}}`).

## Logs

Every run writes:

```text
.agent_control/e2e_results/run_<timestamp>/
    summary.md           # at-a-glance pass/fail table
    summary.json         # machine-readable
    <NN>_<case_id>/
        result.json      # full structured result
        timeline.txt     # human-readable status flow + plan + answer
        audit.json       # every audit event for the task
        diagnosis.md     # only present for failed stages — explains why
```

The summary is rewritten after every stage, so a partial run is still useful.
