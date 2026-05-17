# Telegram To Worker Flow

## Diagram

```mermaid
flowchart TD
    A[Telegram message] --> B[TelegramPollingRunner]
    B --> C[TelegramAdapter allowlist check]
    C --> D[TelegramIntakeService stores message]
    D --> X{Plain command?}
    X -->|status/tasks| Y[Deterministic Telegram response]
    X -->|normal text| E[LLMMessageClassifier]
    E -->|is_task=false| F[LLMTelegramResponder answers with runtime context]
    E -->|is_task=true| G[TaskRepository creates task: received]
    G --> H[TaskWorker run-worker loop]
    H --> I{Task type and access}
    I -->|development + VS Code write enabled| J[Default VS Code development plan]
    I -->|other| K[PlannerService asks local LLM for plan]
    J --> L[ToolExecutor policy check]
    K --> L
    L -->|approval needed| M[Approval request]
    M --> H
    L -->|allowed| N[VSCodeBridgeTerminalAdapter]
    N --> U{VS Code state connected?}
    U -->|yes| O[Backend /vscode/terminal-commands]
    U -->|no| V[Local Copilot CLI fallback]
    O --> P[VS Code extension polls command]
    P --> Q[VS Code terminal runs command]
    Q --> R[Backend /vscode/terminal-output]
    R --> N
    V --> N
    N --> S[Task completed or failed]
    S --> T[TelegramTaskNotifier sends result to source chat]
```

## Task Types Today

The classifier writes these task types from `TaskType`:

- `development`
- `configuration`
- `admin_control`
- `desktop_observation`
- `question`
- `status_request`
- `other`

Only messages classified with `is_task=true` become persisted tasks.

## Current Pickup Rules

Tasks are created with status `received`. They are picked up only by:

```powershell
.\scripts\start_stack.ps1
```

The stack script starts the worker. For debugging, `.\scripts\run_worker.ps1` starts only the worker.

The worker processes `received`, `interpreting`, `planned`, `awaiting_approval`, `running`, and `retrying` tasks. It skips `paused`, `cancelled`, `completed`, `failed`, and `blocked` tasks unless a control action changes their status.

## VS Code And Copilot Notes

The implemented minimal bridge path queues a command into the VS Code integrated terminal and waits for a matching terminal-output record. If no VS Code state is connected, the adapter falls back to running the local Copilot CLI directly and still reports the captured output. By default, the deterministic development plan uses:

```text
gh copilot -p '<task prompt>'
```

This requires GitHub CLI Copilot to be installed and authenticated if you want actual Copilot output. VS Code terminal output capture requires VS Code shell integration; without shell integration, the extension records a final dispatch message instead of command stdout.

The remaining gap is direct GitHub Copilot Chat response capture inside VS Code. The public bridge here can open/run terminal commands, but it does not have a stable public API for reading Copilot Chat answers from the chat panel.
