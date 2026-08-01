# YBM Control UI/UX Audit and Product Roadmap

Audited 2026-08-01 against the live local console, the React source, FastAPI admin API,
`docs/ARCHITECTURE.md`, `docs/CAPABILITIES.md`, and the current configuration schema.
This document separates current behavior from recommendations; roadmap items are not implemented
unless explicitly marked **shipped**.

## Outcome

YBM's strongest product idea is not generic chat. It is governed local agency: powerful tools,
runtime-owned approvals, and a trace that proves what happened. The console already exposes all
three, but the previous presentation made the product look like an unstyled settings utility.
The redesign establishes a clearer control-plane hierarchy:

1. **Chat** is the simple front door.
2. **Approvals** interrupt every route only when a decision is required.
3. **Tasks and traces** explain what ran and where it failed.
4. **Access** communicates safety posture before individual configuration.
5. **Settings** holds integrations and advanced operator controls.

## What Shipped in This Pass

- A semantic color system with a single blue interaction accent and distinct success, warning,
  information, and danger roles in both light and dark themes.
- A user-selectable light/dark/system theme that also applies to token entry and onboarding.
- A responsive shell: desktop navigation rail, mobile header, and mobile bottom navigation.
- A rebuilt chat surface with a multiline composer, useful empty state, clearer execution status,
  readable error state, and hard wrapping for URLs and long unbroken output.
- A redesigned Access page with an at-a-glance posture summary, explicit preset choices, and
  directly visible per-group modes instead of internal enum text.
- Consistent page hierarchy, width, spacing, cards, and semantic task-status badges across Tasks,
  Trace, Settings, Access, and Chat.
- Web-chat notification routing fixed: local chat IDs are no longer sent to Telegram.
- Credential-safe Telegram transport errors plus response-boundary secret redaction for current
  and historical admin data.

## Current Feature Coverage

| Area | Implemented today | Console coverage | Important gap |
|---|---|---|---|
| Agent runtime | Concierge, bounded Operator loop, Auditor, fulfillment checks, parallel calls, delegation | Task status, step list, advanced lane graph | The graph cannot connect a delegated/parallel lane to the exact spawning decision |
| Channels | Telegram text/voice and one local web conversation | Web chat plus Telegram settings | Web chat has no attachments, voice, multiple threads, edit/regenerate, or message queue |
| Safety | Capability policies, risk ceilings, allowlists, one-shot approvals, disabled-by-default tools | Persistent approval banner, Evidence Pack, Access posture and modes | Fine-grained scopes/patterns are read-only; no time-boxed grants or grant revocation list |
| Tooling | Filesystem, terminal, browser, computer use, workspace, coding agents, documents, schedules, MCP, HTTP, knowledge, persona, skills | Access groups, diagnostics, trace outputs | Tool health and availability are scattered; no consolidated capability-health matrix |
| Observability | Structured audit, task trace, evidence, token usage, cost data, CLI trace | Tasks, trace list/graph, audit viewer | No latency trend, failure-rate view, evaluation signal, or cross-task cost dashboard |
| Configuration | LLM profiles/presets, Telegram, VS Code, workspace, computer use, MCP summary | Settings forms and advanced mode | No per-role model, prompt overrides, delegate presets, or editable advanced policies |
| Secrets | Encrypted local vault and reference-based injection | List/add/delete/init without returning values | Rotation/last-used state and integration validation are absent |
| Operations | Setup, doctor, lifecycle scripts, logs, scheduler, supervised services | Health indicator and diagnostics | Two supervisor implementations remain; UI health is compact but not incident-oriented |
| Developer quality | Backend unit/scenario suite, Zod API parsing, frontend typecheck/build | Query devtools in development | No committed frontend unit, contract, accessibility, or Playwright regression suite |

## Competitive Review

The goal is not to imitate a general chat product. The useful patterns are:

- [Open WebUI's official feature set](https://docs.openwebui.com/features/chat-conversations/)
  establishes the interaction baseline for self-hosted chat: attachments, message queueing,
  conversation organization, and tools that stay inside the conversation. YBM should adopt the
  message ergonomics, not its multi-user breadth.
- [OpenHands' security model](https://docs.openhands.dev/sdk/guides/security) pairs explicit
  confirmation policies with risk analysis. YBM's runtime gates are already a strong match; the
  Access page should keep making the difference between "can run," "needs review," and "cannot
  run" visible before an action occurs.
- [Langfuse's agent graphs](https://langfuse.com/docs/observability/features/agent-graphs) offer
  aggregated and expanded views, while its
  [trace guidance](https://langfuse.com/docs/observability/best-practices) emphasizes correct
  nesting, cost, and token metadata. YBM has enough correlation data for a useful lane graph, but
  needs exact parent edges and an expandable tree to become a first-class debugger.
- [AnythingLLM's official documentation](https://docs.anythingllm.com/) combines workspaces,
  agents, model routing, and explicit flows. YBM should add named delegate presets and per-role
  models first. It should not show a drag-and-drop workflow builder until the backend pipeline is
  genuinely configurable.

## Gap Analysis by Priority

### P0 — Trust and regression safety

1. Add a committed Playwright suite for token entry, chat wrapping, theme persistence, mobile
   navigation, approval review, access-mode changes, and a failed-task trace.
2. Add frontend unit/contract tests for status mapping, API schemas, access preset computation,
   and secret masking. Today TypeScript and production build are the only automated UI gates.
3. Add a React error boundary. A render-time component failure can still blank the current route.
4. Audit every external adapter's exception path for credential-bearing URLs or command text.
   Telegram is fixed and admin responses redact configured values, but prevention should happen
   at every adapter boundary.

### P1 — Daily-use product quality

1. Render assistant answers as safe Markdown with code blocks, tables, copy actions, and link
   treatment. Preserve plain text as the fallback and sanitize HTML.
2. Add web-chat attachments backed by artifacts and scoped filesystem ingestion; do not bypass
   existing policy checks.
3. Add real task pagination and server-side search/filtering. The API exposes offsets, but the UI
   currently fetches and filters only the first 100 tasks.
4. Add a trace tree beside the graph: collapsed by default, failure opened automatically, exact
   parent/child edges, and duration per step.
5. Add a capability-health matrix combining configured access, adapter readiness, policy ceiling,
   and last failure. Access answers permission; Diagnostics answers availability; operators need
   both in one view.
6. Make advanced capability scopes and allow/deny patterns editable through validated backend
   endpoints with before/after confirmation.

### P2 — Differentiating control-plane features

1. Per-role models for Concierge, Operator, and Auditor, with estimated cost/latency impact.
2. Versioned prompt overrides with diff, reset, and an explicit scenario-fixture warning.
3. Named delegate presets such as Researcher or Coder, restricted to selected tools.
4. Time-boxed grants with exact tool/operation scope, capped TTL, visible expiry, and revocation.
5. Cross-task reliability and cost dashboards: failure reason, tool latency, retry count, token
   spend, model, and time window.
6. SSE for task and approval events after measuring current polling load; keep polling fallback.

### P3 — Expansion only after evidence

1. Multiple local chat threads, search, archive, and export.
2. Re-run/replay from a trace with a clear statement of what can cause side effects again.
3. Configurable workflow graphs only after the runtime becomes data-driven.
4. Multi-user/RBAC only if the product moves beyond its current trusted-local-operator boundary.

## Delivery Plan and Acceptance Criteria

Revised 2026-08-01: Chat quality, Task Receipts, and Connections outrank the control-plane and
operational-insight items the Gap Analysis above lists as P1-P2 - most users spend nearly all
their time in Chat, and receipts are the trust story that matters most once Connections lets data
leave the machine for the first time. The gap analysis above still holds as reference; the phase
numbers below are the actual build order.

### Phase 0 — Stabilize the baseline (**shipped**)

- Fixed the Evidence Pack's mislabeled "Reversibility" field (renamed to "Capability" - it never
  computed reversibility) and README's absolute "secrets never reach logs" claim.
- Re-recorded the 6 scenario fixtures the runtime risk-floor change invalidated.
- Wired the web chat channel to resume a CLARIFYING task on reply instead of spawning an unrelated
  new one, matching Telegram's existing behavior (shared via clarification.py).
- Acceptance: ruff, full pytest suite, tsc, and vite build all green with no known-red tests.

### Phase 1 — Complete Chat

- Sanitized Markdown rendering, a Stop button wired to the existing cancel signal, inline
  clarifications (reusing Phase 0's resume path), artifact cards, inline approvals
  (Deny / Approve once / Allow for this task, backed by a real task-scoped grant), and
  attachments + folder selection.
- Acceptance: a user can read code/tables in an answer, stop a runaway task in one click, answer a
  clarifying question without leaving the conversation, and approve or deny a pending action
  without opening a separate page.

### Phase 2 — Task Receipts

- A human-readable "Done" card in Chat and a full receipt view: result summary, changes made,
  services contacted, whether data left the machine (needs per-task egress tracking), approvals,
  evidence, duration/cost, uncertainty, export.
- Acceptance: a completed task's outcome is understandable without opening the technical trace;
  every receipt states plainly whether anything left the machine.

### Phase 3 — Connections

- A Connections page; GitHub first (OAuth app already has capability plumbing via
  `github.read`/`github.push`), then Google (Calendar, then Gmail/Drive sharing one consent flow).
  Per-connection scopes, vault-backed tokens, connection testing and error states.
- Hard external dependency: registering the OAuth App (GitHub) and the OAuth consent screen +
  client credentials (Google) requires the repository owner's own account - not something this
  agent can do unattended.
- Acceptance: a connection can be added, scoped, tested, and revoked entirely from the console; no
  token is ever displayed after entry.

### Phase 4 — Structured memory

- A real schema (provenance, confidence, category, contradiction handling), remember/edit/forget
  controls, a Memory page, and full-text/entity retrieval - replacing the current rolling summary
  + keyword search with something inspectable and correctable.
- Acceptance: a user can see why the agent believes something, correct it, and have the
  correction stick.

### Phase 5 — Skills lifecycle

- A skill manifest format, a Skills page (catalog, permission labels, install/uninstall, version
  pinning, updates, integrity verification) - `skills.use` today is list/read over a directory with
  no lifecycle concept at all.
- Acceptance: a skill can be installed, its permissions inspected before installing, and removed,
  entirely from the console.

### Phase 6 — Packaging

- Windows installer, tray app, automatic startup, versioned updates, backup, a stable release
  channel.
- Acceptance: install-to-running-tray-app in one click, no terminal required.

### Phase 7 — Public launch

- README/screenshots/demo polish, a website, public security documentation, an initial connection
  and skill catalog, first stable version tag.
- Needs the repository owner's direct decisions on public content and hosting - not something this
  agent originates unprompted.

## Explicit Non-goals

- No fake workflow builder over the current hardcoded agent pipeline.
- No generic enterprise RBAC for a local single-operator product.
- No color-only status communication; text and icons remain required.
- No UI control that implies a backend policy exists when it does not.
- No secret value display, including in errors, trace JSON, or historical records returned by the
  admin API.
