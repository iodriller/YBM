# YBM Roadmap — from patchwork to product

Written 2026-07-26. Every claim in "Evidence" was observed on this machine, not inferred.
Originally superseded `PHASED_APPROACH.md`, `STEP_BY_STEP_IMPLEMENTATION.md`,
`docs/FIX_PLAN.md`, and `docs/PROJECT_GAPS.md`; all four have since been deleted (§6) —
this file is now the only planning doc in the repo.

**The goal this repo exists to serve:** *I talk to my local computer, and agents on my
local computer do whatever I ask.* Every decision below is judged against that sentence.

**Status (updated 2026-07-27): P0 and P1 are done and verified** — see the checkmarks in
§3. The "Evidence" section below is left in its original, as-found form (marked `[FIXED]`
where superseded) because it's the record of *why* each P0/P1 change was made; the current
state of each item is in §3, not here.

---

## 0. Evidence

Observed, not assumed, on 2026-07-26 — before any of this plan was executed.

**Scale.** 200 tracked files. 37,017 lines of Python. 22 scripts (17 PowerShell, 5 Python)
totalling 2,759 lines. 11 docs + 3 root planning docs, 4,380 lines. 401 unit tests.
72 E2E cases.

**Unit tests pass.** `pytest backend/tests` → 401 passed, ~2 min. Verified.

**[FIXED in P0] Dependency declaration is broken.** `streamlit`, `pandas`, `json_repair`,
and `pygetwindow` were imported by `backend/src` but appeared in no dependency group in
[../backend/pyproject.toml](../backend/pyproject.toml). They resolved only because they
happened to exist in the global Anaconda install. On any other machine — including this one
after an env rebuild — `run_admin_ui.ps1` died on import. Fixed: declared, split into a
`[desktop]` extra, `uv.lock` added.

**[FIXED in P0] No virtualenv anywhere.** Every script called bare `python`, resolving to
`C:\Anaconda3`. Fixed: `backend/.venv` via `uv`, every launcher now uses it explicitly.

**[FIXED in P0] Hardcoded absolute paths.** `scripts/run_localdeploy.ps1` and
`scripts/start_localdeploy.ps1` both hardcoded `C:\for fun\LocalDeploy`. Fixed:
`YBM_LOCALDEPLOY_ROOT` in `.env`; `start_localdeploy.ps1` deleted (redundant with
`ybm start`'s readiness wait).

**[FIXED in P0] Startup lied about success.** `start_stack.ps1` printed
`Started worker (supervised pid N)` after `Start-Process` returned — it never checked the
process was still alive. `run_supervised.ps1` then restarted a crashing child every 5
seconds forever, silently, into a log file nobody opened. A missing package produced a
green-looking startup and an invisible infinite crash loop. Fixed: crash-loop breaker (3
restarts inside 60s → `failed`, last 20 log lines printed) in
[../scripts/run_supervised.ps1](../scripts/run_supervised.ps1); `ybm start` polls a real
readiness signal per service instead of trusting `Start-Process`'s return.

**[FIXED in P1] A script referenced a test that did not exist.** `run_smoke_suite.ps1` ran
`backend/tests/test_capability_requirements_matrix.py`. No such file existed. Fixed: file
deleted, superseded by `ybm test`.

**Config drift is the top E2E failure cause.** `config/config.yaml` is gitignored;
`config/config.example.yaml` ships with every capability disabled. A fresh clone therefore
fails with `tool adapter not registered` — which is precisely the dominant failure string
in run `20260525_125109`. **Still true** — `ybm setup` bootstraps a config file but every
capability still starts disabled by design; P2's re-baseline will confirm how much this
was actually costing the pass rate.

**E2E honestly measured.** Last broad run (`run_20260525_125109`, 47 cases): **23 passed,
24 failed — 49%**. The later `run_20260525_185028` shows 11/14, but that is a hand-picked
subset, not a regression. Individual cases take 55–670 seconds; the full 72-case suite is
a multi-hour run. Two months stale, **not yet re-measured** — P2 covers this.

**There is no test tier between "unit" and "live".** 401 unit tests (20 of 47 files are
stub/mock-driven) and then a jump straight to *live Telegram + live LLM + live desktop*.
Nothing deterministic exercises worker → planner → policy → executor end to end. **Still
true** — this is P2, not yet started. (433 unit tests now, up from 401, after P0/P1's own
new coverage — still the same mocked-then-live gap in kind.)

**Stale state silently reanimates.** On starting the stack the worker immediately began
churning 7 queued tasks created in May, spawned by 7 orphan schedules pointing at dead
fixture ports (`127.0.0.1:54395`, `:60282`, …). The DB held 108 tasks, 9,403 audit events,
22.9 MB. No retention, no startup reconciliation, no schedule expiry. Cleared during this
review (DB now ~114 KB); **the mechanism that let it happen is still there** — automatic
retention/reconciliation is P6, not yet done.

**Two admin UIs.** [../backend/src/agent_control/admin.py](../backend/src/agent_control/admin.py)
(2,210 lines, FastAPI + embedded HTML) and
[../backend/src/agent_control/admin_streamlit.py](../backend/src/agent_control/admin_streamlit.py)
(1,599 lines) present overlapping views of the same data. 3,809 lines, two code paths, two
sets of bugs. **Still true** — this is P4, not yet started.

**[PARTIALLY FIXED in P1] Admin UI polish, observed by screenshot.** KPI tiles truncated
their own values (`localdepl…`, `Running, …`). Health was rendered twice
(`Backend: online` *and* `Backend: Running, 4s ago`) across 13 undifferentiated chips.
Streamlit's own chrome (`RUNNING…`, `Stop`, `Deploy`) was visible to the operator. Raw
internals leaked into the task card: `Handshake status 500 … No such target id:
BC2DB8B35B7BAEDBCE8FFC123933AD83`. One `Step` field rendered as mojibake (`�`). The whole
console was a single unstructured scroll. Fixed so far: Streamlit chrome hidden
(`--client.toolbarMode minimal`), confirmed by screenshot. **Still open:** KPI truncation,
duplicate health rendering, raw error leakage, mojibake, page structure — all P4.

**Security.** [../backend/src/agent_control/policy/access_modes.py](../backend/src/agent_control/policy/access_modes.py)
is genuinely good — 9 capability groups × 4 modes, cleanly mapped to config. The gaps are
around it: the admin API has **no CORS middleware, no Origin check, and no CSRF token**
([../backend/src/agent_control/main.py](../backend/src/agent_control/main.py) registers no
middleware), and the token is optional on loopback. Any local process — or any web page you
visit, via DNS rebinding — can `POST /admin/api/config/access-modes` and turn on desktop
control. A secret vault exists ([../backend/src/agent_control/storage/secrets.py](../backend/src/agent_control/storage/secrets.py),
Fernet) with no UI at all. **Still true, and confirmed live-relevant:** this machine's own
`config/config.yaml` has `desktop.control`, `terminal.run`, `browser.control`, and
`filesystem.write` all enabled without approval. `ybm doctor` now surfaces the missing
admin token as a `[WARN]`, but the CORS/CSRF gap itself is P5, not yet done.

**The brain plans once; it does not loop.**
[../backend/src/agent_control/orchestration/worker.py:185-260](../backend/src/agent_control/orchestration/worker.py#L185)
calls the planner once, gets a static N-step plan, executes the steps, and on failure
*replans from scratch* (capped at 2). There is no observe→decide→act cycle. This is the
single biggest architectural gap against the stated goal. **Still true** — this is P3, not
yet started; it is deliberately sequenced after P2 so the rewrite has a test net.

**Routing is keyword patchwork.** `default_plans.py` is 1,277 lines with 68 keyword-match
sites and ~30 `_looks_like_*` predicates. Its own docstring claims it "only handles
explicit system commands" — then defines 8 plan builders. `build_evaluator_recovery_plan`
dispatches by substring-matching *error message text*
(`"use_code_interpreter" in reason`, `"tool adapter not registered" in reason`).
Even `planner.py` picks its model via `_MAJOR_TASK_KEYWORDS`.

**Ten LLM roles.** 10 base prompts + 13 task prompts + 5 tool prompts: classifier,
planner, synthesizer, validator, conversation-memory, telegram-gateway, computer-use
(×3), code-interpreter, folder-OCR, health-check.

**17 tools, one 997-line registry function.** Concepts are named four different ways for
overlapping things: *capability*, *tool*, *adapter*, *connector*.

---

## 1. Diagnosis — seven root causes

Everything above collapses into seven causes. Fix these, not the symptoms.

| # | Root cause | Symptom it produces |
|---|---|---|
| R1 | **No environment contract.** No venv, no lockfile, undeclared deps, hardcoded paths. | "It doesn't install what's missing." Works only on this machine. |
| R2 | **Startup reports intent, not reality.** No readiness gate, no preflight, silent restart loop. | Green output, dead stack. |
| R3 | **No deterministic test tier.** Unit-with-mocks, then live-everything. | Can't tell a real regression from a flaky LLM. 49% E2E means nothing. |
| R4 | **Config is untracked and defaults to off.** | `tool adapter not registered`; unreproducible runs. |
| R5 | **Plan-once architecture.** Static plan + replan-from-scratch instead of an agent loop. | Recovery has to be faked with keyword rules, which is where the patchwork comes from. |
| R6 | **Keyword routing substitutes for reasoning.** | `default_plans.py`; every new failure adds another `if`. |
| R7 | **No product surface.** Two half-UIs, no install path, no first-run flow. | Not shippable, even to yourself on a new laptop. |

R5 and R6 are the same wound: because the executor cannot adapt mid-task, the only place
left to encode adaptation is a hand-written if/else chain over error strings.

---

## 2. Target architecture

### 2.1 Three agents, many tools

Collapse 10 LLM roles into 3. The named roles become *prompt sections*, not modules.

| Agent | Absorbs | Responsibility |
|---|---|---|
| **Concierge** | classifier, telegram-gateway, conversation-memory, clarify | The front door. Chat vs. task. Holds conversation memory. Asks the clarifying question. Reports progress. |
| **Operator** | planner, default_plans, recovery_policy, failure_diagnosis, executor loop | The agent loop. Given a goal + tool catalogue + policy, runs observe→decide→act until done, blocked, or out of budget. |
| **Auditor** | validator, fulfillment, synthesizer | Did we actually achieve the goal? Produces the grounded answer and the evidence trail. |

Everything else — computer-use decisions, OCR, code-interpreter prompts — is **not an
agent**. It is a tool with an internal LLM call. Stop giving them top-level prompt files.

### 2.2 The Operator loop replaces plan-once

```
goal + tool catalogue + policy + budget
  ↓
┌─────────────────────────────────────────┐
│ observe   → current state, last result  │
│ decide    → next tool call | done | ask │  ← ONE LLM call, structured output
│ gate      → policy engine (unchanged)   │
│ act       → tool                        │
│ record    → audit + attempt history     │
└──────────────┬──────────────────────────┘
               └── loop until done / blocked / budget
```

This is ~200 lines. It deletes the need for `build_evaluator_recovery_plan`, the
`_looks_like_*` family, and the replan-from-scratch path — recovery becomes "the next
decide() call sees the error in context," which is what an LLM is actually good at.

Keep the plan as an *artifact for the UI* (so the operator can watch), not as a
contract the executor is forced to follow to the end.

### 2.3 Naming: two concepts, not four

- **Capability** — a permission scope the user toggles (`filesystem.write`). Stays.
- **Tool** — a callable unit with a JSON schema (`filesystem.manage`). Stays.
- **Adapter**, **connector** — *delete from the vocabulary.* An adapter is just a tool's
  implementation; a connector is just a tool. Rename `adapter.factory` → `tool.author`.

### 2.4 Framework decision: build the loop, buy the sandbox and the protocol

You asked whether to adopt LangGraph or AWS Strands. My recommendation, and the reasoning:

**Do not port the orchestrator to LangGraph or Strands.** You already have the parts those
frameworks are mainly bought for, and yours are better fitted: durable SQLite task state,
an atomic worker claim, an audit trail, an approval gate, and a capability policy engine.
LangGraph's checkpointer would duplicate and fight your task store, and — the part that
matters — a graph framework makes it *easier* to route around your policy gate, which is
the one invariant that must never be bypassable. The thing you genuinely lack is the agent
loop, and that is ~200 lines, not a dependency.

**Do buy, in two places:**

1. **The sandbox.** `code.interpreter` accepts an `allowed_imports` knob that is never
   enforced, and the Docker backend is half-finished. Generated code executing unsandboxed
   on your daily-driver machine is the sharpest edge in the repo. Make the Docker backend
   the *default* for anything generated, and keep local Python only for trusted paths.
2. **The protocol.** You already ship both `mcp_client` and `mcp_server`. Make **MCP the
   single tool interface** — internal tools registered the same way external ones are.
   That is how you get "if it sees something missing, it goes and finds it" without
   inventing a second plugin system.

Revisit LangGraph only if you later want multi-agent fan-out with human-in-the-loop
resumption *across processes*. You do not need that to hit the stated goal.

---

## 3. The plan

Seven phases. Each has an exit criterion you can check. **P0 and P1 are the ones that make
the repo usable by a second person; do not reorder them behind the fun architecture work.**

### P0 — Make it install and start honestly *(~2 days)*

1. `uv`-managed venv + committed lockfile. Declare `streamlit`, `pandas`, `json_repair`,
   `pygetwindow`. Split extras honestly: `[desktop]`, `[voice]`, `[e2e]`, `[dev]`.
2. **`ybm doctor`** — preflight that checks and *reports*, one line per item: Python
   version, venv active, every declared import resolvable, DB writable, ports 8000/8765/8501
   free, LocalDeploy reachable, config present and valid, Telegram token present.
   Non-zero exit on failure. Runs automatically as step 1 of `ybm start`.
3. **`ybm setup`** — creates venv, installs, generates `config/config.yaml` from the
   example, generates the admin token and vault key, prompts for Telegram credentials.
4. Replace both LocalDeploy path constants with `YBM_LOCALDEPLOY_ROOT` env / config, with
   a clear error when unset.
5. Supervisor: **crash-loop breaker.** More than 3 restarts in 60s → mark the service
   `failed`, surface the last 20 log lines to stdout and the admin UI, stop restarting.
6. `ybm start` waits for a real readiness signal per service and prints a truthful
   summary table. Never print "Started" for a process that has already exited.

**Exit:** on a clean Windows box with a fresh clone and no Python packages,
`git clone && ybm setup && ybm start` reaches a working admin UI, or tells you exactly
what is missing and how to fix it.

### P1 — Collapse 22 scripts into one CLI *(~1 day)*

One entry point, `ybm`, backed by the existing `cli.py`:

```
ybm setup | doctor | start | stop | restart | status | logs [service] [-f]
ybm test  [--unit|--scenario|--all]
ybm e2e   [--suite smoke] [--only ID]
ybm db    inspect | clean | reset
ybm config show | set <path> <value>
```

Delete: `init_db.ps1`, `run_backend.ps1`, `run_worker.ps1`, `run_scheduler.ps1`,
`run_telegram_polling.ps1`, `run_admin_ui.ps1`, `run_coding_session_watcher.ps1`,
`run_localdeploy.ps1`, `start_localdeploy.ps1`, `run_tests.ps1`, `run_smoke_suite.ps1`
(broken anyway), `clean_agent_control.ps1`, `login_telegram_e2e.ps1`,
`test_dizibox_5_episodes.py`, `test_e2e.py`. Keep `start_stack.ps1`/`stop_stack.ps1` as
three-line shims that call `ybm` — muscle memory is worth preserving.

**Exit:** `scripts/` holds ≤ 4 files. Nothing in the README references a `.ps1` directly.

### P2 — Build the missing test tier *(~3 days — highest leverage in the plan)* — **foundation done, porting not started**

Introduce **scenario tests**: real DB, real registry, real policy gate, real worker loop,
real tools against a temp filesystem — with a **scripted LLM** that replays recorded
responses. No Telegram, no network, no GPU. Deterministic. Whole suite in seconds.

**Done (2026-07-27):**
- `backend/src/agent_control/testing/scripted_llm.py` — `ScriptedLLMProvider` replays a
  JSON fixture keyed on exact (method, system_prompt, user_prompt) text and *fails loudly*
  on an unrecorded prompt; `RecordingLLMProvider` wraps a live provider and writes every
  call to a fixture file (the `--record` capability, as a Python API rather than a CLI
  flag). Fixture keys normalize any 6+-char hex run before hashing — some tools (e.g.
  `code.interpreter`'s per-task workspace dir) embed a fresh random task id straight into
  their own LLM prompt, which would otherwise make every fixture a one-time-use cache miss.
  9 unit tests.
- `backend/tests/scenario/harness.py` — wires the real worker/planner/policy/registry/
  executor stack (mirrors `cli.run_worker()`'s production wiring exactly) against a temp
  DB, with `isolated_settings()` guaranteeing zero influence from this repo's real
  `config/config.yaml` or `.env` (see the note on that function — a partial
  `adapters={...}`/`capabilities={...}` override does **not** fully replace the YAML
  source; pydantic-settings deep-merges nested fields, confirmed by a scenario test that
  silently inherited this repo's real `allowed_roots` and passed by accident).
- 3 scenario tests proving the pattern across the two real usage shapes: a zero-LLM
  deterministic path (`status_request`), and two content-tool round-trips exercising
  planner → tool → validator → synthesizer with real recorded fixtures
  (`filesystem_search`, `code_interpreter_fibonacci` — the latter runs a real local Python
  subprocess). Reran 3× consecutively to confirm reproducibility. Full run: ~1.2s.
- Fixed a real, verified test-isolation bug this surfaced: 14 of 23 tests in
  `test_admin.py` broke the instant this repo's real `.env` gained an `AGENT_ADMIN_TOKEN`
  (reproduced deliberately, then fixed, then re-verified the fix holds under the same
  reproduction). Root cause: `AppSettings()` construction calls
  `_load_env_file_into_process()`, which does `os.environ.setdefault(...)` from the real
  `.env` — a **permanent, process-wide** mutation nothing later in that pytest process can
  undo, including a later test's own `monkeypatch.chdir`. One test skipping the
  `monkeypatch.chdir`-before-first-`AppSettings()` pattern poisons every test that runs
  after it in the same process. All 23 now chdir first.

**Not done — still the biggest remaining gap in this plan:**
- Only 3 of 72 E2E cases are ported. "Port the majority of the 72 E2E cases down to this
  tier" (the original ask) did not happen — each one takes real per-case work (a
  representative objective, a recorded fixture, sometimes several recording attempts:
  gpt-4.1 at temperature 0.1 was *not* fully deterministic during recording — one objective
  needed three rephrasing attempts before the model stopped planning a spurious extra step.
  That's a real, reproducible finding about LLM planning reliability, not just a recording
  nuisance — worth keeping in mind for P3's `decide()` design).
- The live E2E suite has **not** been trimmed to ~10 cases — trimming it now, before
  equivalent scenario coverage exists, would be a net loss of coverage, not a cleanup.
- `run_all_e2e_tests.py` has not been retired/thinned.
- `ybm test` does not yet have a `--scenario`-only flag; `backend/tests/scenario/` currently
  runs as part of the normal `ybm test` / `pytest backend/tests` pass (which is fine at 3
  cases — revisit once there are enough scenario tests that separating them from the fast
  unit tier actually matters).

**Exit (unchanged, not yet met):** the majority of the 72 E2E cases are covered by
scenario tests, the live suite is down to ~10, and a red scenario test reliably means a
real regression rather than an LLM having a bad day.

### P3 — Rebuild the brain *(~4 days)*

1. Implement the Operator loop (§2.2). Land it behind a config flag next to the existing
   path, then flip the default once P2's scenario suite is green on both.
2. Delete `default_plans.py` down to the genuinely deterministic cases (status requests).
   Target: **1,277 → under 200 lines.** Everything keyword-driven goes.
3. Delete `build_evaluator_recovery_plan` entirely — the loop subsumes it.
4. Merge the 10 prompt roles into 3 agent prompts (§2.1).
5. Delete `_MAJOR_TASK_KEYWORDS`; pick the model from declared task complexity or plan
   size, not from substrings.
6. Split `build_tool_registry` (997 lines) — each tool module declares its own
   `TOOL_SPEC`; the registry just collects them. New tool = new file, no registry edit.

**Exit:** zero `_looks_like_*` functions. Zero routing decisions made by substring match
on user text or error text. Scenario suite still green.

**Known local-model capacity issue (carried over from the retired `docs/FIX_PLAN.md`
investigation, still relevant to the Operator loop's `decide()` call):** the weaker local
models struggle with structured planning — wrong tool choice, malformed generated code,
can't repair their own schema mistakes on retry. Options, cheapest first: (a) route
`decide()` through `major_provider` for complex objectives (already how `planner.py` picks
a bigger model today, via `_MAJOR_TASK_KEYWORDS` — P3 replaces the keyword trigger but the
two-tier model selection itself is worth keeping); (b) self-consistency — run `decide()` 2-3×
with the same context, keep the first candidate that passes registry validation, at 2-3x
token cost; (c) per-pattern deterministic short-circuits as a last resort only — this is
exactly the keyword-branch pattern P3 is trying to delete, so reach for it last, not first.

### P4 — One real admin UI *(~4 days)*

**Pick Streamlit and delete the other one.** Streamlit is the honest choice here: it is
local-first, you already have 1,599 working lines, and a React SPA is a project you do not
need. Delete the 2,210-line embedded-HTML admin in `admin.py`, keeping only the JSON API
it exposes.

Then make it a console rather than a dump:

- **Structure:** four pages — *Now* (live activity), *Tasks* (history + trace), *Access*
  (security), *Settings*. Not one scroll.
- **Fix the observed defects:** stop truncating KPI values; render health once, not twice;
  hide Streamlit chrome (`[client] toolbarMode = "minimal"`); fix the `�` encoding; never
  surface raw DevTools/stack text — show a plain cause with the detail behind a disclosure.
- **Access page** (this is the "toggles" you asked for): the 9 access groups as the
  primary control, plus what is currently unreachable from any UI — **allowlisted roots**,
  **HTTP host allowlist**, **secret vault** entries (write-only), **per-tool enable**, and
  a **kill switch** that sets every group to Off.
- **Evidence:** for every completed task show *what was actually touched* — files written,
  URLs fetched, commands run, secrets read — sourced from the audit trail you already
  write. This is the "we need to be able to see the result of it" ask, and the data is
  already there.

**Exit:** one admin module. A new user can grant filesystem access, run a task, and see
exactly what it touched, without reading a log file.

### P5 — Close the security gaps *(~1.5 days)*

1. Add CORS + `Origin` checking + a CSRF token to the admin API. Today any local page can
   flip your capability toggles.
2. Bind the admin token by default at `ybm setup` rather than leaving it optional.
3. Enforce `allowed_imports` in `code.interpreter`, or delete the knob. A config field that
   silently does nothing is worse than no field.
4. Default generated code to the Docker backend; local Python only for trusted paths.
5. Approval requests carry a **diff/preview** (files to be changed, command to be run) —
   approving blind is not approval.

**Exit:** `ybm doctor --security` passes. A hostile local web page cannot change your
capability config.

### P6 — Operational hygiene *(~1 day)*

1. **Startup reconciliation:** on worker boot, any task left `running`/`interpreting` by a
   dead worker is failed or requeued explicitly — never silently resumed.
2. **Retention:** default 30-day audit/task retention, `ybm db clean` to run it.
3. **Schedule hygiene:** schedules expire, and a schedule whose target has failed N times
   consecutively disables itself and notifies. The 7 dead May schedules found today would
   never have survived this rule.
4. Move `agent_control.db` under `.agent_control/` so runtime state lives in exactly one
   place.

**Exit:** starting the stack after a month idle does not resurrect month-old work.

---

## 4. Reaching the actual goal

The stated goal needs three things this plan does not fully cover. They sit **after P3**,
because they are cheap once the Operator loop exists and expensive before it.

- **Claude Code / Codex / Copilot as first-class backends.** `coding_agent.py` already
  shells out to all three CLIs (`PROVIDERS = ("codex", "github_copilot", "claude_code")`).
  Add the **Claude Agent SDK** as an in-process backend alongside the CLI path — it gives
  you streaming progress and structured tool events instead of scraping stdout, which is
  what makes "show me what it's doing" possible.
- **Tool authoring that actually closes the loop.** `adapter.factory` writes a proposal to
  a cache and stops. Wire it end to end: write → sandbox-test → register over MCP → use it
  *in the same task*. That is the difference between "it can write code" and "it can give
  itself a new ability."
- **Computer use as a real capability.** Today it is Windows-only, capped by
  `max_steps`, and its three prompts are top-level roles. Make it one tool with an internal
  loop, and gate it on the Access page like everything else.

---

## 5. Sequencing

```
P0 install & start honestly    ██                    ~2d
P1 one CLI                     ██                    ~1d
P2 scenario test tier          ████                  ~3d   ← unblocks safe change everywhere
P3 rebuild the brain           █████                 ~4d
P4 one real admin UI           █████                 ~4d   ← can run parallel to P3
P5 security gaps               ██                    ~1.5d
P6 operational hygiene         █                     ~1d
                                                     ≈ 3.5 weeks solo
```

If you only ever do three: **P0, P2, P3.** P0 makes it installable, P2 makes it safe to
change, P3 removes the patchwork. P4 makes it presentable, but a beautiful console over a
keyword-routing brain is still a demo.

---

## 6. Delete list

Stale, superseded, or dead. Deleting these is part of the work, not housekeeping.

| Path | Why | Status |
|---|---|---|
| `PHASED_APPROACH.md` | Last touched 2026-05-16. Superseded by this file. | **Deleted** |
| `STEP_BY_STEP_IMPLEMENTATION.md` | Same. | **Deleted** |
| `docs/FIX_PLAN.md` | P1–P6 executed 2026-05-25. Historical. | **Deleted** (one live finding — the local-model planning-capacity issue — carried into P3 above) |
| `docs/PROJECT_GAPS.md` | Snapshot; several claims already fixed (worker claim now exists, `default_plans` is 1,277 not 3,424). | **Deleted** |
| `docs/EXTENSION_IMPLEMENTATION_PLAN.md` | 1,358 lines for a 3-command extension, marked "complete" in its own text. | **Deleted** |
| `docs/PROMPT_GAP_ANALYSIS.md` | Obsolete once prompts collapse to 3 agents. | **Deleted** |
| `docs/FLOW.md`, `docs/FLOW_DIAGRAMS.md`, `docs/TASK_FLOW.md` | Three overlapping flow docs, one (FLOW_DIAGRAMS) mostly a single hardcoded example that would go stale the moment prompts change. | **Deleted**, merged into `docs/ARCHITECTURE.md` |
| `docs/GETTING_STARTED_ADMIN_TELEGRAM_LLM.md` | Found during the docs pass, not in the original list: duplicated `LOCAL_SETUP.md`'s setup steps and `ARCHITECTURE.md`'s behavior sections, including duplicating itself (two "Local Workspace" sections in one file). | **Deleted**, unique content merged into `ARCHITECTURE.md` |
| `scripts/*` (15 of 22) | Listed in P1. | **Deleted/relocated** — see P1 §"delete superseded scripts" |
| `sales_data.xlsx` | Thought to be a committed test fixture. | **Not tracked by git and already `.gitignore`d** (`*.xlsx`) — nothing to do |
| `backend/src/agent_control/admin.py` HTML | 2,210 lines of second UI (keep the JSON API). | Pending — P4 |
| `default_plans.py` (~1,100 of 1,277 lines) | Keyword routing, replaced by the Operator loop. | Pending — P3 |

Docs now: `README.md`, `CLAUDE.md`, `docs/ROADMAP.md`, `docs/ARCHITECTURE.md`,
`docs/LOCAL_SETUP.md`, `docs/MINIMAL_END_TO_END_TEST.md`, `docs/DATABASE_INSPECTION.md`,
`e2e/README.md` — 8 files, down from 16 (6,007 → ~1,600 lines).

---

## 7. Honest unknowns

- The 49% E2E figure is from May and was **not re-measured** — you asked me not to run the
  stack against the stale queue, which was the right call. Re-baseline after P0+P4 fix the
  config-drift cause; the true number is probably higher.
- I did not audit `browser.py` (963 lines) or `computer_use.py` (513 lines) for
  correctness — only for how they are routed to.
- Effort estimates assume you are working with an agent, not by hand. Treat them as
  relative weights, not commitments.
- Whether Streamlit stays sufficient at P4's ambition is the one call I would revisit
  after building the *Now* page. If it fights you there, it will fight you everywhere.
