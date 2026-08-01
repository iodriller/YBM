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

### Phase 1 — Complete Chat (**shipped**)

- Sanitized Markdown rendering, a Stop button wired to the existing cancel signal, inline
  clarifications (reusing Phase 0's resume path), artifact cards, inline approvals
  (Deny / Approve once / Allow for this task, backed by a real task-scoped grant), and file
  attachments. **Folder selection was deferred, not shipped** - a browser directory picker exposes
  no usable absolute path, so it needs the server-side browser planned in Phase 14. This bullet
  previously read "attachments + folder selection", which overstated what landed.
- Acceptance: a user can read code/tables in an answer, stop a runaway task in one click, answer a
  clarifying question without leaving the conversation, and approve or deny a pending action
  without opening a separate page.

### Phase 2 — Task Receipts (**shipped**)

- A human-readable "Done" card in Chat and a full receipt view: result summary, changes made,
  services contacted, whether data left the machine (needs per-task egress tracking), approvals,
  evidence, duration/cost, uncertainty, export.
- Acceptance: a completed task's outcome is understandable without opening the technical trace;
  every receipt states plainly whether anything left the machine.

### Phase 3 — Connections (**blocked on the repository owner**)

- A Connections page; GitHub first (OAuth app already has capability plumbing via
  `github.read`/`github.push`), then Google (Calendar, then Gmail/Drive sharing one consent flow).
  Per-connection scopes, vault-backed tokens, connection testing and error states.
- Hard external dependency: registering the OAuth App (GitHub) and the OAuth consent screen +
  client credentials (Google) requires the repository owner's own account - not something this
  agent can do unattended.
- Acceptance: a connection can be added, scoped, tested, and revoked entirely from the console; no
  token is ever displayed after entry.

### Phase 4 — Structured memory (**shipped, reduced scope**)

- Shipped: a real schema (provenance via `MemorySource`, confidence, category), a `memory_facts`
  table, remember/edit/forget controls on the admin API, a Memory page, keyword search, and a
  `memory.manage` tool so the agent can save a fact mid-task (always stamped `task_derived` -
  the model cannot claim a different source).
- Not built: contradiction handling and full-text/entity retrieval. `supersedes_id` exists on the
  schema as a reserved field but nothing sets it yet - two facts that conflict just both exist;
  there is no auto-detection or merge step. Search is a plain `LIKE` query, not an index.
- Acceptance (as shipped): a user can see why the agent believes something (category + source +
  confidence), correct it, and have the correction stick. The stronger "contradiction handling"
  half of the original acceptance criterion is not met.

### Phase 5 — Skills lifecycle (**shipped, reduced scope**)

- Shipped: an extended manifest (optional `version` and `tools` frontmatter fields), a Skills page
  (catalog, install, uninstall), and an inferred "tools referenced in these instructions" tag
  (which registered tools a skill's body references, scanned against the real tool registry when
  not explicitly declared). **Corrected in Phase 8:** this was originally labeled a "permission
  label" in three places, which overstated what a literal substring scan can guarantee - it is
  informational only, and every real action still goes through YBM's normal capability gates
  regardless of what a skill's instructions say.
- Not built as originally scoped: there is no skill registry or distribution channel for this
  product, so "version pinning, updates, integrity verification" would mean inventing trust
  infrastructure with nothing real behind it. What shipped instead is honest about that: a
  `version` string an author can bump, and a content hash shown per skill so a person can notice
  "this changed since I last looked" - not a signature or a source to pin against.
- Acceptance (as shipped): a skill can be installed, the tools its instructions reference
  inspected before installing (or immediately after, live from the same install call), and
  removed, entirely from
  the console.

### Phase 6 — Packaging (**shipped, reduced scope**)

- Shipped: a tray icon (`scripts/tray_app.py`, `ybm tray`) that opens the admin console and shells
  out to `ybm.ps1` for start/stop/restart/status - no process-supervision logic of its own.
  `ybm autostart enable|disable|status` registers/removes a per-user Startup-folder shortcut, no
  admin rights needed. `ybm backup [--out <dir>]` zips the database, config.yaml, .env, and the
  secret vault. `ybm check-updates` compares the installed version against GitHub's latest release
  (read-only, no auto-apply).
- Not built: a compiled one-click Windows installer (`.msi`/`.exe`). This would need a real build
  toolchain (Inno Setup or NSIS) that isn't present in this environment, and installing one
  unprompted is a bigger, less reversible step than adding a Python dependency -
  `install.ps1`/`install.sh` remain the actual installer (`iwr ... | iex`, one command, no terminal
  *after* that one command - not literally the "no terminal required" acceptance criterion below).
  There is also no published release yet - `check-updates` currently and correctly reports that,
  since cutting and pushing the first tag/release is a public, external action for the repository
  owner to trigger, not one this agent originates unprompted (same reasoning as Phase 7).
- Acceptance (as shipped): starting from a checkout, one command (`ybm autostart enable`) gets a
  running tray icon at every future login; "install-to-running-tray-app" from *zero* still requires
  running `install.ps1`/`install.sh` first, which itself needs a terminal.

### Phase 7 — Public launch

- README/screenshots/demo polish, a website, public security documentation, an initial connection
  and skill catalog, first stable version tag.
- Needs the repository owner's direct decisions on public content and hosting - not something this
  agent originates unprompted.

## Phases 8-15 (**planned, not shipped**)

Added 2026-08-01 after an operator review of the shipped console, then extended the same day with
a second, sharper review. Two themes: the runtime records far more than the UI shows, and several
shipped surfaces claim more precision than the code actually delivers.

### Findings from the second review — all verified against the code before planning

Each was checked rather than accepted, because three of them are defects in work shipped earlier
in this same pass and the wording of the fix depends on what is actually true:

| Finding | Verified | Evidence |
|---|---|---|
| Cancelling a task leaves its pending approvals pending | **Confirmed** | `apply_task_signal`'s cancel branch only calls `update_metadata(..., CANCELLED)` — no approval rejection, no grant revocation, no invocation cleanup (`orchestration/signals.py:38`) |
| Receipts label reads *and* writes as "Changed" | **Confirmed** | `TaskReceiptCard.tsx:34,105` render "Changed" over `_extract_evidence`, which merges paths from tool **inputs and outputs** without distinguishing effect |
| Receipts only exist for `completed` tasks | **Confirmed** | `ChatPage.tsx:275` gates on `task.status === "completed"`; a task that modified files then failed produces no receipt |
| "Nothing left this computer" is overconfident | **Confirmed** | `record_egress` has exactly **one** caller, `tools/http_request.py:82`. Browser, MCP, coding agents, and Telegram send record nothing, yet `TaskReceiptCard.tsx:132` still prints the absolute claim |
| Local artifacts can't be opened or downloaded | **Confirmed** | No artifact download route exists; `FileResponse` in `admin.py` only serves the admin SPA |
| "Folder selection" is documented as shipped but was deferred | **Confirmed** | Phase 1's bullet (line 141) says "attachments + folder selection"; the implementation deferred it because browser directory pickers expose no usable absolute path |
| Every remembered fact is injected into every task, uncapped | **Confirmed** | `channels/memory.py:93` renders all facts and is *deliberately* exempt from `max_chars` |
| `user_stated` provenance is effectively unreachable | **Confirmed** | Only `task_derived` (the tool) and `operator_admin` (the admin API) are ever written; the Memory page's "You told it" label has no producer |
| `memory.manage` can forget facts with no approval | **Confirmed** | One capability covers list/remember/forget at `requires_approval: false`, `max_risk_level: low` (`config.example.yaml:137`) |
| Skill "permission labels" don't constrain anything | **Confirmed** | `detect_referenced_tools` is a literal substring scan of the body; a skill saying "use the shell" registers nothing, and the label is called a *permission* in three places |

The first item is an operational reliability bug and outranks every feature below it.

### Grounding facts from the first review

- `build_task_trace()` already returns `timeline` (audit + tool calls, merged and time-sorted) and
  `context` (inbound message, classification, classifier LLM). **The frontend reads neither.**
- `DELETE /api/tasks` (with `include_active`, audit-logged) already exists and **no UI calls it**.
- `_tool_registry_summary()` already returns every tool's name, group, capability, enabled state,
  lifecycle, operations, and schemas; `DiagnosticsCard` deliberately dropped that table.
- `build_task_trace()` already returns `timeline` (audit + tool calls, merged and time-sorted) and
  `context` (inbound message, classification, classifier LLM). **The frontend reads neither.**
- `DELETE /api/tasks` (with `include_active`, audit-logged) already exists and **no UI calls it**.
- `_tool_registry_summary()` already returns every tool's name, group, capability, enabled state,
  lifecycle, operations, and schemas; `DiagnosticsCard` deliberately dropped that table.
- `ToolCallRequest.parent_step_id` exists but is always `null` - the exact field needed to close
  the gap `TraceGraph`'s own docstring discloses (a subagent lane can't be linked to the step that
  spawned it).
- LLM prompts are **not persisted anywhere**; `render_prompt()` builds them per call. This is the
  only genuinely new backend capability in this group.

### Phase 8 — P0: correctness and honesty (blocks everything below)

Every item here is a defect, not a feature. Three are defects in work shipped earlier in this pass.

- **Cancellation cleanup.** Cancelling a task must reject its pending approvals, revoke its
  task-scoped grants, mark in-flight invocations cancelled where the adapter allows it, stop
  waiting on external sessions, and release the worker to claim the next queued task. Today a
  cancelled task can leave a stale approval that blocks the single worker. This is the highest
  priority item in the entire roadmap.
- **Receipt honesty.** Rename "Changed" to **"Touched during this task"** - `_extract_evidence`
  merges tool inputs and outputs, so the list genuinely mixes reads, searches, writes, and
  merely-requested commands. Replace the absolute "Nothing left this computer" with
  **"No external transfer was recorded"** until every network-capable adapter calls
  `record_egress` (today only `http.request` does). Both are wording fixes to stop over-claiming;
  real per-item effect classification (read/created/modified/moved/deleted/command executed/
  website visited/message sent) is real work, not a rename, and belongs with Phase 12's other
  trace-fidelity work - many tools already carry the needed signal in their `operation` name
  (`read_file` vs `write_text_file` vs `open_file`), so it's a mapping table, not a redesign.
- **Receipts for every terminal state**, not just `completed`. A task that modified files and then
  failed is exactly when a receipt matters most. Cover completed, failed, cancelled, and blocked.
- **Artifact download.** Add `GET /admin/api/artifacts/{artifact_id}/download`, serving only
  artifacts registered in the database whose resolved path stays inside approved artifact or
  workspace roots. Wire Open / Download / Copy path into the artifact card. A generated file the
  user cannot open is not delivered.
- **Skill label wording.** Rename to **"Tools referenced in these instructions"** with an
  explicit "Informational only - actual actions remain governed by YBM's normal permissions" note.
  A literal substring scan cannot see "use the shell", and calling it a *permission* implies a
  constraint the manifest cannot yet enforce. Update the three places that say "permission label".
- **Fix the Phase 1 folder-selection claim** in this document; it says shipped, the implementation
  deferred it. Real folder selection is Phase 14.
- Acceptance: no shipped label claims more than the code can support, and cancelling a task always
  frees the worker.

### Phase 9 — Console surfaces over data that already exists

- Rebuild the pending-approval window: one approval at a time with a pager, two-column layout,
  sticky action bar, risk-colored header, collapsed-by-default parameter JSON, keyboard shortcuts.
  Layout only - no change to Evidence Pack semantics or approval policy.
- Tasks list: an outcome column (result summary, duration, tool count, cost) alongside status,
  failure reason inline on failed rows, clear-history UI wired to the existing endpoint, and
  per-task delete.
- A timeline/waterfall view in the trace, rendering the already-computed `timeline`.
- Diagnostics: service cards (status, restarts, per-service log link), a database size chart, a
  doctor-check runner, and a one-click copy-diagnostics-bundle - replacing the current flat text.
- Acceptance: an operator can decide an approval without scrolling, tell success from failure in
  the task list without opening anything, and clear history from the console.

### Phase 10 — One command to run it, and a real identity

The current story is an *install* script plus a lifecycle CLI with ~20 subcommands. That is a
developer's interface. The target user should never see a terminal after the first double-click.

- **One entry point: `ybm run`** (wrapped by a double-clickable `YBM.bat` at the repo root, and a
  desktop/Start-menu shortcut). It detects what is missing, installs only that, applies pending
  database migrations, checks for an update and applies it if one exists, starts the stack, and
  opens the console. Running it when everything is already current should just open the browser.
- **Demote, don't delete, the power-user surface.** `setup`, `doctor`, `start`, `stop`, `status`,
  `logs` keep working for development and for `AGENTS.md`'s verification matrix; they stop being
  the documented front door. `install.ps1`/`install.sh` shrink to "clone, then call `run`".
- Reorganize `scripts/` so the human-facing entry points are visually obvious and the service
  runners move out of the top level.
- **A real logo.** Today the mark is a stock Lucide `Bot` glyph reused in the sidebar, the tray
  icon, and the favicon. Design one custom SVG mark plus a wordmark, and use it consistently
  across console, favicon, tray icon, installer, and README. Honest scope note: this pass can
  produce a clean, simple geometric mark - a distinctive brand identity is a designer's job, and
  the plan should not pretend otherwise.
- Acceptance: a non-developer can go from a downloaded folder to a working console by
  double-clicking one file, and can update the same way.

### Phase 11 — Console redesign: one place to configure the agent

Tools, Skills, MCP servers, Connections, and Memory are all currently separate top-level
destinations (or absent), but conceptually they are one thing: **what the agent is made of**.
Access is a different thing: **what it is allowed to do**. Chat and Tasks are a third: **what it
is doing**. The current five-to-seven-item flat nav does not express that.

- Regroup the console into three areas - *Work* (Chat, Tasks), *Agent* (Tools, Skills, MCP,
  Connections, Memory, Persona), and *Control* (Access, Settings, Diagnostics) - with the Agent
  area as a single hub page whose sections are panels, not separate routes.
- Within the Agent hub: a Tools catalog with counts, enabled state, capability, operations and
  risk (data `_tool_registry_summary` already returns); MCP server add/edit/test (a new write
  endpoint - MCP is config-file-only today); an `adapter.factory` surface to review, sandbox, and
  promote generated tools, which the engine already supports headlessly; and a bundled starter
  skill catalog shipped in-repo (`.agent_control/skills` is generated, so starters must live
  somewhere committed) with browse/install, edit-in-place, and duplicate.
- Keep deep links working - regrouping navigation must not break `/tasks/:id` or bookmarked routes.
- Acceptance: a new user can see everything the agent is made of on one screen, and extend it
  there, without reading config.

### Phase 12 — The rich, clickable trace

- Persist LLM calls (`task_id`, `step_index`, `source`, `model`, messages, raw response, tokens,
  latency), written through the existing `redact_payload` with a per-call size cap. Enabled by
  default - the product's whole claim is that it shows the receipts - with a config flag to
  disable and pruning through `ybm db clean`.
- Give each operator step a stable `step_id` and stamp it into `ToolCallRequest.parent_step_id`,
  linking operator history, tool invocations, approvals, and LLM calls into one real tree. This
  is the root-cause fix for the graph gap above, not a display workaround.
- Graph v2 rooted at the actual user query: query -> classification -> operator step (with its
  reasoning) -> tool calls / approval gates / artifacts -> final answer, typed and colored by node
  kind, with duration and token badges.
- Click any node for a side panel: the exact prompt sent, the raw model response, tool input and
  output, tokens, latency, and the audit events scoped to that step.
- Real per-item effect classification for receipts and evidence, replacing Phase 8's "Touched
  during this task" wording fix with actual read/created/modified/moved/deleted/command-executed/
  website-visited/message-sent labels. A mapping table from each tool's `operation` name to an
  effect kind (`filesystem.manage`'s `read_file` vs `write_text_file` vs `open_file` already
  distinguishes this at the source), not a redesign of evidence extraction.
- Acceptance: "why did it do that" is answerable from the console alone, for any past task, and a
  receipt's evidence list says what actually happened to each item, not just that it was touched.

### Phase 13 — Memory: real retrieval, real provenance, gated forgetting

Structured memory shipped, but retrieval did not. All three sub-items are correctness, not polish.

- **Deterministic relevance selection.** Today every fact is injected into every task and is
  explicitly exempt from `max_chars`. That is fine at five facts and actively harmful at a
  thousand. Score by exact entity match, keyword overlap with the objective, category relevance,
  recency of use, and current folder/service context; take the top 10-20; always include pinned
  global preferences. Still no vector database - the reasoning in Phase 4 holds, and a
  deterministic scorer is inspectable in a way an embedding search is not.
- **A real `user_stated` route.** The enum value exists with no producer, so the Memory page's
  "You told it" badge can never appear. Detect an explicit "remember that ..." in the user's own
  message at the runtime level, store the user's actual words, and stamp `user_stated` - with the
  provenance decided by the runtime, never selectable by the model, exactly as `task_derived`
  already works.
- **Split `memory.manage` by operation.** One capability currently covers list, remember, and
  forget at `requires_approval: false` / `low`. Deleting durable facts should not be ungated:
  keep list and remember low, and give **forget** a medium risk floor with approval required, via
  the existing `operation_risks` / `approval_required_operations` mechanism.
- Acceptance: memory stays useful at 1,000 facts, "You told it" is reachable, and the agent cannot
  silently erase something the user asked it to remember.

### Phase 14 — Server-side folder picker

- Browser directory pickers cannot yield a usable absolute path, which is why Phase 1 deferred
  this. The correct implementation is server-side: list the configured allowed roots, browse
  subdirectories through the backend, select one, and insert a server-recognized folder reference
  into the task. Path resolution is validated against the allowed roots on every request - no
  traversal outside them, and no reliance on the browser's directory-upload as a stand-in.
- Acceptance: "organize this folder" is expressible from the console without typing a path.

### Phase 15 — A second channel

- Refactor `channels/` into a channel-adapter interface (Telegram already provides the shape:
  intake -> classify -> task -> notify) so a new channel is an adapter, not a fork.
- Candidate order by reach-per-effort: WhatsApp (Baileys), Discord, Email/IMAP, Slack, Signal.
  iMessage needs a macOS host and is out of scope on this machine.
- Comparable prior art: OpenClaw (MIT, self-hosted) routes ~25 chat platforms through one local
  gateway into a single agent - the same architecture this refactor points at.

## Explicit Non-goals

- No fake workflow builder over the current hardcoded agent pipeline.
- No generic enterprise RBAC for a local single-operator product.
- No color-only status communication; text and icons remain required.
- No UI control that implies a backend policy exists when it does not.
- No secret value display, including in errors, trace JSON, or historical records returned by the
  admin API.
