# Configurable Agentic Control System - Phased Approach

## 1. Product Direction

Build a local-first, Telegram-driven control plane for a personal desktop and development environment. The system receives text or voice commands, turns them into structured tasks, reasons over those tasks through a configurable LLM layer, and executes only the actions that are allowed by explicit configuration.

The first useful version targets Telegram plus VS Code. Future versions can add more communication channels, more development tools, browser automation, desktop control, and richer agent scheduling without rewriting the core orchestrator.

## 2. Architecture Principles

- Secure by default: invasive capabilities are disabled unless explicitly enabled.
- Configuration-driven: adapters, models, scopes, approvals, limits, and retention are controlled by config.
- Structured internally: agent messages, plans, tool calls, approvals, observations, and results use Pydantic models.
- Assistant-agnostic: Codex, Copilot, terminal agents, VS Code extensions, and local tools are adapters.
- Auditable: important decisions, tool calls, approvals, failures, and artifacts are persisted.
- Recoverable: long-running tasks can pause, resume, retry, or ask for human intervention.

## 3. MVP Defaults

- Backend: Python 3.12+
- Data validation: Pydantic v2 and pydantic-settings
- API/service layer: FastAPI
- Telegram adapter: aiogram 3.x
- Local state: SQLite
- VS Code integration: TypeScript extension with authenticated localhost bridge
- Coding assistant integration: generic terminal-agent adapter first
- Voice transcription: pluggable STT adapter
- Desktop visibility: screenshot-only adapter, disabled by default
- Desktop control: out of MVP and disabled

## 3.1 Gap Review Items

These items were found during implementation review and should stay explicit in the plan:

- Keep bytecode, caches, local databases, artifacts, `.env`, and dependency folders out of git.
- Scope checks must be path-boundary aware; prefix-only checks are not acceptable.
- Tool completion audit events must include the task ID.
- Tasks waiting for approval must have a minimal resume path after approval.
- Telegram commands such as `/status`, `/tasks`, `/task`, `/logs`, and `/screenshot` need notification-layer responses before MVP completion.
- VS Code terminal control needs an explicit backend command queue; extensions should poll and dispatch commands instead of relying on UI scraping.
- VS Code terminal output capture through official APIs is limited; record command dispatch and use terminal-agent subprocess adapters for reliable output.
- The initial database bootstrap is acceptable for MVP, but real schema versioning/migrations are needed before broader use.

## 4. Phase 0 - Project Bootstrap

Create the project structure, package metadata, safe config examples, and baseline docs.

Primary outputs:

- Monorepo folder structure
- Backend package scaffold
- VS Code extension scaffold
- Example environment and YAML configuration
- Initial tests and developer scripts

Success criteria:

- Backend imports successfully.
- Test runner starts.
- VS Code extension has a clear placeholder structure.
- Example config keeps all invasive actions disabled.

## 5. Phase 1 - Shared Schemas And Configuration

Define the structured language used inside the system.

Primary outputs:

- Pydantic models for messages, tasks, plans, tool calls, approvals, capabilities, artifacts, and audit events.
- Strict configuration models with unknown-key rejection.
- Safe defaults for every capability.
- Redacted config summary helpers.

Success criteria:

- Invalid configuration fails at startup.
- Unknown config keys are rejected.
- Disabled capabilities are represented explicitly.
- Secrets are never shown in config summaries.

## 6. Phase 2 - Persistence, Audit Log, And Task State

Add durable local state.

Primary outputs:

- SQLite schema for conversations, messages, tasks, subtasks, approvals, tool calls, artifacts, and audit events.
- Repository layer that hides SQL details from orchestration.
- Append-only audit event writer.
- Redaction helpers for secrets and long payloads.

Success criteria:

- A task can be created, updated, paused, resumed, cancelled, and completed.
- Every state transition writes an audit event.
- Audit payloads are redacted before storage.

## 7. Phase 3 - Telegram Channel Adapter

Connect Telegram as the first communication channel.

Primary outputs:

- Telegram polling adapter for local development.
- Allowlist checks for user IDs and chat IDs.
- Text message normalization.
- Bot commands for status, tasks, pause, resume, cancel, logs, and screenshots.
- Inline approval buttons.

Success criteria:

- Unauthorized users are denied and audited.
- A text message creates a task.
- Inline buttons create structured task signals.

## 8. Phase 4 - Voice Transcription Pipeline

Support voice commands from Telegram.

Primary outputs:

- Voice message detection.
- Telegram file download through `getFile`.
- STT adapter interface.
- Transcript artifact support.
- Transcript-to-task flow.

Success criteria:

- Voice input creates the same task type as text input.
- Failed transcription produces a clear Telegram response.
- Voice retention follows configuration.

## 9. Phase 5 - LLM Provider Abstraction And Structured Planner

Introduce configurable reasoning.

Primary outputs:

- Provider interface for text, structured, and streaming generation.
- OpenAI-compatible HTTP provider.
- Configurable model profiles.
- Plan generation using validated Pydantic output.
- Clarification detection.

Success criteria:

- A user request becomes a structured plan.
- The plan declares required capabilities.
- Invalid LLM output is rejected before execution.

## 10. Phase 6 - Permission And Approval Engine

Enforce safety between planning and execution.

Primary outputs:

- Capability registry.
- Risk levels.
- Scope checks.
- Approval rules.
- Signed approval tokens.

Success criteria:

- Disabled capabilities cannot run.
- Sensitive actions pause until approval.
- Rejected approvals are persisted and surfaced to the task manager.

## 11. Phase 7 - Task Orchestrator And Worker Loop

Coordinate long-running tasks.

Primary outputs:

- Durable task state machine.
- Worker loop.
- Task signals for pause, resume, cancel, approve, reject, redirect, and status.
- Notification checkpoints.

Success criteria:

- Long-running tasks survive backend restart.
- Paused tasks stop making new tool calls.
- Resumed tasks continue from the next safe checkpoint.

## 12. Phase 8 - VS Code Extension Bridge

Connect the orchestrator to VS Code through supported APIs.

Primary outputs:

- VS Code extension.
- Authenticated localhost bridge.
- Workspace observation commands.
- Terminal command queue, terminal creation, and command-dispatch observations.
- Extension heartbeat.

Success criteria:

- Backend can see VS Code connection status.
- Backend can request workspace state.
- Backend can enqueue terminal commands for the extension to dispatch.
- Terminal actions remain blocked unless enabled and routed through policy.
- Bridge requests are authenticated when a bridge token is configured.

## 13. Phase 9 - Coding Assistant Adapter

Drive coding tools without hardcoding one assistant.

Primary outputs:

- Generic terminal-agent adapter.
- Command template configuration.
- Output streaming.
- Completion, failure, waiting, rate-limit, and usage-limit detection.

Success criteria:

- The orchestrator can start a configured terminal assistant.
- Assistant output is persisted as task history.
- Rate limits can trigger wait or fallback behavior.
- The adapter is never called directly by channel code; it runs only behind policy-gated tool execution.

## 14. Phase 10 - Observation, Screenshots, And Artifacts

Make task progress visible from Telegram.

Primary outputs:

- Artifact service.
- Screenshot adapter behind `desktop.screenshot`.
- Telegram artifact delivery.
- Retention policy support.

Success criteria:

- `/screenshot` works only when enabled.
- Denied screenshot attempts are audited.
- Task summaries can include artifact references.
- Screenshot artifacts are persisted to local artifact storage.
- Telegram can deliver screenshot artifacts when produced by `/screenshot`.

## 15. Phase 11 - Error Recovery, Limits, And Resume

Handle failures without abandoning work.

Primary outputs:

- Error classification.
- Retry policy.
- Backoff scheduling.
- Rate-limit and usage-limit handling.
- Human intervention summaries.

Success criteria:

- Transient failures retry.
- Repeated failures request intervention.
- Usage limits pause or switch tools according to configuration.
- Retry state is persisted on the task so it survives process restarts.

## 16. Phase 12 - Tests And Security Validation

Build confidence around the safety boundary.

Primary outputs:

- Schema tests.
- Config tests.
- Permission tests.
- Telegram adapter integration tests with fakes.
- VS Code bridge contract tests.
- Security-deny tests.

Success criteria:

- MVP flows are covered.
- Denied capabilities are tested as first-class behavior.
- No test requires real Telegram, real VS Code, or a real LLM by default.

## 17. Phase 13 - Packaging And Local Deployment

Make the system usable on a local machine.

Primary outputs:

- Backend service commands.
- Worker startup commands.
- Telegram polling command.
- VS Code `.vsix` packaging instructions.
- Windows-friendly setup docs.

Success criteria:

- A fresh local setup can run from documented steps.
- Safe config works without terminal, filesystem write, browser, desktop control, or Git push.
- Enabling any invasive capability requires an explicit config change.
- Windows scripts exist for database initialization, backend startup, tests, and VS Code extension packaging.
- A local Telegram polling script exists for MVP operation.

## 18. MVP Acceptance Criteria

- Telegram text creates a persisted task.
- Telegram voice creates a transcribed persisted task.
- The configured LLM creates a structured plan.
- The user can approve, reject, pause, resume, cancel, and request status.
- VS Code extension connects and reports workspace state.
- Generic terminal assistant runs only when terminal access is enabled.
- Screenshots work only when explicitly enabled.
- Important decisions and actions are audited.

## 19. Later Roadmap

- Add Slack and Discord channel adapters.
- Add browser automation adapter.
- Add GitHub issue and pull request adapter.
- Add stronger desktop streaming.
- Add web dashboard.
- Add multi-agent scheduling.
- Add distributed workers with Redis, Dramatiq, or Temporal.
- Add richer Codex, Copilot, and VS Code assistant adapters where official APIs allow.
