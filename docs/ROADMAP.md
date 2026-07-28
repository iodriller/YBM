# YBM Roadmap — from patchwork to product

Written 2026-07-26. Every claim in "Evidence" was observed on this machine, not inferred.
Originally superseded `PHASED_APPROACH.md`, `STEP_BY_STEP_IMPLEMENTATION.md`,
`docs/FIX_PLAN.md`, and `docs/PROJECT_GAPS.md`; all four have since been deleted (§6) —
this file is now the only planning doc in the repo.

**The goal this repo exists to serve:** *I talk to my local computer, and agents on my
local computer do whatever I ask.* Every decision below is judged against that sentence.

**Status (updated 2026-07-28): P0 and P1 are done and verified; P3's core (steps 1–3) is done
and verified** — the Operator loop (§2.2) is now the sole execution path, replacing plan-once-then-replan
and its keyword-driven recovery entirely (R5/R6, both fixed structurally, not patched). See the
checkmarks in §3. The "Evidence" section below is left in its original, as-found form (marked
`[FIXED]` where superseded) because it's the record of *why* each P0/P1 change was made; the
current state of each item is in §3, not here.

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

### P2 — Build the missing test tier *(~3 days — highest leverage in the plan)* — **foundation done, porting under way (16/72)**

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
- 16 scenario cases (32 tests) ported so far, covering every category exercised by real tool
  execution: a zero-LLM deterministic path (`status_request`), content-tool round-trips
  through planner → tool → validator → synthesizer (`filesystem_search`, `folder_inspection`
  — `inspect_folder` operation, `file_find_and_read` — a 2-step plan inside one tool
  (search then read_file), `code_interpreter_fibonacci` — runs a real local Python
  subprocess, `document_pdf_summary` — real PDF via the same minimal-PDF writer
  `e2e/fixtures.py` uses), a non-content-tool deterministic-completion path
  (`schedule_create`), two delivery-only completion paths with no synthesizer at all
  (`code_interpreter_csv_summary` — code.interpreter twice, then `artifact.deliver`;
  `code_interpreter_generate_file` — single code.interpreter call, then `artifact.deliver`),
  a third and fourth delivery-only path (`send_found_pdf` — one literal-path resolve, one
  `artifact.deliver`, recorded clean with zero replan on the first attempt, unlike
  `code_interpreter_csv_summary`/`output_delivery` above; `desktop_file_search_then_delivery`
  — search-then-deliver, also clean first try), an implicit-routing case
  (`implicit_code_interpreter_numbers_report` — objective never says "code interpreter";
  asserts the planner still picks it over `coding.agent`/`vscode.copilot_terminal`, which
  are both in the same registry and could plausibly be chosen instead), a fourth
  code.interpreter variant (`code_interpreter_json_transform` — data given inline in the
  objective, no fixture file needed), two MCP-category cases against a real fake MCP server
  subprocess over stdio, not a mock (`mcp_call_fake_echo`, `mcp_discover_tools` — see the
  real fulfillment-gap bug both of them surface, below), and one through the new Operator
  loop (`operator_loop_filesystem_search` — 2 real tool calls chosen one at a time from
  history, not a single-shot happy path). Reran repeatedly (including both MCP subprocess
  cases, twice more each) to confirm reproducibility. Full scenario suite: ~10s.
- Real bug found and documented, not fixed, while recording `mcp_call_fake_echo` and
  `mcp_discover_tools`: every `mcp.client` step succeeds cleanly (right server, right tool,
  exact echoed text for `call_tool`; correct tool list for `list_tools`, both zero-replan) -
  but the task still ends in CLARIFYING either way. Confirmed it isn't operation-specific:
  `list_tools`'s recorded plan self-declares the identical wrong postcondition type as
  `call_tool`'s. Root cause:
  `PostconditionType` has no entry for "an external/MCP tool call returned a result," so the
  planner's own self-declared `plan.postconditions` picks the closest-sounding option,
  `adapter_proposal` (meant for `adapter.factory` scaffolding a *new* adapter).
  `fulfillment.py`'s `_postcondition_satisfied()` for that type checks for an `adapter_dir`,
  which a `mcp.client` call never produces, so the gap never closes and the worker retries
  an already-succeeding call 3 times before giving up. `_postconditions_from_plan()` already
  has the right deterministic, tool-name-based pattern for this exact mismatch (used for
  `adapter.factory`/`artifact.deliver`/`document.manage`) but it's never reached —
  `expected_postconditions()` trusts the plan's self-declared list first, only falling back
  to the reliable tool-derived rules when that list is empty. Not fixed here: a proper fix
  needs either a new `PostconditionType` plus prompt guidance (touches
  `planner_system.md`, which is embedded in *every* scenario fixture's key — would force
  re-recording all 16 fixtures, not something to bundle into a porting pass) or a priority
  change in `expected_postconditions()` (a bigger behavioral change than warranted here).
  `test_mcp_call_fake_echo.py`/`test_mcp_discover_tools.py` assert the actual guaranteed
  behavior (tool call correctness) rather than the ideal one (task completion) — see those
  files' docstrings. Worth a dedicated P3-adjacent item: add a `PostconditionType` for
  external/MCP tool results.
- Second fixture-reproducibility gap found and fixed, on top of the hex-run one from the
  original P2 foundation work: `tempfile.TemporaryDirectory()`/pytest's `tmp_path` name
  directories `"tmp"` + 8 chars from `[a-z0-9]` — not restricted to hex digits, so the
  existing `_HEX_RUN` normalizer didn't catch it. When a plan step's relative path resolves
  against that randomized CWD and gets rejected by policy, the error text embeds the random
  dir name, which then feeds the next retry prompt on a worker-level replan — a fresh,
  unmatchable fixture key every run (hit while first recording `output_delivery`, before
  that case was set aside — see below). Fixed generally in
  `testing/scripted_llm.py`: `_normalize()` now also collapses `\btmp[a-z0-9]{6,}\b` before
  hashing. Verified with a new unit test in `test_scripted_llm.py`.
- One case attempted and deliberately set aside rather than force-fixed: `output_delivery`
  ("create a text file, then send it to me"). Confirmed reproducibly across 3 independent
  live recordings that the planner *always* needs one internal registry-validation repair
  for this objective (attempt 1 invents `filename`+`root` fields; attempt 2 correctly uses
  `path`, but still uses `text` instead of the schema's `content` field —
  `ToolInputModel`'s `extra="allow"` config means that's silently kept as an unused extra
  rather than rejected, not a crash but a real, separate content-fidelity gap in the same
  family as the `code_interpreter_csv_summary` finding above). On top of that,
  something further downstream is non-deterministic across separate process runs even
  against one fixed, self-consistent fixture — confirmed it isn't Python hash-randomization
  (tested explicitly with `PYTHONHASHSEED=0` fixed across runs; no change) but didn't
  chase it further given the time already sunk relative to one test case. Deferred, not
  abandoned — the harness fixes above (delivery support, tempdir normalization) both came
  out of this attempt and now help every future case.
- Harness gap found and fixed while recording `code_interpreter_csv_summary`:
  `build_scenario()` passed `telegram_client=None`, and tasks created by
  `run_task_to_completion()` had no `source_chat_id` in metadata. Both are fine for
  synthesized-answer endings, but the planner routinely adds an `artifact.deliver` step
  after a tool creates a file — completely reasonably, this is what production does too —
  and that step then fails with a synthetic "chat_id is required" error that would never
  happen against a real Telegram client with a real inbound message. Fixed generally, not
  per-test: `harness.py` now has a `FakeTelegramClient` (records sent files, mirrors
  `test_artifact_delivery.py`'s), wired into every scenario's registry, and
  `run_task_to_completion()` stamps `source_chat_id` on every task by default. Unblocks
  every future delivery-ending case (`output_delivery`, `send_found_pdf`,
  `screenshot_capture`, and others still unported) without each one rediscovering this.
- Real gap found and *not* fixed, deliberately, while recording `code_interpreter_csv_summary`
  (see that test file's docstring for the full trace): the recorded script for "read
  expenses.csv, calculate the total, write expense-summary.json" ran without error but
  wrote a JSON echo of the objective text instead of an actual computed total.
  `generate_and_run` only regenerates on `SyntaxError`/`ValueError` — valid-but-wrong Python
  is invisible to it — and a delivery-only plan (ends in `artifact.deliver`, no synthesis
  step) has no validator reading the delivered file's content against the objective, unlike
  the content-tool round-trips (`document_pdf_summary`'s AnswerValidator would have caught
  an analogous PDF-summary failure). Out of scope to fix while porting E2E cases — this is
  an architecture gap (LLM-generated code correctness has zero verification anywhere in
  this path), not a bug in the test or the port. Worth a P3/P5-adjacent backlog item:
  content-integrity checking for code.interpreter output, not just execution-success.
- Fixed a real, verified test-isolation bug this surfaced: 14 of 23 tests in
  `test_admin.py` broke the instant this repo's real `.env` gained an `AGENT_ADMIN_TOKEN`
  (reproduced deliberately, then fixed, then re-verified the fix holds under the same
  reproduction). Root cause at the time: `AppSettings()` construction called
  `_load_env_file_into_process()`, doing `os.environ.setdefault(...)` from the real `.env` —
  a **permanent, process-wide** mutation nothing later in that pytest process could undo,
  including a later test's own `monkeypatch.chdir`. All 23 tests now chdir first regardless.
  (That env-loading mechanism was independently refactored during this same work to use
  pydantic-settings' native `dotenv_settings` source instead — which doesn't mutate global
  `os.environ` at all, closing the same hole at the root. Re-verified the reproduction
  still passes clean under both fixes together. One casualty: `bootstrap.py`'s
  `_check_localdeploy` read `os.environ.get("YBM_LOCALDEPLOY_ROOT")` directly instead of
  the `read_env_value()` helper every other check uses, and silently stopped seeing a value
  that's genuinely in `.env` — fixed to match the other checks.)
- Two real, non-test product gaps surfaced and fixed while recording fixtures against a
  live LLM — not found by reading the code, only by actually running the policy engine and
  planner against real tool schemas: (1) `ToolDefinition` carries no static `risk_level`,
  and the policy engine denies a request outright if it's understated — the Operator
  loop's first draft defaulted every call to one fixed level and would have silently
  denied half the catalog; fixed by having `OperatorDecision` declare risk per call like
  `PlanStep` does. (2) `schedule.manage` had zero worked examples in its `ToolDefinition`
  (every other multi-field tool has some) — the planner reliably invented a nonexistent
  `tool_input` shape (a `frequency` field, a nested `task` object) and had to replan almost
  every time. Added three worked examples; the malformed-shape replan stopped recurring
  across repeated re-recordings.

**Not done — still the biggest remaining gap in this plan:**
- 16 of 72 E2E cases are ported. "Port the majority of the 72 E2E cases down to this
  tier" (the original ask) has not happened yet — each one takes real per-case work (a
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

### P3 — Rebuild the brain *(~4 days)* — **steps 1, 2, 3, 5, and 6 done and verified; step 4 not started**

1. ~~Implement the Operator loop (§2.2). Land it behind a config flag next to the existing
   path, then flip the default once P2's scenario suite is green on both.~~ **Done
   (2026-07-27), then made the sole execution path (2026-07-28) — the old plan-once path was
   deleted, not just defaulted off.** Explicit user decision after being asked directly
   (`AskUserQuestion`, given the known gaps below): "enable the 3 agent simpler loop and get
   rid of the old system, we need to move forward." Before flipping the default, closed every
   gap between the Operator loop and the plan-based path that would otherwise have been a
   silent regression:
   - **Approval flow.** A `NEEDS_APPROVAL` tool result now creates a real `ApprovalRequest`
     (the executor's policy engine already creates one — the fix was to *stop* creating a
     second, duplicate one, caught by a test asserting exactly one `ApprovalRequest` row) and
     transitions to `AWAITING_APPROVAL` with the pending call stashed in
     `metadata["operator_pending_call"]`. `_process_operator_awaiting_approval()` replays that
     exact call with `approved=True` once every approval on the task clears — same
     plain-text-"approve" Telegram resume path as before, since it only checks
     `ApprovalStatus`, not plan shape.
   - **Fulfillment gaps.** A `done` decision is checked against `validate_fulfillment()`
     (objective-inferred postconditions, since there's no `PlanModel` to derive them from)
     before being allowed to complete; a gap is appended to `history` as an observation and
     the loop continues instead of completing — "the next `decide()` call sees the error in
     context," capped at 2 cycles so a model that can't close the gap doesn't loop forever.
     This *exposed a real, pre-existing bug* in `fulfillment.py`'s `_postconditions_from_objective()`:
     it tokenizes the raw objective text for keyword matches, and objectives routinely embed
     a literal filesystem path (`"look in the folder C:\...\operator_loop_filesystem_search"`)
     — the trailing path segment `..._search` got word-split into the token `"search"`,
     spuriously matching the `BROWSER_STATE` trigger and producing a fulfillment gap that
     could never close (nothing in the task ever produces browser state). This path was always
     reachable (it's `expected_postconditions()`'s fallback when a plan produces no
     postconditions) but rarely exercised, since a `PlanModel` almost always existed; wiring
     fulfillment checks into the Operator loop made it the *primary* path and immediately hit
     the bug on the very first non-trivial scenario re-run. Fixed at the root — strip
     path-shaped substrings before tokenizing (`fulfillment.py`'s `_strip_embedded_paths()`) —
     not by special-casing "search," since the failure mode is the pattern (path segments
     read as intent), not any one keyword.
   - **Rate limits.** `_operator_retry_or_ask()` ports the plan-based path's backoff
     (`RETRYING` status + computed `next_retry_at` for `RATE_LIMITED`/transient failures;
     immediate `ask_user` for `USAGE_LIMITED`, since that needs a human decision, not a
     wait) — without this, a rate-limited tool would get retried on every ~3s poll tick
     instead of backing off.
   - **Background sessions.** `coding.agent` can report a session still running
     (`status=running` + `session_id`) rather than a finished result. `_await_operator_external()`
     transitions to `AWAITING_EXTERNAL`; `_resume_operator_pending_external()` picks it back up
     once `metadata["pending_tool_result"]` appears. The completion callback
     (`cli.py`'s `_coding_session_completion_callback`) needed no changes — it only checks
     `task.status == AWAITING_EXTERNAL`, not plan shape, so it already worked for both paths.
   - **Model escalation.** `OperatorLoopService` gained the same reactive
     `major_provider` escalation item 5 gave the planner (retry the *next* attempt on the
     bigger model only after an observed structured-output failure) — this was about to be
     silently lost since it lived in `PlannerService`, not `operator.py`.
   - Deliberately **not** ported: the plan-based path's `diagnose_failure`/`choose_recovery`
     keyword-driven recovery routing, and its same-tool/same-strategy attempt-limit counters.
     This is the intended design change (§2.2) — the LLM sees the failure in `history` and
     decides itself whether to retry, switch tools, or ask the user, rather than a hand-coded
     `FailureType` → `RecoveryAction` table doing it for them; `operator_max_steps` (default 8)
     is the backstop against a model that can't converge, replacing per-strategy counters with
     one overall budget. Also not ported: the plan-based path's raw-output validator/synthesizer
     gate before completing a "content tool" call — the Operator's own `decide()` call is
     expected to serve that role; tracked as part of item 4 (Auditor absorbs
     validator/fulfillment/synthesizer), not silently dropped.
   - **Verified:** 21 unit tests on the worker integration (tool-then-done, ask_user, blocked,
     step-budget exhaustion, unregistered tool, the full approval-flow round trip, fulfillment-gap
     continue/resolve/exhaust, rate-limit backoff round trip, usage-limit ask-user, background-session
     await/resume, and a misconfigured-worker guard), 9 unit tests on `OperatorLoopService` (incl. the
     2 new major-provider-escalation tests), and the 2 pre-existing operator-loop scenario tests
     through the real worker/registry/policy/executor stack — one of which (`test_operator_loop_finds_and_reads_resume_file`)
     is what surfaced the fulfillment-gap path-tokenizing bug above; both green after the fix.
     Full backend suite green throughout (`pytest backend/tests`, 0 failures). Live-wiring smoke test
     against real `config/config.yaml` (isolated throwaway DB, not the real one): registry builds
     18 tools, `TaskWorker` constructs, `process_next()` runs cleanly. `ybm doctor`: 21 ok, 6 warning
     (expected — nothing running), 0 failures.
   - **Known, disclosed regression, not silently swept under the rug:** the 16 scenario tests
     ported in P2 that exercised the plan-based path (`PlannerService`/`ResponseSynthesizer`/`AnswerValidator`
     prompts) cannot run against the Operator loop's fixtures — the fixtures are keyed on exact
     `(system_prompt, user_prompt)` text, and the Operator's prompt is entirely different from the
     planner's. Re-recording requires a live LLM call per case, which this pass did not do. All 16
     files (31 test functions) are marked `pytest.mark.skip` with an explicit reason, not deleted —
     the scenario each documents (objective, assertions, known gaps) survives as a checklist for
     whoever re-records them against the Operator loop next. **P2 scenario coverage is 2/18 (was
     16/18) until that re-recording pass happens** — this is the most concrete, well-scoped follow-up
     coming out of this change.
   - One correctness bug from the original 2026-07-27 landing, still worth flagging: `ToolDefinition`
     has no static `risk_level`, and the policy engine denies a request outright if `risk_level >
     capability.max_risk_level` — an early version defaulted every operator tool call to a fixed risk
     level and would have silently denied half the catalog. Fixed by having `OperatorDecision` declare
     risk per call.
2. Delete `default_plans.py` down to the genuinely deterministic cases (status requests), then
   delete the rest once the Operator loop replaced it. **Done.** `build_default_task_plan`
   turned out to already match the target shape on 2026-07-27 (status-only, ~19 lines) — see
   the original note below, unchanged. Once the Operator loop became the sole execution path
   (item 1), even that status-only shortcut became unreachable (the loop calls `task.status`
   itself, same as any other tool) — **`default_plans.py` was deleted in full**, along with
   `orchestration/recovery_policy.py`, `orchestration/failure_diagnosis.py`, and
   `orchestration/attempt_history.py` (all exclusively used by the plan-based recovery path
   this item and item 3 together removed). *(Original 2026-07-27 note, preserved: checked the
   current source before assuming the roadmap's original framing still held —
   `build_default_task_plan`'s own docstring said "only handles status requests and nothing
   else," confirmed true (~19 lines). Also found and removed 242 lines of dead code
   [`build_default_vscode_development_plan` and helpers] never wired into production, before
   this item's main deletion — see the commit history for the full trace.)*
3. Delete `build_evaluator_recovery_plan` entirely — the loop subsumes it. **Done (2026-07-28),
   as part of item 1's migration.** The gating condition this item was waiting on — "a decision
   to make the Operator loop the default execution path... not something to flip silently" —
   is exactly what got decided explicitly (see item 1). `build_evaluator_recovery_plan`, its
   ~1,000 lines of keyword-driven recovery-stage routing, and every helper exclusive to it are
   gone along with the rest of `default_plans.py` (item 2). `orchestration/worker.py` also lost
   every plan-based-only method as dead code once the Operator loop became unconditional:
   `_process_planned`, `_process_running`, `_handle_step_result`, `_await_external`,
   `_replan_after_mcp_catalog`, the old `_process_retrying`/`_process_awaiting_approval`,
   `_create_step_approval`, `_step_is_approved`, `_attach_recovery_plan`, `_retry_decision`,
   `_ask_user`, `_validate_and_synthesize`, `_replan_with_error`, `_transition`, and their
   exclusive module-level helpers (`_resolve_step_input`, `_replace_placeholders`,
   `_route_decision`, etc.) — `worker.py`: 1,761 → 978 lines. `llm/planner.py`,
   `llm/synthesizer.py`, `llm/validator.py`, and `orchestration/clarify.py` were deleted
   outright (nothing outside the deleted plan-based path referenced them);
   `llm/providers.py`'s `StaticPlanProvider` test double went with them.
   `OperatorConfig.enabled` (the flag that gated the old opt-in) was removed from config
   entirely rather than left as dead config surface — R4 named exactly this kind of drift as a
   root cause. `test_planner.py`, `test_synthesizer.py`, `test_validator.py`,
   `test_recovery_policy.py`, and `test_llm_intent_routing.py` (749 lines, ~30 deferral-guard
   tests for the now-deleted keyword routing) were deleted in full; 9 other tool-specific test
   files lost their 1-4 `build_default_task_plan`-deferral tests each (26 tests, same reasoning
   — testing that a deleted function returns `None` verifies nothing). See item 1 for the
   full verification story (this deletion is the other half of the same change, verified
   together).
4. Merge the 10 prompt roles into 3 agent prompts (§2.1). **Not started.** Item 1 folded the
   Operator's own prompt role into what "the loop" means, but the *other* two agents in §2.1 —
   **Concierge** (absorbs classifier, telegram-gateway, conversation-memory, clarify) and
   **Auditor** (absorbs validator, fulfillment, synthesizer) — are untouched subsystems this
   pass never investigated. Concretely still separate: `llm/classifier.py`
   (`LLMMessageClassifier`), the Telegram intake pipeline (`channels/telegram.py`), conversation
   memory (`channels/memory.py`), and the content-validation gap item 1 explicitly declined to
   port into the Operator loop (see item 1's "deliberately not ported" note) — that gap is
   this item's Auditor half, not a separate TODO. Scoping this properly needs the same
   gap-by-gap verification item 1 got, for two subsystems, not one.
5. Delete `_MAJOR_TASK_KEYWORDS`; pick the model from declared task complexity or plan
   size, not from substrings. **Done (2026-07-27).** Went with a third option, simpler than
   either alternative sketched above: `major_provider` is no longer chosen proactively at
   all (not from keywords, not from a declared complexity/plan-size signal computed before
   ever trying). `plan_task()`'s existing 3-attempt structured-output retry loop now
   escalates to `major_provider` reactively, only inside the `except (ValueError,
   ValidationError)` branch, after an **observed** failure on the current provider
   (`llm/planner.py`). No upfront signal to compute or keep in sync with the tool registry;
   costs exactly one extra attempt, and only on objectives that actually need it. Confirmed
   against the P2 scenario-recording evidence: `_MAJOR_TASK_KEYWORDS` matched none of
   "create a daily schedule that checks X" (no code, no Excel, no "step by step") despite
   that objective needing a worker-level replan in practice (see `schedule.manage` fix
   above) — the keyword list was both over- and under-firing, not a proxy worth keeping in
   any form.
   - Verified with 2 new unit tests in `test_planner.py`
     (`test_planner_escalates_to_major_provider_after_observed_failure`,
     `test_planner_does_not_escalate_when_default_provider_succeeds` — the second deliberately
     reuses old trigger phrases like "step by step" / "excel" / "script" in the objective to
     prove they no longer force escalation) plus the 2 pre-existing planner tests, all green;
     full backend suite (`pytest backend/tests`) still exits 0 with no failures after the
     change.
6. Split `build_tool_registry` (997 lines) — each tool module declares its own
   `TOOL_SPEC`; the registry just collects them. New tool = new file, no registry edit.
   **Done (2026-07-27).** The 16 `_register_*` functions were already well-isolated by
   tool (this was the easy part, done earlier) — the actual gap was that they all lived in
   one file, so a new tool still meant editing `registry.py` (add the function, append to
   `_REGISTRARS`, add its contract imports to the shared block). Moved each one into its own
   adapter's existing module as a `register(deps, definitions, adapters)` function — e.g.
   `code.interpreter`'s registration now lives in `code_interpreter.py` next to
   `CodeInterpreterAdapter` itself, not in a shared file 900 lines away from the code it
   configures. The shared types (`ToolDefinition`, `ToolRegistry`, `RegistryDeps`,
   `capability_enabled()`, `same_output_schema()`) moved to a new `tools/spec.py` with zero
   adapter imports, specifically so adapter modules can import *from* it without `registry.py`
   ever needing to import back from them — avoiding the two-way import a naive split would
   hit. `registry.py` itself dropped from 997 lines to 93: imports each module's `register`,
   lists them in `_REGISTRARS`, and that's the whole file now. New tool = new file with a
   `register()` function + one import line in `_REGISTRARS`, matching the goal exactly.
   Verified three ways: full backend suite green with no test changes needed (every existing
   `from agent_control.tools.registry import ToolDefinition/ToolRegistry/build_tool_registry`
   import kept working unmodified, since those names are still module-level attributes of
   `registry.py`, just re-exported from `spec.py` instead of defined in place); a real running
   `uvicorn` server's `/admin/api/summary` queried directly, confirming all 18 tool
   definitions (16 registrars, 2 of which — `vscode`, `browser` — each contribute 2
   definitions) register with identical names and enabled/disabled state as before the split;
   and `ybm doctor` still passes clean. One incidental fix found along the way:
   `adapter_factory.py` had a *function-scoped* `from agent_control.tools.registry import
   ToolDefinition` (deferred specifically to dodge what would otherwise have been a circular
   top-level import back when `ToolDefinition` lived in `registry.py`) — now that it lives in
   `spec.py`, that workaround is gone; `adapter_factory.py` imports it at module level like
   everything else, and the underlying avoid-the-cycle reason for the workaround no longer
   exists structurally, not just by accident.

**Exit:** zero `_looks_like_*` functions for *routing* — **met** (`default_plans.py` and its
keyword-driven recovery are deleted; the `_looks_like_mcp_output`/`_looks_like_http_output`
pair that remains in `worker.py` classifies a tool *result's shape* for text extraction, not a
routing decision — different thing, same name coincidentally). Zero routing decisions made by
substring match on user text or error text — **met** for execution routing (item 3); the
Operator loop decides every tool call itself. Scenario suite still green — **met** for what
still has fixtures (2/18 — see item 1's disclosed regression; 16/18 need re-recording, not a
scenario-suite health problem). Unit suite green throughout — **met**, verified after every
step in items 1–3, not just at the end.

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

### P4 — One real admin UI *(~4 days)* — **"pick Streamlit and delete the other one" done and verified; console restructure/Access page/Evidence view not started**

**Pick Streamlit and delete the other one.** Streamlit is the honest choice here: it is
local-first, you already have 1,599 working lines, and a React SPA is a project you do not
need. Delete the 2,210-line embedded-HTML admin in `admin.py`, keeping only the JSON API
it exposes. **Done (2026-07-27).** `_ADMIN_HTML` (1,288 lines, running to the literal end
of the file) is gone, along with the two "Legacy admin" / "Legacy FastAPI admin" link
buttons in `admin_streamlit.py` that pointed back to it (and the now-unused
`_legacy_admin_url()` helper) — keeping them would have been circular once Streamlit is the
only real console. `GET /admin` now returns a small pointer page (a dozen lines: title, a
link to the Streamlit app, a note that `ybm start` launches it) instead of a second
1,300-line SPA competing with Streamlit; anyone with the old URL bookmarked gets a page that
tells them where things moved, not a 404. Every route under `/admin/api/*` — what Streamlit
itself actually talks to — is byte-for-byte unchanged.
  - Verified: `test_admin.py`'s HTML-content-specific assertions (which asserted on markup
    that no longer exists — `class="task-card"`, `id="task-trace-modal"`, specific CSS
    rules, etc.) replaced with assertions matching the new pointer page (small, mentions
    Streamlit's port, contains no leftover SPA markup) plus a separate test confirming the
    JSON API is unaffected. `test_admin_streamlit.py` gained a regression test asserting
    neither "Legacy admin" button text nor `_legacy_admin_url` exist anymore (`st.link_button`
    isn't inspectable through Streamlit's `AppTest` structured-widget API, so this checks the
    module source directly), and its existing `AppTest`-based smoke test (which actually runs
    the Streamlit app headlessly against a faked API and asserts `not app.exception`) still
    passes after the column-layout change. Full backend suite green (35/35 in the two admin
    test files, full suite unaffected elsewhere). Also smoke-tested against a real running
    `uvicorn` process (not just `TestClient`): `GET /admin` returns the new pointer HTML,
    `GET /admin/api/summary` still returns 200 — this repo has no browser/screenshot tool
    available in this environment, so visual rendering of the pointer page and of Streamlit
    itself was **not** visually confirmed, only its HTTP response and (for Streamlit) its
    headless `AppTest` render.
  - The "hide Streamlit chrome" defect in the list below turned out to already be handled —
    `scripts/services/run_admin_ui.ps1` already passes `--client.toolbarMode minimal` when
    launching Streamlit. Not new work; confirmed while investigating this item.
  - The other three listed defects (KPI truncation, duplicate health rendering, the `�`
    encoding bug) were investigated and **not found** in `admin_streamlit.py`'s current
    source: `_render_health()` has exactly one call site (no duplication to fix), no
    `st.metric`/KPI-truncation code exists, and a repo-wide search for the classic Windows
    mojibake root cause (`open()`/`.read_text()` without explicit `encoding="utf-8"`, which
    defaults to `cp1252` on Windows) turned up nothing in text-mode file I/O anywhere in
    `backend/src`. Most likely these were observed in the now-deleted embedded-HTML page,
    not Streamlit, and were removed along with it rather than fixed in place — but this
    wasn't visually confirmed either way, so treat as "not reproduced," not "disproven."

Then make it a console rather than a dump:

- **Structure:** four pages — *Now* (live activity), *Tasks* (history + trace), *Access*
  (security), *Settings*. Not one scroll. **Not started.**
- **Fix the observed defects:** stop truncating KPI values; render health once, not twice;
  hide Streamlit chrome (`[client] toolbarMode = "minimal"`); fix the `�` encoding; never
  surface raw DevTools/stack text — show a plain cause with the detail behind a disclosure.
  **Chrome-hiding already done (pre-existing); the other three not reproduced in
  `admin_streamlit.py` — see above. Raw DevTools/stack-text leakage not investigated.**
- **Access page** (this is the "toggles" you asked for): the 9 access groups as the
  primary control, plus what is currently unreachable from any UI — **allowlisted roots**,
  **HTTP host allowlist**, **secret vault** entries (write-only), **per-tool enable**, and
  a **kill switch** that sets every group to Off. **Not started** — this is new feature
  work, not a bug fix, and deserves its own focused pass rather than folding it into a
  cleanup session.
- **Evidence:** for every completed task show *what was actually touched* — files written,
  URLs fetched, commands run, secrets read — sourced from the audit trail you already
  write. This is the "we need to be able to see the result of it" ask, and the data is
  already there. **Not started**, same reasoning as the Access page.

**Exit:** one admin module (**done** — verified above). A new user can grant filesystem
access, run a task, and see exactly what it touched, without reading a log file (**not yet
met** — needs the Access page and Evidence view).

### P5 — Close the security gaps *(~1.5 days)* — **items 1, 2, 5 done and verified; 3 partially done; 4 addressed differently**

1. Add CORS + `Origin` checking + a CSRF token to the admin API. Today any local page can
   flip your capability toggles. **Done (2026-07-27).** No CORSMiddleware exists on the app
   (confirmed — zero middleware registered at all), so Starlette sends no
   `Access-Control-Allow-Origin`, which stops a malicious page's JS from *reading* a
   cross-origin response but not from *sending* a state-changing one in the first place (a
   plain `<form enctype="text/plain">` POST needs no preflight). On the common local,
   token-less, loopback-only setup, `require_admin()`'s only other check was the *server's*
   bind host, never the *caller's* origin — so any website the admin's browser visited could
   trigger mutations against 127.0.0.1 without needing to read the response back. Fixed with
   an `Origin`-vs-`Host` same-origin check in `admin.py`'s `require_admin()` (the single
   function every one of the 21 admin endpoints already calls) — rejects any request whose
   `Origin` header doesn't match `Host`, allows requests with no `Origin` header at all
   (curl, the `ybm` CLI, direct navigation). This is a recognized, stateless CSRF defense
   (OWASP's "Verifying Origin With Standard Headers") — didn't add a separate CSRF token,
   since Origin-checking closes the same hole without needing session/token state on a
   single-user local API. Verified with 2 new tests (cross-origin rejected 403, same-origin
   allowed) plus all 23 pre-existing `test_admin.py` tests still green (they never set an
   `Origin` header, so the new check doesn't touch them).
2. Bind the admin token by default at `ybm setup` rather than leaving it optional. **Already
   true, verified, not this session's work** — `bootstrap.py`'s `run_setup()` already
   unconditionally generates `AGENT_ADMIN_TOKEN` via `secrets.token_urlsafe(32)` regardless
   of bind host, and `ServerConfig.host` already defaults to `127.0.0.1`. Confirmed while
   investigating item 1; no gap found here.
3. Enforce `allowed_imports` in `code.interpreter`, or delete the knob. A config field that
   silently does nothing is worse than no field. **Partially done (2026-07-27); forcing a
   default allowlist deliberately still not done.** The knob itself was never silently
   broken — `_validate_import()` correctly enforces it whenever a caller sets it non-empty.
   Forcing a *non-empty default allowlist* is still not done, and still the right call not to
   rush: it needs a default permissive enough for general-purpose generated scripts (most of
   the stdlib) without becoming the denylist inverted, and getting that curation wrong either
   breaks routine generated scripts or reopens the same gap under a different name.
   What **is** done: while reasoning through the denylist as the interim defense, found and
   closed a real bypass in it. `_validate_python()`'s AST walk blocks `Import`/`ImportFrom`
   nodes and a handful of direct dangerous calls (`eval`, `exec`, `compile`, `__import__`,
   `input`) — but `importlib.import_module("os")` is a plain function call, not an
   `Import`/`ImportFrom` node, so every existing `blocked_imports` entry was invisible to it;
   any blocked module was one `importlib.import_module(...)` away from working anyway. Fixed
   by adding `importlib` itself to the default `blocked_imports` list — blocking the module
   blocks every form of reaching it (`import importlib`, `from importlib import
   import_module`, aliased), which is simpler and more complete than trying to special-case
   the one call-syntax pattern. Also added `multiprocessing` (process spawning — the same
   risk class as `subprocess`, a different stdlib door to it) and `winreg` (Windows registry
   read/write, high-impact and Windows-specific, and this is a Windows-first tool) to the
   default list, since both were missing entirely. Verified with 4 new parametrized tests
   (`import importlib; importlib.import_module(...)`, `from importlib import import_module`,
   `import multiprocessing`, `import winreg` — all rejected by default) plus the full
   existing `test_code_interpreter.py` suite (25 tests, all still pass — none of the tests
   that override `blocked_imports` explicitly are affected by a default-list change). This is
   still explicitly best-effort static analysis, not a sandbox — a sufficiently determined
   gadget chain (`getattr`/`vars` tricks) isn't blocked and isn't the goal; Docker (item 4
   below) is the real boundary, this just closes the bypass an LLM is actually likely to
   reach for, not every one a deliberate adversary could construct.
4. Default generated code to the Docker backend; local Python only for trusted paths.
   **Addressed via a different, less disruptive mechanism achieving the same safety
   property**, not by flipping this default. Investigating this item surfaced a sharper bug
   than "the default backend isn't Docker": `_approval_required_for_run_python()` in
   `code_interpreter.py` explicitly exempted *all* LLM-generated code
   (`generate_and_run`/`solve_once`/`build_temp_helper`/`repair_script`) from approval,
   unconditionally — combined with `fallback_to_local_when_backend_unavailable: True`
   (default) silently dropping to unsandboxed `local_subprocess` whenever Docker isn't
   running (confirmed true on this machine, in every scenario-test recording this session:
   "Docker disabled or not running"), the net effect was arbitrary LLM-generated code
   running immediately, unsandboxed, with zero human review, gated only by the import
   denylist. Flipping `fallback_to_local_when_backend_unavailable` to `False` outright was
   considered and rejected — it would make `code.interpreter` simply stop working on any
   machine without Docker configured, which is most of them, including this one. Fixed
   instead by moving backend selection before the approval check and gating specifically on
   the *silent, unintentional* fallback case: `_approval_required_for_run_python()` now
   requires approval for generated code when `backend_fallback_warning is not None` (Docker
   was wanted, wasn't available, and the run silently downgraded) while leaving execution
   automatic when Docker succeeds *or* when an admin has explicitly configured
   `untrusted_default_backend: local_subprocess` themselves (no fallback occurred, that's an
   informed choice already made via config, not re-prompted per call). `ApprovalRequired`'s
   message now includes the fallback reason so an approval prompt is self-explanatory.
   Verified with 2 new unit tests (approval required on fallback; still succeeds once
   `approved: true`) plus a new scenario test
   (`test_code_interpreter_default_settings_need_approval_without_docker.py`) proving the
   gate holds through the *entire* real worker/planner/policy/registry/executor stack under
   genuinely default settings, not just at the adapter unit level. Updated 5 existing
   scenario tests that exercise `generate_and_run` under this repo's real (Docker-less)
   environment to explicitly set `require_approval_for_untrusted_run_python: false` with a
   comment explaining why — those tests are about execution correctness, which now has its
   own dedicated approval-gate coverage instead. Full backend suite green throughout
   (33 scenario tests, all unit tests) — verified before and after.
5. Approval requests carry a **diff/preview** (files to be changed, command to be run) —
   approving blind is not approval. **Done for the message the user actually sees
   (2026-07-27); the formal decision UI (a real approve/reject control anywhere other than
   Telegram) is a separate, larger gap also found and documented here, not fixed.**
   Investigating this while thinking through item 4's new approval gate surfaced something
   worse than "no preview": `channels/telegram_notifications.py`'s `AWAITING_APPROVAL`
   message was 100% generic ("I need approval before I can continue... otherwise approve it
   from the admin UI") — and a repo-wide search for `InlineKeyboard`/`inline_keyboard`/
   `reply_markup` turned up **zero matches** in production source. `channels/telegram.py`
   parses `approval:{id}:{decision}` callback data if one ever arrives, but nothing anywhere
   sends a message with the button that would produce one — that half of the flow is dead
   code. The admin API (`admin.py`) only exposes approvals read-only
   (`repositories.approvals.list_for_task` inside the task-trace view) with no
   decide/approve endpoint at all. So the message was telling users to do something
   impossible in the one place it named, while never mentioning the one thing that actually
   works: replying `approve` in the same Telegram chat
   (`channels/telegram.py`'s `_approve_latest_pending()`, a working plain-text command).
   Fixed the part that's fixable without new UI surface: `_create_step_approval()`'s caller
   (`worker.py`'s `_process_planned()`) now builds a human-readable preview line per
   approval-required step (title, risk level, tool name, `tool_input`) and writes it to
   `task.metadata["pending_approval_preview"]`; `_user_facing_task_message()`'s
   `AWAITING_APPROVAL` branch now shows that preview and tells the user the resume path that
   actually works instead of the one that doesn't. Verified with a new assertion on the
   existing `test_worker_resumes_after_step_approval` (checks the preview contains the step
   title/tool/risk) plus 2 new tests in `test_telegram_notifications.py` (preview shown and
   correct guidance text when present; correct guidance text even with no preview). Full
   backend suite green (all unit tests, unaffected elsewhere).
   - **Not fixed, and out of scope for this pass:** there is still no actual
     approve/reject *control* outside Telegram's plain-text command and the (currently
     unreachable) inline-button path — building one (in the admin API and/or Streamlit)
     is real new feature work, the same category as the Access page and Evidence view
     below, not a message-text fix. Also not investigated: whether wiring up an actual
     inline-keyboard *send* (closing the dead-code half of the callback flow) is easy
     given the parsing already exists, or whether it was abandoned for a reason not visible
     from the code alone.
   - **Separately confirmed safe, not a related gap:** the *new* code.interpreter
     sandbox-approval gate from item 4 above does **not** go through this
     `_create_step_approval()`/`ApprovalRequest` path at all — it's a `NEEDS_APPROVAL`
     result raised mid-execution, one layer below where `ToolExecutor`'s own policy-level
     approval creation lives. Traced this empirically (not just by reading code) because it
     looked at first like a possible dead end: `failure_diagnosis.py` classifies
     `error_class == POLICY_DENIED` as `FailureType.UNSAFE_ACTION` (a structured match on
     the error class, not fuzzy keyword-guessing) and routes it to the existing ask-user/
     clarify flow instead, which does have a working resume path (the user replies in
     chat). Confirmed against `test_code_interpreter_default_settings_need_approval_without_docker.py`'s
     scenario: final status is `CLARIFYING`, not a stuck `AWAITING_APPROVAL`.

**Exit:** `ybm doctor --security` passes (not yet re-checked against these two fixes — `ybm
doctor` doesn't currently have a `--security` mode at all, tracked separately). A hostile
local web page cannot change your capability config (verified for the admin API's own
mutation endpoints via item 1's Origin check).

### P6 — Operational hygiene *(~1 day)* — **all items done and verified**

1. **Startup reconciliation:** on worker boot, any task left `running`/`interpreting` by a
   dead worker is failed or requeued explicitly — never silently resumed. **Done
   (2026-07-27).** `claim_next()` already has a claim-expiry mechanism (a crashed worker's
   claim eventually times out and becomes reclaimable), but that's a *wait-up-to-N-minutes*
   fix, not an *on-restart* one — a task left `RUNNING`/`INTERPRETING` sits stuck (or, once
   the stale claim expires, gets silently re-claimed and re-run from a status that assumes
   in-flight state which no longer exists — re-running from the top could duplicate side
   effects like a second Telegram send or file write, and there's no checkpoint to resume
   from mid-flight). Added `reconcile_orphaned_tasks()` in `orchestration/worker.py`, called
   once in `cli.py`'s `run_worker()` before the polling loop starts: finds every task still
   `RUNNING`/`INTERPRETING`, explicitly fails it with an audit trail
   (`last_worker_error` + `AuditEventType.ERROR` + `TASK_STATE_CHANGED`), and releases its
   claim. Verified with 3 new unit tests (fails the right statuses and leaves everything
   else alone, releases the claim and writes the audit trail, no-op when there's nothing to
   reconcile) plus a live import check that `cli.py` still wires up cleanly.
2. **Retention:** default 30-day audit/task retention, `ybm db clean` to run it. **Already
   done** (P0 work, this session's earlier `db_tools.py`) — `db_clean(days)` defaults to 30
   and cascade-deletes a task's full FK graph. No gap found; confirmed while surveying P6,
   not new work.
3. **Schedule hygiene:** schedules expire, and a schedule whose target has failed N times
   consecutively disables itself and notifies. The 7 dead May schedules found today would
   never have survived this rule. **Done (2026-07-27).** Added
   `SchedulerConfig.max_consecutive_failures` (default 5) and
   `ScheduleRepository.update_metadata()`. `scheduler.py`'s `run_scheduler_once()` now
   checks the outcome of a schedule's *previous* spawned task (via `schedule.last_task_id`)
   before spawning its next one: a `FAILED` previous run increments
   `schedule.metadata["consecutive_failures"]`; a `COMPLETED`/`BLOCKED`/`CANCELLED` one
   resets the streak to 0; hitting the configured threshold auto-transitions the schedule to
   `ScheduleStatus.PAUSED` and writes an `AuditEventType.ERROR` event instead of spawning
   another failing run. Verified with 3 new tests (streak increments but keeps running below
   threshold, auto-pauses and stops spawning at threshold with the audit event present,
   streak resets to 0 after a success).
4. Move `agent_control.db` under `.agent_control/` so runtime state lives in exactly one
   place. **Done (2026-07-27).** `StorageConfig.database_url` now defaults to
   `sqlite:///.agent_control/agent_control.db`, matching where every other piece of runtime
   state already lived (artifacts, the secret vault, workspaces). The migration risk flagged
   when this was first deferred — orphaning an existing repo-root `agent_control.db`, making
   task history appear to silently vanish — is closed with an explicit, tested migration:
   `Database.__init__` now calls `_migrate_legacy_database_file()`, which moves a file at the
   old default location into the new one, but *only* when the constructed path is exactly
   today's default (never a caller-customized `database_url`) and *only* when nothing already
   sits at the destination (never overwrites). Verified two ways: 5 new unit tests in
   `test_database.py` (migrates and preserves content byte-for-byte; no-ops with no legacy
   file; never overwrites an existing destination file; never touches a customized
   `database_url`; the migrated file is immediately queryable, not just moved-and-forgotten),
   and against the real `agent_control.db` this repo had accumulated during this session's
   own `ybm doctor`/`ybm setup` runs — recorded its MD5 before, ran `ybm doctor`, confirmed
   the file moved to `.agent_control/agent_control.db` with an *identical* MD5, the old path
   gone, and a second `ybm doctor` run printed no migration message (confirmed idempotent).

**Exit:** starting the stack after a month idle does not resurrect month-old work (items 1,
3, and 4 verified; item 2 pre-existing).

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
