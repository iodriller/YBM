# AGENTS.md

## Project

YBM is a configurable local agent-control system. A FastAPI/Python backend accepts
Telegram and local requests, applies policy, schedules work, invokes configured tools
or coding agents, and exposes a Streamlit admin UI. A VS Code extension provides the
editor bridge.

Use these as the durable sources of truth:

- `docs/ARCHITECTURE.md` for current components and message flow.
- `docs/HISTORY.md` for design rationale and completed phases.
- `config/config.example.yaml` for supported configuration.
- `README.md` for operator-facing setup and commands.

Do not describe planned behavior as implemented. Keep documentation, schemas, and
configuration examples aligned with the code that exists.

## Supported Commands

Use `scripts/ybm.ps1` instead of assembling service commands by hand:

```powershell
.\scripts\ybm.ps1 setup
.\scripts\ybm.ps1 doctor
.\scripts\ybm.ps1 start
.\scripts\ybm.ps1 status
.\scripts\ybm.ps1 logs worker -Follow
.\scripts\ybm.ps1 stop
.\scripts\ybm.ps1 test
```

Additional operator workflows:

```powershell
.\scripts\ybm.ps1 e2e --only desktop_inspection
.\scripts\ybm.ps1 trace <task_id>
.\scripts\ybm.ps1 scenario record <name>
.\scripts\ybm.ps1 package-extension
```

`scenario record` makes a real LLM call. Live E2E requires the setup documented in
`e2e/README.md`; do not run either workflow implicitly.

## Architecture Boundaries

- `backend/src/agent_control/` owns domain models, policy, orchestration, adapters,
  persistence, and API behavior.
- `backend/tests/` contains unit and deterministic scenario coverage.
- `vscode-extension/` is a TypeScript bridge; keep editor-specific behavior out of
  the backend domain layer.
- `scripts/ybm.ps1` is the public lifecycle interface. Keep service scripts behind it.
- `config/config.example.yaml` is safe, committed configuration. Local
  `config/config.yaml` and `.env` are private runtime state.
- `.agent_control/`, `backend/agent_control.db`, logs, screenshots, generated
  workspaces, caches, `.venv`, and `node_modules` are generated and must not be
  committed.

Keep tool execution policy-bound. Preserve approval gates, allowlists, workspace
boundaries, bounded retries, redaction, and the disabled-by-default settings for
terminal, filesystem, browser, desktop control, dependency installation, and Git
pushes. Never log secret values or place them in task output.

Treat prompt, tool-schema, and workspace-layout changes as behavioral changes.
Update or re-record only the affected scenario fixtures, review the generated fixture,
and never hide a degraded fallback path.

## Change Style

- Inspect the relevant code, configuration, and logs before editing.
- Define the expected outcome and make the smallest coherent change.
- Reuse existing registry, policy, adapter, and supervisor patterns.
- Fix a shared root cause instead of adding prompt-specific or example-specific
  branches.
- Preserve unrelated local changes and avoid speculative refactors.
- Distinguish observed facts, inferences, and unverified assumptions.

## Verification

Choose checks in proportion to the change:

- Documentation or agent-guidance only: verify referenced paths and commands, then
  run `git diff --check`; application tests are not required.
- Backend behavior: `.\scripts\ybm.ps1 test` and the focused affected test.
- Python quality: from `backend`, run `uv run --frozen ruff check .`.
- VS Code extension: from `vscode-extension`, run `npm run compile`.
- Full runtime or integration changes: run `doctor`, then the narrowest relevant
  live or E2E flow only when its prerequisites and external effects are understood.

Never claim a check ran unless its output was observed. Report skipped checks and why.

## Git and Handoff

- Keep commits focused and use the configured repository-owner identity.
- Do not add assistant names, co-author trailers, session links, or tool attribution
  to commits, branches, pull requests, or release notes.
- Do not enable external writes, installs, desktop control, or Git pushes merely
  because a task could benefit from them; require explicit scope and existing policy.
- Finish with: what changed, what was verified, what was not verified, and remaining
  risks or next steps.
