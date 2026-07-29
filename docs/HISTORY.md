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

**Status (2026-07-28, end of day):** P0–P3 and P6 done; P4/P5 partially done. N1–N5 done. N6
(4-page console restructure) not started. A further cleanup pass the same day removed the
plan-era code §1.1 had only flagged as dead (now physically deleted, not just unreachable),
trimmed 3 duplicate admin API routes, cut the live E2E suite from 72 to 11 cases, cut
`config/config.yaml` from 317 to 144 lines (verified redundant-with-default keys only), fixed
a real bug in `ybm clean`'s flag handling, and confirmed the fallback chat responder is live
code, not dead weight — see the closing note at the end of Part 2.

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
6. **Auditor never runs on the delivery path.** `artifact.deliver` is not in `CONTENT_TOOLS`,
   so a task whose last step is "send the file" is never audited. **Still open.**

## 5. The plan — N1 through N6

### N1 — Make it observable *(done, 2026-07-28)*

`logging_setup.py` (structlog JSON + console, called from every `cli.py` entry point,
`serve_backend.py`, and `admin_streamlit.py`); `task_id` contextvar bound in `process_task()`;
~10 silent-debug exception handlers upgraded to warnings; `print()` → logger at real service
call sites; `operator_history` rendered in the trace endpoint and Streamlit (dead
plan-rendering functions deleted); `ybm trace <task_id> [--json]`.

### N2 — Fix the three confirmed bugs *(done, 2026-07-28)*

See Part 2 §3. Each got a regression test.

### N3 — Restore the fast test loop *(tooling done, 2026-07-28; re-recording not done)*

`ybm scenario record <name> [--profile <name>]` resolves a fixture name to its test file(s),
flips their skip marker, and runs them through pytest with a live provider built from this
machine's real config. Verified mechanically (name resolution, error paths, profile lookup)
**without spending on a live LLM call**, per the standing decision not to incur real API cost
without a user check-in. Actually re-recording the 16 skipped fixtures is not done.

### N4 — Delete the dead weight *(done, 2026-07-28)*

See Part 2 §1.

### N5 — Close the safety gaps *(done, 2026-07-28)*

Approve/reject control (admin API + Streamlit + Telegram inline keyboard), kill switch,
evidence view, orphaned-audit retention — see Part 2 §4. New tests for every item; full suite
green throughout.

### N6 — Then, and only then, P4's UI restructure *(not started)*

The 4-page Streamlit split is worth doing **after** N1/N2 (now done), because before that it
would have been restructuring a UI that displayed the wrong data.

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
