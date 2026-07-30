# YBM History — from patchwork to product, then the post-migration audit

This file merges two documents written back-to-back and is read as one continuous story:
**Part 1 (P0–P6)**, written 2026-07-26, is the original architecture overhaul — from "doesn't
reliably install" to the current three-agent design. **Part 2 (N1–N6)**, written 2026-07-28
immediately after P3 landed, is the audit that found P3 had regressed observability, plus the
plan that fixed it. Both parts keep their original section labels (`P0`–`P6`, `§1.1` etc.,
`N1`–`N6`) because ~90 code comments across the codebase cite them by that label — if you're
here from a comment like `(docs/HISTORY.md P3 §2.2)`, search this file for that exact string.

The detailed evidence trail for completed items — exact test names, line-count deltas,
before/after reproductions — mostly lives in git history, not here; `git log --oneline` plus
commit bodies from 2026-07-27 onward has it. **For current architecture and known gaps, see
[ARCHITECTURE.md](ARCHITECTURE.md) — that's the accurate "how it works now" reference. This
file is "why it's built this way."**

**The goal this repo exists to serve:** *I talk to my local computer, and agents on my local
computer do whatever I ask.* Every decision below is judged against that sentence.

**Status (2026-07-29):** P0–P3 and P6 done; P4/P5 partially done. **N1–N5 done, including
N3's re-recording pass — the scenario tier is 33/33 green with zero skips, up from 2/18.**
N6 (4-page console restructure) is the only unstarted item.

A cleanup pass on 2026-07-28 removed the plan-era code §1.1 had only flagged as dead (now
physically deleted), trimmed 3 duplicate admin API routes, cut the live E2E suite from 72 to
11 cases, cut `config/config.yaml` from 317 to 144 lines (verified redundant-with-default
keys only), fixed a real bug in `ybm clean`'s flag handling, and confirmed the fallback chat
responder is live code, not dead weight — see the closing note at the end of Part 2.

A follow-up pass on 2026-07-29 recorded the remaining scenario fixtures and, in doing so,
found and fixed **five real product bugs** the deterministic tier had been too dark to catch:
the code interpreter's per-call workspace isolation (item 7), generated code hardcoding
absolute paths in a way that breaks the Docker sandbox (item 9), the MCP catalog's misleading
dotted `server.tool` format (item 8), an MCP subprocess leak on handshake failure (item 12),
and a marginal MCP timeout masquerading as a random anyio error (item 13). Items 6 and 11 in
§4 also record a **correction to an earlier claim of mine** that turned out not to be a bug.

A final pass the same day ran an AST-normalised duplicate detector over the whole repo and
collapsed **seven groups of duplicated code** into one definition each - including `_failed()`,
which had 13 byte-identical copies, one per tool adapter (item 16). It also fixed a
misleading `ybm doctor` result (item 15) and removed the last orphaned prompt files (item 14).
**Part 3 below is the prioritised way forward** from here.

---

# Part 1 — P0–P6: the architecture overhaul

## 0. Evidence (as observed 2026-07-26, before any of this plan was executed)

**Scale then:** 200 tracked files, 37,017 lines of Python, 22 scripts (2,759 lines), 401 unit
tests, 72 E2E cases at a 49% pass rate (`run_20260525_125109`, stale even then).

**Findings, now fixed (P0/P1):** undeclared dependencies resolving only via a global Anaconda
install; no virtualenv anywhere; hardcoded absolute LocalDeploy paths; `start_stack.ps1`
reporting success without checking the process was still alive, so a crashing worker
restarted silently forever; a script referencing a test file that didn't exist.

**Findings, load-bearing for later phases:**
- **Config drift.** `config/config.yaml` is gitignored and every capability starts disabled
  by default — the dominant real-world failure mode. `ybm setup` bootstraps a file now, but
  capabilities still start off by design.
- **No test tier between unit and live.** Nothing deterministic exercised the worker loop end
  to end before P2 built scenario tests.
- **Stale state silently reanimates.** No retention, no startup reconciliation existed —
  closed in P6.
- **Two admin UIs, overlapping.** `admin.py` (embedded HTML) and `admin_streamlit.py` —
  resolved in P4 by deleting the embedded-HTML one.
- **Security gaps around an otherwise-good policy engine**: no CORS/Origin/CSRF check on the
  admin API, admin token optional on loopback — addressed in P5 item 1.
- **The brain plans once; it does not loop.** `worker.py` called the planner once, got a
  static plan, replanned from scratch on failure, capped at 2. No observe→decide→act cycle.
  This was the single biggest architectural gap — closed in P3.
- **Routing was keyword patchwork.** `default_plans.py` was 1,277 lines, 68 keyword-match
  sites; even model selection used a keyword list — closed in P3.
- **Ten LLM roles, 17 tools in one 997-line registry function** — closed in P3 item 4 and
  item 6 respectively.

## 1. Diagnosis — seven root causes

| # | Root cause | Symptom it produces |
|---|---|---|
| R1 | **No environment contract.** No venv, no lockfile, undeclared deps, hardcoded paths. | "It doesn't install what's missing." Works only on this machine. |
| R2 | **Startup reports intent, not reality.** No readiness gate, no preflight, silent restart loop. | Green output, dead stack. |
| R3 | **No deterministic test tier.** Unit-with-mocks, then live-everything. | Can't tell a real regression from a flaky LLM. |
| R4 | **Config is untracked and defaults to off.** | `tool adapter not registered`; unreproducible runs. |
| R5 | **Plan-once architecture.** Static plan + replan-from-scratch instead of an agent loop. | Recovery has to be faked with keyword rules. |
| R6 | **Keyword routing substitutes for reasoning.** | `default_plans.py`; every new failure adds another `if`. |
| R7 | **No product surface.** Two half-UIs, no install path, no first-run flow. | Not shippable, even to yourself on a new laptop. |

R5 and R6 are the same wound: because the executor couldn't adapt mid-task, the only place
left to encode adaptation was a hand-written if/else chain over error strings. All seven are
now closed or substantially closed; R5/R6 were closed structurally in P3, not patched.

## 2. Target architecture (as designed; current state lives in ARCHITECTURE.md)

### 2.1 Three agents, many tools

Collapse 10 LLM roles into 3, as *prompt sections*, not modules: **Concierge** (front door —
chat vs. task, conversation memory, progress reporting), **Operator** (the agent loop — given
a goal + tool catalogue + policy, runs observe→decide→act until done/blocked/out-of-budget),
**Auditor** (did we actually achieve the goal — grounded answer + evidence). Everything else
(computer-use decisions, OCR, code-interpreter prompts) is a tool with an internal LLM call,
not a fourth agent.

### 2.2 The Operator loop replaces plan-once

```
goal + tool catalogue + policy + budget
  ↓
┌─────────────────────────────────────────┐
│ observe   → current state, last result  │
│ decide    → next tool call | done | ask │  ← ONE LLM call, structured output
│ gate      → policy engine (unchanged)   │
│ act       → tool                        │
│ record    → audit + operator_history    │
└──────────────┬──────────────────────────┘
               └── loop until done / blocked / budget
```

This deletes the need for keyword-driven recovery — "the next `decide()` call sees the error
in context," which is what an LLM is actually good at.

### 2.3 Naming: two concepts, not four

**Capability** (a permission scope, e.g. `filesystem.write`) and **tool** (a callable unit
with a JSON schema, e.g. `filesystem.manage`) stay. **Adapter** and **connector** were retired
from the vocabulary — an adapter is just a tool's implementation.

### 2.4 Framework decision: build the loop, buy the sandbox and the protocol

Do not port the orchestrator to LangGraph or AWS Strands — this repo already has durable
SQLite task state, an atomic worker claim, an audit trail, an approval gate, and a capability
policy engine; a graph framework would duplicate the task store and make it *easier* to route
around the policy gate, the one invariant that must never be bypassable. The genuinely missing
piece was the agent loop (§2.2), ~200 lines, not a dependency. Do buy: the sandbox (Docker for
generated code — see P5 item 4) and the protocol (MCP as the single tool interface — already
in place via `mcp_client`/`mcp_server`).

## 3. The plan

### P0 — Make it install and start honestly *(done, 2026-07-27)*

`uv`-managed venv + lockfile with all deps declared; `ybm doctor` preflight (Python version,
venv, imports, DB, ports, config, Telegram token); `ybm setup` bootstraps a fresh config;
`YBM_LOCALDEPLOY_ROOT` replaces hardcoded paths; supervisor crash-loop breaker (3 restarts/60s
→ `failed`, last 20 log lines surfaced); `ybm start` waits for real readiness signals instead
of trusting `Start-Process`.

### P1 — Collapse 22 scripts into one CLI *(done, 2026-07-27)*

One entry point, `ybm` (`setup | doctor | start | stop | restart | status | logs | test | e2e
| db | config | trace | scenario`), backed by `cli.py`. 15 of 22 scripts deleted or relocated;
`scripts/` now holds a handful of thin launchers plus `ybm.ps1` itself.

### P2 — Build the missing test tier *(foundation done 2026-07-27; re-recording not done)*

Scenario tests: real DB/registry/policy/worker loop/tools against a temp filesystem, with a
scripted LLM (`testing/scripted_llm.py`) replaying recorded fixtures — deterministic, no
Telegram/network/GPU, whole suite in seconds. 16 cases were ported against the *old*
plan-based path before P3 deleted that path. **Only 2 of those 18 scenario tests still have
valid fixtures** — the other 16 are `pytest.mark.skip`'d, not deleted, because their fixtures
are keyed on the old planner's exact prompt text, which no longer exists post-P3.
Re-recording them against the Operator loop's prompt needs a live LLM call per case — this is
N3, explicitly gated on a cost/scope check-in before spending on live API calls, not yet done.
The live E2E suite was trimmed 2026-07-28 from 72 to 11 representative smoke cases (one per
major capability: filesystem inspect/search/organize, document, browser, desktop observation,
code interpreter, artifact delivery, scheduling, MCP, adapter authoring) — not re-baselined
yet, since that needs a real run against the live stack.

### P3 — Rebuild the brain *(done, 2026-07-28)*

All six items landed: (1) the Operator loop is the sole execution path — flipped by explicit
user decision after being shown the gaps, with approval flow/fulfillment-gap
checking/rate-limit backoff/background-session resume/model escalation all ported across
first; (2)+(3) `default_plans.py`, `recovery_policy.py`, `failure_diagnosis.py`,
`attempt_history.py`, `llm/planner.py`, `llm/synthesizer.py`, `llm/validator.py` all deleted
outright (`worker.py`: 1,761 → 978 lines); (4) Concierge absorbs classifier +
telegram-gateway into one call, Auditor absorbs validator + synthesizer + fulfillment, wired
into the Operator loop's `done` path (conversation-memory and pre-task clarification
deliberately *not* merged — ordering and failure-isolation reasons, see git history for the
full rationale); (5) model selection is now reactive (escalate only after an observed
structured-output failure), not keyword-triggered; (6) the 997-line registry function is split
into per-tool `register()` functions in `tools/spec.py` + each adapter module.

**Known, disclosed regression:** the 16 scenario tests that exercised the deleted plan-based
path cannot run against the Operator loop without re-recording (see P2, N3). Full unit suite
was green throughout, verified at every step, not just at the end. `ybm doctor` clean.

### P4 — One real admin UI *(pick-one-UI done 2026-07-27; console restructure not started)*

**Done:** deleted the 1,288-line embedded-HTML admin (`admin.py`'s `_ADMIN_HTML`), kept only
the JSON API it exposed; `GET /admin` now returns a small pointer page to Streamlit instead of
a second competing SPA. Verified against a real running server, not just `TestClient`.
Investigated and found **not reproduced** in current Streamlit source: KPI truncation,
duplicate health rendering, mojibake encoding — likely artifacts of the deleted embedded-HTML
page, not Streamlit; not visually re-confirmed either way (no browser tool in this
environment).

**Not started:** four-page console restructure (*Now* / *Tasks* / *Access* / *Settings*) —
see N6.

### P5 — Close the security gaps *(items 1, 2, 5 done 2026-07-27; 3 partial; 4 addressed differently)*

1. **Done.** Admin API had zero middleware — any local page's JS could POST a capability
   change to loopback without needing to read the response. Fixed with an Origin-vs-Host
   same-origin check in `require_admin()` (OWASP's "Verifying Origin With Standard Headers"),
   not a separate CSRF token — sufficient for a single-user local API.
2. **Already true**, not this session's work — `ybm setup` already generates the admin token
   unconditionally and binds to `127.0.0.1` by default.
3. **Partially done.** `allowed_imports` enforcement itself was never broken. What was broken:
   `importlib.import_module("os")` bypassed the `blocked_imports` denylist entirely (a
   function call, not an `Import` AST node) — fixed by adding `importlib`, `multiprocessing`,
   `winreg` to the default denylist. Forcing a non-empty default *allowlist* (safer than a
   denylist) is still not done — needs careful curation to avoid breaking routine generated
   scripts, deliberately not rushed.
4. **Addressed via a sharper, less disruptive fix than "default to Docker."** Found that
   generated code was exempted from approval *unconditionally*, and silently fell back to
   unsandboxed `local_subprocess` whenever Docker wasn't running (true on this machine, every
   session). Fixed by requiring approval specifically when that silent fallback occurs, not
   when Docker succeeds or when a user has explicitly chosen local execution via config.
5. **Done, including the follow-up N5 found.** Found the `AWAITING_APPROVAL` Telegram message
   pointed users at an admin-UI approve action that didn't exist and never mentioned the one
   thing that worked (replying `approve` in chat) — fixed the message and added a
   human-readable preview (title/risk/tool/input) of what's pending. The Telegram
   inline-keyboard callback parser (`channels/telegram.py`) had zero code sending the button —
   half-built and dead. **N5 wired it up** (both approve and reject) rather than leaving it
   dead or deleting it.

### P6 — Operational hygiene *(done, 2026-07-27)*

Startup reconciliation (`reconcile_orphaned_tasks()` explicitly fails any task left
`RUNNING`/`INTERPRETING` by a dead worker instead of silently resuming or re-running side
effects); retention already existed pre-P6 (`ybm db clean`, 30-day default; N5 extended it to
orphaned audit events); schedule hygiene (`max_consecutive_failures`, default 5, auto-pauses a
schedule and notifies instead of respawning a permanently-broken task forever — the exact
pattern that produced 7 dead May schedules in the original evidence); `agent_control.db`
moved under `.agent_control/` with a tested, non-destructive one-time migration for anyone
with the old path.

## 4. Reaching the actual goal

Three things beyond this plan, cheap once the Operator loop exists, expensive before it:

- **Claude Code / Codex / Copilot as first-class backends.** `coding_agent.py` already shells
  out to all three CLIs. Adding the Claude Agent SDK as an in-process backend (streaming
  progress, structured tool events instead of scraped stdout) is what makes "show me what
  it's doing" possible.
- **Tool authoring that closes the loop.** `adapter.factory` writes a proposal and stops. Wire
  it end to end: write → sandbox-test → register over MCP → use it in the same task.
- **Computer use as a real capability**, not three top-level prompts capped by `max_steps` —
  one tool with an internal loop, gated on the Access page like everything else.

## 5. Delete list

| Path | Why | Status |
|---|---|---|
| `PHASED_APPROACH.md`, `STEP_BY_STEP_IMPLEMENTATION.md`, `docs/FIX_PLAN.md`, `docs/PROJECT_GAPS.md`, `docs/EXTENSION_IMPLEMENTATION_PLAN.md`, `docs/PROMPT_GAP_ANALYSIS.md` | Superseded planning docs, one historical finding carried into P3. | **Deleted** |
| `docs/FLOW.md`, `docs/FLOW_DIAGRAMS.md`, `docs/TASK_FLOW.md` | Three overlapping flow docs. | **Deleted**, merged into `docs/ARCHITECTURE.md` |
| `docs/GETTING_STARTED_ADMIN_TELEGRAM_LLM.md` | Duplicated `LOCAL_SETUP.md` and `ARCHITECTURE.md`. | **Deleted**, unique content merged |
| `scripts/*` (15 of 22) | Listed in P1. | **Deleted/relocated** |
| `backend/src/agent_control/admin.py` HTML (2,210 lines) | Second UI. | **Deleted** (P4) |
| `default_plans.py`, `recovery_policy.py`, `failure_diagnosis.py`, `attempt_history.py`, `llm/planner.py`, `llm/synthesizer.py`, `llm/validator.py` | Plan-based path, replaced by the Operator loop. | **Deleted** (P3) |
| `scripts/benchmark_models.py`, `scripts/benchmark_progress.py` | One-off scripts, superseded by `ybm test`/scenario tests. | **Deleted** (N4) |
| 13 of 14 stale scenario fixture JSONs | Keyed to the deleted planner's prompt text, permanently unmatchable. | **Deleted** (N4) — kept `operator_loop_filesystem_search.json` |
| `PlanModel`, `PlanStep`, `ApprovalGate`, `SubtaskRecord`, `ToolObservation`, `ApprovalDecision`, `PlanRepository`, `ToolRegistry.validate_plan()`, `plans`/`subtasks` tables, `tasks.plan_id`/`current_step_id` columns | §1.1 found these unreachable; physically deleted in a later 2026-07-28 pass (see Part 2's closing note). | **Deleted** |
| `docs/ROADMAP.md`, `docs/AUDIT.md` | Merged into this file. | **Deleted, merged here** |

Docs now: `README.md`, `CLAUDE.md`, `docs/HISTORY.md`, `docs/ARCHITECTURE.md`,
`docs/LOCAL_SETUP.md`, `docs/MINIMAL_END_TO_END_TEST.md`, `docs/DATABASE_INSPECTION.md`,
`e2e/README.md`.

## 6. Honest unknowns

- The 49% E2E figure is from May and was **not re-measured**. Re-baseline after config-drift
  and N3's re-recording pass; the true number is probably higher now.
- `browser.py` (963 lines) and `computer_use.py` (513 lines) were not audited for correctness,
  only for how they're routed to.
- Effort estimates assume working with an agent, not by hand — relative weights, not
  commitments.
- Whether Streamlit stays sufficient at P4's ambition is the one call worth revisiting after
  building the *Now* page.

---

# Part 2 — N1–N6: the post-migration audit

Written 2026-07-28, immediately after P3 landed. Every claim below was **observed on this
machine** — commands and reproductions are cited inline.

## 0. Headline

The P3 migration succeeded structurally and **regressed observability badly**. The system now
executes through one clean agent loop, but at the time this was written: you could not see
what the agent did (the admin UI rendered the deleted plan concept, showing "Plan Steps: 0"
for every task, while the real execution record `operator_history` was written and read by
nothing); there was no logging configuration anywhere in the codebase; three confirmed
behavioral bugs had been introduced by the migration. All of this is now fixed — see N1/N2.

## 1. Redundancy found (since deleted — see the closing note at the end of this part)

### 1.1 Dead code left by the P3 migration (confirmed unreachable, since physically deleted)

The plan-once path was deleted, but its data model and consumers were not, at first. Nothing
called `repositories.plans.create()` anywhere in `backend/src` — plans were never created, so
everything downstream of them was dead: `PlanStep`, `RecoveryAction`,
`_postconditions_from_plan()` (~106 lines), `expected_fulfillment()`, `fulfillment_gap()`,
`ToolRegistry.validate_plan()`, `PlanRepository`, the `plans` DB table, `attempt_history`
metadata, `current_step_id`. All of it, plus `PlanModel`, `ApprovalGate`, `SubtaskRecord`,
`ToolObservation`, and `ApprovalDecision` (found in the same pass but not yet listed here),
was deleted in a follow-up cleanup pass the same day — see this part's closing note.

### 1.2 Stale documentation (fixed)

`docs/ARCHITECTURE.md` described 8 already-deleted files as current — rewritten. `README.md`
claimed "structured planner with plan persistence" — fixed. `docs/ROADMAP.md` was 1,074 lines
of mostly-changelog — trimmed, then merged into this file.

### 1.3 Scripts

`scripts/benchmark_models.py` (640 lines, stale since May) and `scripts/benchmark_progress.py`
(120 lines) deleted; `ybm benchmark`/`ybm benchmark-status` removed with them.
`scripts/test_e2e.py` was nearly deleted as a mistaken duplicate but is actually the
implementation behind `ybm send` — kept.

### 1.4 Repo clutter (cleaned)

`.agent_control/live_e2e_runs` (82 MB), `.agent_control/e2e_results` (15 MB, since re-wiped
after the E2E trim), `.agent_control/benchmark_*`, and an untracked `sales_data.xlsx` were
removed. `ybm clean` extended to cover `e2e_fixtures`/`e2e_runs`/`live_e2e_runs` so they don't
reaccumulate silently.

### 1.5 The 16 skipped scenario fixtures

Deleted (the test files were kept as a re-recording checklist) — each embedded the entire
deleted `planner_system.md` text as its fixture key, permanently unmatchable.

## 2. Development speed — the real bottlenecks (all fixed — see N1)

### 2.1 There was no logging at all

The stdlib root logger defaulted to WARNING with a last-resort stderr handler — every
`logger.debug()` call (17 of them) was discarded, `structlog` was a declared dependency
imported by nothing, and service logs were unstructured stdout capture. **Fixed:**
`logging_setup.py` (structlog JSON + console per service, called from every entry point),
`task_id` bound as a contextvar for the duration of each `process_task()` call so every line
for one task is greppable by one id, debug-only excepts upgraded to warnings where the
failure was a real degradation rather than a routine expected-to-fail probe.

### 2.2 You could not see what the agent did

`operator_history` — the real step-by-step execution record — was written and read by
nothing; the admin UI still rendered the deleted plan concept ("Plan Steps: 0" for every
task). **Fixed:** rendered in both the trace endpoint and Streamlit's task view; dead
plan-rendering functions deleted.

### 2.3 No fast feedback loop between "unit test" and "hours-long live E2E"

Scenario tests (deterministic, real stack + recorded LLM, seconds to run) existed but 16 of
18 had gone dark after P3 changed the prompt they were keyed on. **Fixed (tooling; execution
still pending):** `ybm scenario record <name>` (N3) makes re-recording a one-liner; the live
E2E suite was separately cut from 72 to 11 cases the same day.

### 2.4 No one-command task post-mortem

Debugging a failed task needed the stack running, then the admin UI, then a click into a
trace — no CLI surface existed for the `/admin/api/tasks/{id}/trace` endpoint's data.
**Fixed:** `ybm trace <task_id>` reads the DB directly, no running backend required.

### 2.5 Test boilerplate

9 test files each defined an identical `_repos(tmp_path)` fixture; minor friction, not
addressed in this pass.

## 3. Bugs — all three reproduced, not inferred (all fixed — see N2)

### 3.1 Gap checks consumed the tool-call step budget

`_fulfillment_check`/`_audit_check` pseudo-entries counted against `operator_max_steps`
alongside real tool calls — with defaults (`max_steps=8`), 2 fulfillment + 2 audit gaps could
burn half the budget on bookkeeping, worst case exhausting it with zero real tool calls.
**Fixed:** `_tool_call_count()` excludes check entries from the budget count.

### 3.2 The Auditor judged a 2,000-character truncation

The Auditor's sufficiency check ("are all 5 episodes present?") was given
`output_summary`, truncated to 2,000 characters, while the full text (up to 20,000) sat
right there in `metadata["last_tool_output_text"]` — producing false INSUFFICIENT verdicts
on exactly the long-content objectives it existed to check. **Fixed:** Auditor now reads the
full stored text.

### 3.3 Progress notifications were dead

The RUNNING/RETRYING dedupe key was built from `attempt_history`/`current_step_id`, both
zero-writer plan-era fields, so it collapsed to the constant `"running"` and never changed —
a 30-step task sent one "working on it" and then silence for minutes. The same root cause
dropped the "Latest attempt: X ended with Y" explanation from every failure message.
**Fixed:** key on `len(operator_history)`; `_latest_attempt_summary()` rewritten against
`operator_history`.

### 3.4 `operator.max_steps` was undocumented

Present in neither `config/config.example.yaml` nor `config/config.yaml`, despite being the
sole backstop against a runaway loop — the config-drift root cause (R4) reappearing.
**Fixed:** reconciled, and 25 further config keys present live-but-not-in-example (and 15
the other way — notably the whole `code_interpreter.docker` sandbox block) were found in the
same pass. A real, live security gap surfaced alongside it: this machine's `blocked_imports`
list was missing 3 entries a prior session had added to the code default, unprotected because
pydantic-settings replaces rather than merges explicit YAML lists.

## 4. Feature-level gaps found — status after N5

1. ~~No approve/reject UI; inline keyboard dead; no reject at all.~~ **Fixed by N5** — real
   approve/reject in Streamlit, inline keyboard wired up on both decisions.
2. ~~No evidence view.~~ **Fixed by N5** — key-based file/URL/command extraction from
   `tool_invocations`, rendered in both Streamlit and `ybm trace`.
3. ~~No kill switch.~~ **Fixed by N5** — one confirm-then-disable-everything control reusing
   the existing access-modes endpoint.
4. **Secret vault has no UI.** `storage/secrets.py` (Fernet) exists; nothing exposes it.
   `AGENT_SECRET_VAULT_KEY` is unset (doctor warns). **Still open.**
5. ~~No retention for audit_events/tool_invocations.~~ **Fixed by N5** — orphaned
   (`task_id IS NULL`) audit events now covered by `db_clean`.
6. ~~Auditor never runs on the delivery path.~~ **Not a real gap - earlier claim was wrong.**
   `artifact.deliver` is indeed absent from `CONTENT_TOOLS`, but
   `_last_content_tool_history_entry()` scans history *backwards past* non-content tools, so
   a search-then-deliver task audits the search results, which is the correct thing to ground
   an answer in. Adding `artifact.deliver` would make the Auditor try to extract an answer
   from a delivery receipt (`{delivered: true, path: ...}`) - meaningless at best. The real
   (narrower, harder) gap is that nothing verifies the *delivered file's content* against the
   objective; see item 11.
7. ~~`code.interpreter` can't chain multi-step file work within one task.~~ **Fixed
   2026-07-29.** `_workspace()` appended `uuid4().hex[:8]`, giving every call its own fresh
   directory. Task ids are already unique, so that suffix only ever separated calls *within*
   one task - exactly what must be shared. Step 2 landed in an empty directory and could never
   see step 1's file; `inspect_state` always reported zero files. Reproduced on three
   independent live recording attempts. Fixed by making the workspace stable per task
   (`root / f"task_{task_id}"`), with three regression tests in `test_code_interpreter.py`
   (verified failing against the old code, passing against the new) and the
   `code_interpreter_csv_summary` scenario now green end to end - two real calls, one shared
   workspace, second call reading back the first's file.
8. ~~`mcp.client`'s tool catalog format misleads the model into malformed `call_tool` input.~~
   **Fixed 2026-07-29.** `mcp_catalog_summary()` printed each tool as a single dotted string
   (`"- fake.echo: Echo text; ..."`) while `MCPClientInput` requires `server` and `tool` as
   separate fields; the model copied the dotted string wholesale into one field, reproducibly,
   across two independent recording attempts with zero self-correction (`server="fake.echo"`
   one run, `tool="fake.echo"` with `server` missing the next). Fixed by labelling the fields
   the way the schema names them (`- server="fake" tool="echo" - Echo text; ...`) plus a
   header line and a realistic worked example. Re-recording immediately produced the correct
   split input, first try; `mcp_call_fake_echo` now asserts the split explicitly as a
   regression guard.
9. **Generated code must never hardcode the workspace's absolute path.** Found 2026-07-29
   after fixing item 7, when a recorded fixture still failed on replay: the generated script
   had baked in the *recording run's* absolute workspace path, which no longer exists on a
   later run. The more serious consequence is not the fixture - it is that the Docker backend
   bind-mounts the workspace at a **different path inside the container**
   (`workspace_mount_target`, default `/workspace`), so any script carrying an absolute host
   path fails outright under the sandbox backend, the one that is supposed to be the secure
   default. Both backends already run the script with the workspace as cwd, so relative paths
   are always correct and portable. **Fixed** by rewriting
   `prompts/base/code_interpreter_system.md` and `prompts/tasks/code_interpreter_user.md` to
   demand relative paths and label the shown path reference-only, with a regression test
   asserting the instruction survives in the prompt actually sent.
10. **Three dead metadata reads in failure messaging.** `intervention_summary`,
    `planning_error`, and `last_replan_reason` were read in `telegram_notifications.py` with
    zero writers anywhere - plan-era keys that could only ever contribute `None` to an `or`
    chain. **Fixed** (removed), along with `_last_command_id()`/`_last_usage()`, two helpers
    orphaned when the dead message formatters were deleted. Found by a systematic
    read-vs-write sweep across all metadata keys, which also confirmed `computer_use_actions`
    (written via `worker.py`'s dynamic copy map) and `task_budget_seconds` (a documented
    per-task override) are **not** dead despite looking that way to a naive grep.
11. **Content correctness of generated files is still unverified.** Nothing checks that a
    script the code interpreter wrote actually did what the objective asked - only that it
    ran and produced files. `generate_and_run` regenerates on `SyntaxError`/`ValueError`, not
    on "valid Python that computes the wrong thing", and a delivery-ending task has no
    Auditor pass over the delivered file's contents. **Still open**; this is the honest
    residue of what item 6 was gesturing at.
12. **A failed MCP handshake stranded the server subprocess.** Found 2026-07-29 while
    diagnosing item 13. `_session.__aenter__` entered `stdio_client` (spawning the
    subprocess), then called `initialize()`; when initialize raised, the exception propagated
    out of `__aenter__`, so the caller's `async with` never ran `__aexit__` and the
    already-entered stdio context was never closed. The orphaned async generator was
    eventually finalized by the event loop at shutdown - in a different task than had entered
    it - surfacing as a confusing `RuntimeError: Attempted to exit cancel scope in a different
    task`. **Fixed** by rebuilding `_session` on `AsyncExitStack`, which unwinds whatever was
    entered, in reverse order, in the same task, on both the failure and normal paths.
    Regression test asserts both layers close when `initialize()` raises.
13. **The MCP scenario tests' 10s handshake timeout was marginal, not generous.** Timed
    directly: the Python-subprocess spawn plus MCP `initialize()` round trip takes **11.5s**
    on this machine with LocalDeploy/Ollama also running (10s -> fails at 10.3s; 30s ->
    succeeds at 11.5s). It presented as an opaque `anyio.WouldBlock`/`CancelledError` rather
    than a clear timeout, and had been intermittently passing purely on machine load.
    **Fixed** by a single shared `MCP_HANDSHAKE_TIMEOUT_SECONDS = 30` in the scenario harness,
    with the measurement recorded next to it so the number isn't re-guessed later.
14. **Four orphaned prompt files.** `tools/copilot_development.md`, `tools/copilot_web_app.md`,
    `tools/adapter_factory_copilot.md`, `tasks/computer_use_validation.md` (47 lines) were
    referenced by nothing - their only consumer was `default_plans.py`, deleted in P3.
    `ARCHITECTURE.md` still listed two of them as live tool prompts. **Fixed** (deleted, doc
    corrected). Found by diffing prompt files on disk against prompt paths referenced in
    source.
15. **`ybm doctor` reported LocalDeploy unreachable while it was running.** `_http_ok()`'s
    liveness probe used a 2.0s timeout, but LocalDeploy's `/health` enumerates Ollama's
    installed models before replying and measured ~2.06s here. Doctor printed
    "LocalDeploy not reachable ... fallback profile 'openai_saved' will be used" in the same
    run that printed "Port 8000 (LocalDeploy) listening" - actively misleading, since it tells
    the user their free local model is down and a **paid** API is about to be billed when
    neither is true. **Fixed** (6.0s, with the measurement recorded at the call site); doctor
    now reports 23 ok / 4 warnings instead of 22 / 5.
16. **Seven groups of duplicated code, found by AST-normalised structural comparison** (names
    and literals stripped, so renamed copies still collide) rather than by reading. All
    **fixed 2026-07-29** by extracting one definition each; the win is single-source-of-truth,
    not line count - a change to any of these previously meant editing N files or, far more
    likely, editing one and silently diverging the rest:
    - `_failed()` - **13 byte-identical copies**, one in every tool adapter. Now
      `tools/spec.py:failed_result()`, which every adapter already imports. This is the
      adapter failure *contract*; having 13 copies meant a new field or a different
      `ErrorClass` could be applied inconsistently across tools without any test noticing.
    - `_repos(tmp_path)` - 8 identical copies across test files. Now
      `tests/helpers.py:make_repos()`. This was flagged in the original audit (§2.5) and had
      been open since.
    - `_settings(...)` - 4 identical copies in scenario tests → `harness.filesystem_settings()`.
    - `_mcp_settings(...)` - 2 copies → `harness.mcp_settings()`.
    - `_task_chat_id()` - 2 copies in **src** (`channels/` and `tools/`, with no import
      direction between them that would let one reuse the other) → `schemas.task_chat_id()`,
      which is where it belongs since it is pure logic over `TaskRecord`'s own fields.
    - `_backend_base_url()` - 2 copies in **src** → `config.backend_base_url()`.
    - Markdown code-fence stripping - 2 copies in **src** (`llm/providers.py`,
      `tools/code_interpreter.py`) → `providers.strip_code_fences()`.

    A re-run of the same detector afterwards reports **1 remaining group**, and that one is a
    false positive: two deliberately-different fake LLM providers in `test_code_interpreter.py`
    that return different canned scripts but have the same shape.
17. **The MCP stdio tests were genuinely flaky under CPU contention, not just marginally
    timed.** Found 2026-07-29 running the full suite while LocalDeploy/Ollama were still up
    from the W1 fixture re-recording pass: `test_mcp_client.py` failed 2 of 4 consecutive
    full-suite runs at the 30s handshake timeout (item 13's fix), passed every time in
    isolation or after LocalDeploy was stopped. Not a code bug - closed by stopping the
    now-unneeded LocalDeploy process, confirmed with 3 consecutive clean full-suite runs
    afterward. Worth remembering operationally: **don't leave LocalDeploy running during a
    full test-suite pass** once a live-recording batch is done; nothing in the suite needs it
    once fixtures are committed.

## 5. The plan — N1 through N6

### N1 — Make it observable *(done, 2026-07-28)*

`logging_setup.py` (structlog JSON + console, called from every `cli.py` entry point,
`serve_backend.py`, and `admin_streamlit.py`); `task_id` contextvar bound in `process_task()`;
~10 silent-debug exception handlers upgraded to warnings; `print()` → logger at real service
call sites; `operator_history` rendered in the trace endpoint and Streamlit (dead
plan-rendering functions deleted); `ybm trace <task_id> [--json]`.

### N2 — Fix the three confirmed bugs *(done, 2026-07-28)*

See Part 2 §3. Each got a regression test.

### N3 — Restore the fast test loop *(done, 2026-07-29 — all 16 fixtures recorded, 33/33 green)*

`ybm scenario record <name> [--profile <name>]` resolves a fixture name to its test file(s),
flips their skip marker, and runs them through pytest with a live provider built from this
machine's real config. Built and verified mechanically first (name resolution, error paths,
profile lookup) without spending on a live call, per the standing decision not to incur real
API cost without a user check-in — that check-in happened 2026-07-29 and the re-recording
pass ran against `localdeploy_qwen3vl_8b` (free, local — LocalDeploy was started for this
specifically to avoid the paid `openai_saved` fallback).

**All 15 fixtures needing re-recording now succeed** (the 16th,
`operator_loop_filesystem_search`, already had a valid one). The scenario tier went from
**2 of 18 green to 33 of 33 green, with zero skips** - the first time every scenario case
has passed since the P3 migration.

The last two (`code_interpreter_csv_summary`, `mcp_call_fake_echo`) were initially left
skipped because each hit a real, reproducible product bug rather than a recording problem.
Both bugs were then fixed (§4 items 7, 8, 9, 12, 13) and both fixtures re-recorded green.
Fixing them invalidated other fixtures too - the workspace-path change and the
relative-path prompt rewrite both alter prompt text, and fixtures are keyed on exact prompt
text - so all five `code_interpreter_*` fixtures were re-recorded a final time afterwards.
That cascade is the system working as intended: a prompt or tool-schema change *should*
fail its fixtures loudly rather than replay stale data.

**Real findings surfaced along the way, not papered over:**
- `RecordingLLMProvider` had no `.calls` tracking (unlike `ScriptedLLMProvider`), breaking any
  test asserting call count while recording. Fixed - both providers now share the interface.
- `status_request`'s "zero LLM calls" premise predated the plan-based path's deletion - the
  Operator loop has no equivalent deterministic shortcut, so it now costs 2 real `decide()`
  calls. Test updated to assert the real (correct, just not free) behavior; the missing
  fast-path is a disclosed, not-yet-built optimization.
- `test_code_interpreter_generate_file.py` had a real pre-existing bug: its own docstring said
  "no-delivery" but its assertions checked for delivery anyway (copy-paste from the delivery
  sibling test, never removed). Fixed - the Operator's actual (correct) behavior was right,
  the assertion was wrong.
- `test_mcp_discover_tools.py`'s original fulfillment-gap bug (self-declared `adapter_proposal`
  postcondition a `mcp.client` call could never satisfy) is now structurally gone - the
  Operator loop has no self-declared `plan.postconditions` left to reach for the wrong type.
  Test updated from "documents the bug" to "confirms the fix." Its capability-disabled sibling
  test also had a too-strict assertion (checking the call was never *attempted* instead of
  correctly *denied*) - fixed to match the pattern used elsewhere for gated tools.
- Two objectives were genuinely ambiguous English and were rewritten rather than worked
  around: "echo hello from E2E" (echo `"hello"`, attributed to E2E? or echo the string
  `"hello from E2E"`?) and the CSV two-step, which under-specified the intermediate file so
  the model reasonably computed the total inline in one call. In both cases the model's
  reading was defensible and the instructions were at fault.
- `test_code_interpreter_csv_summary.py` also carried the same copy-pasted delivery
  assertion as its sibling, asserting a Telegram send the objective never requested. Removed;
  `artifact.deliver` is already covered end to end by two other scenario tests.

### N4 — Delete the dead weight *(done, 2026-07-28)*

See Part 2 §1.

### N5 — Close the safety gaps *(done, 2026-07-28)*

Approve/reject control (admin API + Streamlit + Telegram inline keyboard), kill switch,
evidence view, orphaned-audit retention — see Part 2 §4. New tests for every item; full suite
green throughout.

### N6 — Then, and only then, P4's UI restructure *(not started)*

The 4-page Streamlit split is worth doing **after** N1/N2 (now done), because before that it
would have been restructuring a UI that displayed the wrong data.

---

# Part 3 — The way forward

Written 2026-07-29, after N1–N5 closed and the scenario tier reached 33/33. Ordered by value
per hour, with the reasoning for the order rather than just the list.

**The single most important change in posture:** the deterministic scenario tier is now
trustworthy. Five real product bugs (§4 items 7, 8, 9, 12, 13) were found *by recording
fixtures*, not by reading code — three of them invisible to a 464-test unit suite. Anything
below that would benefit from scenario coverage should get it, because that is now where bugs
actually surface.

### W1 — Verify generated-file content, not just that files appeared *(done, 2026-07-29)*

§4 item 11. A code-interpreter task used to pass if the script ran and produced files;
nothing checked the file said what the objective asked for — `_terminal_output()` listed
file NAMES and stdout only. A script that computed the wrong number, or wrote an empty file,
sailed through, because nothing downstream ever looked inside the file.

**Fixed** by adding a bounded content preview (`code_interpreter.py`'s
`_file_content_previews()`: 3 files max, 600 chars each, text-decodable only, `script.py`
excluded as an implementation detail) to every `run_python`/`generate_and_run` result. It
flows through the existing pipe with no new mechanism: `_terminal_output()` renders it as
`Content of <file>:` lines → `worker.py`'s `_tool_output_text()` picks it up from
`terminal_output` → `last_tool_output_text` → the Auditor's `raw_output` argument. The
Auditor can now actually check a created file's content against the objective, not just its
existence.

Verified three ways: unit tests proving the preview is text-only (binaries silently skipped),
capped, and excludes `script.py`; one unit test that builds a `ToolCallResult` shaped exactly
like a real code-interpreter output and confirms the content reaches `_tool_output_text()` -
i.e. reaches the Auditor - with no LLM call needed; and full scenario-tier re-verification
after the terminal_output text changed shape (see the fixture note below).

**Deliberately not attempted:** the Auditor still doesn't verify *correctness* (that 16 is
the right sum of 3+5+8, not just that a `total` key is present) - its prompt is a
presence/count/topic checker, not a calculator. Doing that would mean either teaching the
Auditor to reason about arithmetic (prompt-risk, hard to keep general) or giving it code
execution of its own (a materially bigger change). Flagged, not built - the structural
blindness (content invisible at all) was the dominant risk and is now closed; semantic
correctness-checking is a separate, larger project.

**Fixture fallout, expected and handled the same way as before:** changing what
`_terminal_output()` renders changes the Operator's next-step prompt for any task with a
second step after `code.interpreter`, so 4 fixtures (`code_interpreter_csv_summary`,
`code_interpreter_generate_file`, `code_interpreter_json_transform`,
`implicit_code_interpreter_numbers_report`) failed on replay until re-recorded against
`localdeploy_qwen3vl_8b` - all 4 re-recorded clean, scenario tier back to 33/33.

### W2 — Give the Access page and the secret vault a UI *(done, 2026-07-29)*

§4 item 4. `storage/secrets.py` (Fernet) existed with zero way to populate it short of
writing Python - no admin route, no UI, `set_secret`'s only caller was a test.

**Fixed**: `SecretVault` gained `list_secrets()` (service → key names, **never values** - the
one invariant the whole feature depends on) and `delete_secret()`. Three admin routes
(`GET`/`POST`/`DELETE /admin/api/secrets`) all fail with a clear "run `ybm setup`" message
when `AGENT_SECRET_VAULT_KEY` is unset rather than a raw vault exception. A "Secret Vault"
section under Streamlit's Configuration panel lists `service.key` pairs with delete buttons
and a form to add/replace one (password-masked input); `set`/`delete` are audited by
service+key, deliberately never by value.

Verified: 6 `SecretVault` unit tests (round-trip, empty-vault, delete-of-missing,
value-never-in-listing), 4 admin-route tests including one that inspects the actual audit
event payload to confirm the value never lands there, and 2 Streamlit `AppTest` smoke tests
(renders the list, shows the setup hint when the key is unset, and asserts no `sk-`-shaped
string appears anywhere in the rendered page).

**Bug found and fixed along the way, unrelated to secrets:** `_render_kill_switch()`'s
`already_off = access_modes and all(...)` returned the empty dict itself (not `False`) when
`access_modes == {}`, and `st.checkbox(disabled=...)` raises `TypeError` on a non-bool -
crashing the entire admin page for a genuinely reachable state (zero capabilities
configured). Found because the new Streamlit smoke test used a minimal fixture with an empty
`access_modes`, unlike every pre-existing fixture in that test file. Fixed with `bool(...)`.

### W3 — Re-baseline the live E2E suite *(not attempted — needs a live sit-down)*

11 cases, untouched since the 72→11 trim; the last real pass rate (49%) predates P3 entirely,
so it measures a system that no longer exists. Needs a running stack and real Telegram/E2E
credentials this session doesn't have queued up - a deliberate sit-down with the user, not
something to do silently in the background. Until then the scenario tier (33/33, seconds to
run, zero live cost) is the honest signal and is described that way in ARCHITECTURE.md.

### W4 — P4's 4-page console restructure (N6) *(not attempted — large, scoped separately)*

Genuinely unblocked now (the UI shows correct data, W2 added the last missing Configuration
piece), but it's a multi-page UI redesign, not a bounded bug fix - the kind of larger,
multi-session effort CLAUDE.md says to scope and propose separately rather than fold into a
gap-closing pass. Ready to start whenever it's prioritized on its own.

### W5 — A fast path for status requests *(considered, deliberately not built)*

The deleted plan path had an LLM-free shortcut for status-shaped objectives, reached via a
hardcoded keyword list (`"current status"`, `"what's happening"`, ...) checked *before* the
LLM ever saw the objective. Rebuilding it would mean reintroducing exactly the brittle,
keyword-matching, case-by-case routing that this codebase's own redesign deliberately moved
away from in favor of the Operator loop always deciding via LLM judgment - and unlike the old
plan-based version (which still went through plan validation), a bare pre-check would
fully bypass that judgment. A message that merely mentions "status" for something unrelated
("check on the status of my package delivery") would misroute silently, with nothing to catch
it. The saved cost is one extra `decide()` call on a rare, cheap request type; the risk is a
new, permanent, un-reviewed special case in the routing logic. Not a good trade - correctly
declined, not silently skipped. If this is revisited, it should be an Operator-loop-level
optimization (e.g. cheaper/faster model tier for single-tool objectives in general), not a
keyword list for one request type.

### Deliberately NOT on this list

- **More scenario cases for their own sake.** 33 green cases cover every tool category. Add
  one when it pins down a specific behaviour (W1 needs one); don't pad the count.
- **Chasing the remaining "duplicate" the detector reports.** It is two intentionally
  different fake providers (§4 item 16).
- **Trimming docs further.** README/ARCHITECTURE/HISTORY now have distinct jobs — how to run
  it, how it works, why it is this way. HISTORY is long because it is a ledger; that is its
  purpose, and it is not loaded by anything at runtime.

## 6. What was not verified

- **Prompt quality.** No live LLM call was made in this audit or the P3 work. Whether the
  merged Concierge/Auditor prompts perform as well as the originals is unmeasured — the N3
  re-recording pass is what would measure it.
- **The live E2E pass rate.** Last broad run was May (49%), on the old 72-case suite. Not
  re-measured against the new 11-case suite either.
- **Anything visual.** No browser/screenshot tooling is available in this environment, so
  every UI claim is from source inspection, not from looking at the rendered page.

## Closing note: the further cleanup pass (2026-07-28, same day)

After N1–N5 landed, a full redundancy sweep (systematic zero-reference symbol search, not
spot-checking) found and fixed:

- **§1.1's dead code was flagged but not yet deleted** — this pass deleted it for real:
  `PlanModel`, `PlanStep`, `PlanPostcondition`'s sibling `ApprovalGate`, `SubtaskRecord`,
  `ToolObservation`, `ApprovalDecision`, `PlanRepository`, `ToolRegistry.validate_plan()`, the
  capability-alias-mapping cluster only those classes used, the `plans`/`subtasks` tables, and
  `tasks.plan_id`/`current_step_id` (both the Pydantic fields and the DB columns for fresh
  installs — existing databases keep the harmless unused columns/tables rather than risk a
  destructive migration). `PlanPostcondition` itself was kept — despite the name, it's the
  Operator loop's own live postcondition record, not part of the deleted plan model.
- **A dead message-builder cluster** in `telegram_notifications.py` (146 lines, `_task_message`
  and friends, superseded by `_user_facing_task_message` since P3 but never removed) was
  deleted. Removing it **exposed a real, live bug**: a completed workspace-launch task replied
  "The local workspace is ready." with no URL — the working `preview_url` link the dead
  formatter used to include was silently lost. Fixed with `_with_result_links()`.
- **The dead "Step" metric** in Streamlit's Live Activity panel (always showed "—", reading
  the same zero-writer `current_step_id`) now shows the real operator step count.
- **3 of 4 candidate-dead admin API routes** (`GET /api/schedules`, `GET
  /api/database/summary`, `GET /api/vscode` — each an exact duplicate of data already embedded
  in `/api/summary`) were deleted. `POST /api/vscode/terminal-commands` was investigated and
  **kept** — it's a real, unique write capability (manually queue a VS Code terminal command
  from the admin API), not a duplicate; the Streamlit UI just never built a button for it.
- **The live E2E suite** was cut from 72 to 11 cases (see Part 1, P2) — the E2E runner script
  itself (`run_all_e2e_tests.py`, 1,241 lines) was deliberately left untouched beyond a
  docstring fix, since verifying a refactor of live-Telegram-driving code isn't possible
  without actually running the full stack against real credentials.
- **`docs/ROADMAP.md` and `docs/AUDIT.md` were merged into this file** — see Part 1 §5's
  delete list. All ~98 code comments citing either by path were mechanically rewritten to cite
  this file instead, and the §2.x/§3.x subsection labels those comments cite by number were
  kept as real headers in Part 2 rather than collapsed into prose, so every citation still
  resolves to a specific section, not just "somewhere in this file."
- **`config/config.yaml` had 128 keys that were exact matches for the Pydantic schema
  default** (verified per-key, not guessed: each candidate key was removed and the resulting
  effective settings compared byte-for-byte against the original before being accepted) —
  317 lines → 144. Notably, this removed the explicit `blocked_imports` list added earlier the
  same day to close the P5 item 3 `importlib` bypass — that list now matches the code default
  exactly, so removing it doesn't change current behavior, and it means this machine will
  automatically inherit any *future* code-level denylist improvement instead of needing a
  manual sync, closing the exact class of bug (explicit YAML silently freezing out a later
  default fix) that P5 item 3 found in the first place. `config/config.example.yaml` was left
  untouched — its job is to document every field explicitly for `ybm setup` to bootstrap from,
  which is a different purpose than the live config's "diff from defaults" role.
- **A real, live bug in `ybm.ps1`'s `clean` command**, found while using it to clear stale E2E
  artifacts: `clean_agent_control.ps1`'s parameters are all `[switch]`, but `ybm clean -Caches`
  passed them through via array-splatting (`@cleanArgv`), which sends each element
  *positionally* rather than re-parsing `"-Caches"` as an actual flag reference — every switch
  was silently dropped, and `ybm clean -Caches` (or any flag) always printed the "choose at
  least one switch" usage error, no matter what was passed. Every invocation of `ybm clean`
  with a flag had been silently broken since P1 introduced `ybm.ps1`. Fixed by building a
  hashtable from the raw tokens and splatting that instead (hashtable splatting binds correctly
  to named/switch parameters; array splatting does not). Verified end-to-end: created a file
  under a target directory, ran `ybm clean -Caches`, confirmed it was removed.
- **Investigated whether `channels/responder.py`'s fallback chat path is truly dead**, per N6's
  open question. Traced it by code, not a live call: `MessageClassification.reply` is an
  *optional* field (`str | None = None`, not enforced by any validator) — the Concierge prompt
  instructs the model to always populate it for `is_task=false`, but nothing at the schema
  level guarantees a weaker/local model follows that instruction. `_non_task_response()` in
  `channels/telegram.py` falls back to the separate `responder.answer()` call whenever `.reply`
  comes back empty, and `cli.py` wires `responder` up by default whenever an LLM provider
  exists (the same condition nearly everything else in the system uses) — so the fallback is
  live-reachable in production, not just defensive dead code. **Kept, not removed.** Found and
  closed a real test-coverage gap in the process: the only existing test proved the *opposite*
  direction (`.reply` populated → responder NOT called); nothing proved the fallback actually
  fires. Added `test_telegram_non_task_falls_back_to_responder_when_concierge_reply_is_empty`.
