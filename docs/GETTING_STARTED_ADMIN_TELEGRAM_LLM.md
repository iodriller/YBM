# Getting Started

Use one command for the normal local workflow:

```powershell
.\scripts\start_stack.ps1
```

That command:

- starts `C:\for fun\LocalDeploy\api_server.py` if the local LLM API is not already listening on `127.0.0.1:8000`
- initializes `agent_control.db`
- starts the FastAPI backend on `127.0.0.1:8765`
- starts the Streamlit admin UI on `127.0.0.1:8501`
- starts Telegram polling
- starts the task worker
- uses `.agent_control/workspaces` as the default generated-file workspace

Open the admin UI:

```text
http://127.0.0.1:8501
```

The legacy FastAPI admin page is still available at `http://127.0.0.1:8765/admin`.

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
Profile: localdeploy_gemma3_12b
Model: gemma3_12b_ollama_safe
Base URL: http://127.0.0.1:8000/v1
API Key Env: blank
```

Keep `OPENAI_API_KEY` in `.env` if you want it saved for fallback. It is not used by the default local profile.

Configuration rule:

- Put normal runtime settings in `config/config.yaml`.
- Put only secrets in `.env`.
- The admin UI follows that rule: non-secret settings are saved to YAML, and token/key fields are saved to `.env` only when you provide a replacement secret.

The Streamlit UI uses the same `/admin/api/*` backend endpoints as the legacy page. It improves the operator experience with tabs for operations, tasks, configuration, audit, and diagnostics; task trace expanders; structured tables; and safer form submissions while keeping the backend behavior unchanged.

The admin LLM panel has two presets:

- `LocalDeploy Gemma 3 12B`: the default local profile.
- `LocalDeploy Gemma 3 4B`: available as a faster local fallback profile.
- `OpenAI GPT-4.1`: saved as an optional preset that uses `OPENAI_API_KEY`.

## Telegram Gateway Behavior

Telegram text is now handled as a gateway:

- Plain `status` and `/status` return deterministic task status.
- `/tasks`, `/task <id>`, `/logs <id>`, `/pause <id>`, `/resume <id>`, and `/cancel <id>` are command routes.
- Non-task messages such as `what can you do?` get a direct local LLM answer with current capability and task context.
- The local LLM receives a short capability/task context, a concise LLM-updated per-chat memory summary, and a small recent-turn window. It does not receive the whole Telegram conversation.
- Messages classified as executable tasks are persisted and picked up by the worker.
- Launchable web-app tasks use Copilot as the creator when VS Code write access is enabled, then materialize files in a configurable local workspace and return a preview URL.
- Development tasks use the VS Code/GitHub Copilot terminal route when VS Code write access is enabled.
- Requests for missing tools/adapters can be routed to a generated adapter proposal cache. Those proposals are review artifacts and are not executed until promoted into the registry.
- Worker completion, failure, blocked, cancelled, and approval-needed states are sent back to the source Telegram chat.

## Conversation Memory

Telegram memory is maintained by `ConversationMemoryService`. It keeps:

- a compact rolling summary
- a bounded recent-turn window
- update metadata in SQLite

The summary is updated with the active local LLM profile. Only the existing summary and recent-turn window are sent to the summarizer, so the gateway has continuity without bloating the local model context. If the summarizer fails or times out, the service falls back to a deterministic rolling summary.

## Local Workspace

For general development tasks, the worker prepares a dedicated workspace first when `filesystem.write` is enabled:

```text
.agent_control/workspaces/task_<id>
```

That workspace is passed as `cwd` to VS Code/Copilot work so generated code has a predictable place to land.

The workspace tool supports prepare, relative file writes, static preview launch, Copilot-output materialization, and the combined fallback web-app preview operation.

## Local Workspace Preview

For a Telegram message like:

```text
create a simple hello world web app and launch it
```

the worker creates a directory like:

```text
.agent_control/workspaces/task_<id>
```

When VS Code/Copilot write access is enabled, the worker first asks Copilot to create the app in that workspace. If Copilot cannot write files directly but returns code blocks, the worker materializes those blocks into `index.html`, `styles.css`, and `script.js` as needed. It then starts a localhost static server, opens the URL on the computer when enabled, and sends the URL plus workspace path back to Telegram. The same URL and path appear in the admin task row.

Configure it in admin under **Local Workspace**, or in YAML:

```yaml
adapters:
  workspace:
    enabled: true
    root_dir: .agent_control/workspaces
    web_host: 127.0.0.1
    web_port_start: 8890
    open_browser: true
```

The workspace route requires `filesystem.write` access. In admin, set **File system** to **Full write** for approval-free local workspace actions, or **Write with approval** if you want the worker to pause for approval.

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

Copilot CLI usage lines such as request or token counts are parsed and included in the stored tool result and Telegram completion message when the CLI prints them. If the CLI fails, the fallback retries once with a plain-text-only prompt and returns the error details if it still fails.

## Adapter Factory

`adapter.factory` is the safe path for a missing capability. It scaffolds generated adapter proposals under:

```text
.agent_control/adapters
```

If VS Code/Copilot is enabled, the default adapter plan can ask Copilot to refine the proposal in that cache directory. The runtime does not import or execute generated adapters automatically; promotion still means reviewing the proposal, adding tests, moving it into `backend/src/agent_control/tools`, and registering it in `backend/src/agent_control/tools/registry.py`.

## Fulfillment Checks

The worker now validates obvious action postconditions. For app requests that ask to launch, open, preview, start, serve, or provide a URL, the task is not considered done unless a preview URL is produced. If that postcondition is missing, the task is requeued once with a `fulfillment_gap` marker so the orchestrator can route it through the workspace preview path.

## Useful Debug Commands

```powershell
.\scripts\run_tests.ps1
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8000/health
.\scripts\run_admin_ui.ps1
```

Logs from the one-command stack are written under:

```text
.agent_control/logs
```

## Where To Look

- Telegram intake/classification: `backend/src/agent_control/channels/telegram.py`
- Direct Telegram LLM answers: `backend/src/agent_control/channels/responder.py`
- Conversation memory: `backend/src/agent_control/channels/memory.py`
- Telegram task completion messages: `backend/src/agent_control/channels/telegram_notifications.py`
- Streamlit admin UI: `backend/src/agent_control/admin_streamlit.py`
- FastAPI admin API and legacy page: `backend/src/agent_control/admin.py`
- Worker pickup loop: `backend/src/agent_control/orchestration/worker.py`
- Default VS Code development plan: `backend/src/agent_control/orchestration/default_plans.py`
- Tool registry: `backend/src/agent_control/tools/registry.py`
- Adapter factory: `backend/src/agent_control/tools/adapter_factory.py`
- Fulfillment checks: `backend/src/agent_control/orchestration/fulfillment.py`
- Local workspace previews: `backend/src/agent_control/tools/local_workspace.py`
- VS Code bridge API and adapter: `backend/src/agent_control/tools/vscode_bridge.py`
- Flow diagram: `docs/TASK_FLOW.md`
