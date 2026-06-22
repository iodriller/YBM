# Project gap analysis

Snapshot date: end of FIX_PLAN P1–P6 execution. 267/267 unit tests pass.
Latest substantial e2e baseline: `run_20260525_125109` — 23/47 = 49% pre-fixes;
predicted ~83% after P1–P6.

Methodology: every claim below is grounded in an observed file, line, grep,
or test output (see citations). Inferences and unknowns are flagged.

## Codebase shape

- Total Python under `backend/src/agent_control/`: **21,711 lines** across the
  module tree.
- Total unit tests: **267** functions across **35** test files.
- Largest single modules:
  - `orchestration/default_plans.py` — **3,424 lines** (98 functions)
  - `admin.py` — **2,199 lines** (FastAPI admin router)
  - `admin_streamlit.py` — **1,598 lines** (parallel Streamlit admin UI)
  - `tools/browser.py` — 958 lines
  - `channels/telegram.py` — 917 lines
  - `orchestration/worker.py` — 903 lines

---

## Gaps, ranked by severity ÷ remediation cost

### CRITICAL ── Worker concurrency has no atomic claim; duplicate workers race

**Evidence.** `repositories.list_by_statuses` ([repositories.py:231-240](backend/src/agent_control/storage/repositories.py#L231))
is a plain `SELECT * FROM tasks WHERE status IN (...) LIMIT ?`. No `FOR UPDATE`,
no claim-by-UPDATE, no row-level locking. SQLite is opened with `foreign_keys = ON`
but NOT in WAL mode. Two worker processes (or one supervised + one manual, which
is what bit us during e2e runs) can both read the same task and both think they
own it. This is the root cause of the "expected_artifact_delivered_missing"
cluster from P3 — two workers raced, neither reached step 2.

**Impact.** Whenever `start_stack.ps1`'s supervised worker is alive at the
same time as a manually-started one, every multi-step task is at risk of
being abandoned mid-plan. The current operational fix (`stop_stack.ps1`
between sessions) is process discipline, not a code guarantee.

**Holistic remediation.** Change `process_next` to atomically claim a task:

```sql
UPDATE tasks
SET status = 'claimed_by_worker_<uuid>', updated_at = ?
WHERE id = (SELECT id FROM tasks WHERE status IN (...) ORDER BY created_at ASC LIMIT 1)
RETURNING *;
```

SQLite supports `RETURNING` since 3.35. The worker then operates on the claimed
task with confidence no other worker has it. Enable WAL mode for better
concurrent-reader throughput. Cost: ~50 lines, one schema migration if you want
a dedicated `claimed_by` column. Single highest-leverage fix in the codebase.

---

### HIGH ── 3,424-line `default_plans.py` is mostly dead code

**Evidence.** [default_plans.py:24-44](backend/src/agent_control/orchestration/default_plans.py#L24)
declares `build_default_task_plan` and documents: *"Fallback plan factory —
only handles explicit system commands… handles status requests and nothing
else; all other task routing belongs to the LLM planner."*

Yet the file has **98 functions** including `_build_browser_plan`,
`_build_filesystem_manage_plan`, `_build_document_manage_plan`, etc. Five are
provably unreachable (zero callers, internal or external):
`_build_intent_plan`, `build_default_vscode_development_plan`,
`_build_browser_plan`, `_build_coding_agent_plan`, `_build_schedule_plan`.

The functions reachable through `build_evaluator_recovery_plan` are the real
load-bearing pieces — but they're mixed in with the dead ones. Hard to tell
what's safe to delete without tracing.

**Impact.** When you change a tool contract (say, `artifact.deliver` operation
name), this file silently rots and you find out when an unused recovery branch
suddenly fires. The dead `_build_browser_plan` etc. are the strongest
*temptation* to revert to deterministic plans whenever the LLM struggles —
violating the architecture's stated principle that the LLM is the only planner.

**Holistic remediation.** Two passes:

1. Delete the 5 provably-dead helpers and any `_build_*` functions that are
   only called from those.
2. Move the legitimately-used helpers (the ones reachable from
   `build_evaluator_recovery_plan`) into a `recovery_plans.py` module. Rename
   `default_plans.py` → `system_plans.py` containing only `_build_status_plan`
   and its direct dependencies (~150 lines max).

Cost: a careful afternoon of grep + delete + re-test. Brings the module from
3,424 lines to ~200 + ~400, in two clearly-purposed files.

---

### HIGH ── 481-line `build_tool_registry` function

**Evidence.** [registry.py:214-694](backend/src/agent_control/tools/registry.py#L214) —
a single function declaring 16 tools, each block 25–40 lines, with significant
boilerplate duplication around `operation_schemas`, `operation_output_schemas`,
`_same_output_schema`, and the `if … enabled:` gating around each
`adapters[...] = ...` assignment.

**Impact.** Adding a new tool touches one giant function. The orphan
`desktop.screenshot` bug existed for some time because nobody read the function
end-to-end. Bug surface scales with function length.

**Holistic remediation.** Convert each tool to a small `register_<tool>(settings,
deps) -> tuple[ToolDefinition, Adapter | None]` helper (one per file or one per
function), then `build_tool_registry` becomes a list-comprehension over all
registrations. Cost: ~1 day of mechanical refactor. Adds ~300 lines of
function headers but reduces per-tool cognitive load dramatically. Each new
adapter is then a single contained PR.

---

### HIGH ── Two parallel admin UIs (`admin.py` + `admin_streamlit.py`) ─ 3,797 lines combined

**Evidence.** `admin.py` (2,199 lines) is the FastAPI admin router served from
`main.py:26`. `admin_streamlit.py` (1,598 lines) is a separate Streamlit admin
launched by `scripts/run_admin_ui.ps1`. Both consume the same admin API
endpoints. Tests exist for both.

**Impact.** Every admin feature has to be implemented twice or one of them
gets stale. The Streamlit one I've seen actively used; the FastAPI one
duplicates the same surface. Maintenance cost is 2× for a feature that's
internal-only.

**Holistic remediation.** Decide which is canonical. The FastAPI router
serves an HTML UI inline (via `HTMLResponse`), so it's self-contained.
Streamlit needs a separate `streamlit run` process. If you've been using
Streamlit, archive the FastAPI HTML view (keep the JSON API endpoints — those
are used by both UIs and by the e2e runner). Saves ~1,500 lines and a moving
piece. Cost: half a day to confirm no consumer is hitting the FastAPI HTML
templates other than browsers.

---

### MEDIUM ── `code.interpreter` accepts an `allowed_imports` config knob that's never enforced

**Evidence.** [code_interpreter.py:258-269](backend/src/agent_control/tools/code_interpreter.py#L258):
`_validate_python` only walks the AST for `blocked_imports` and a handful of
banned builtins. The `allowed_imports` argument is in the signature but never
read. The config default is `[]` so today nobody notices, but if a user sets
`code_interpreter.allowed_imports = ["pandas"]` expecting it to be a whitelist,
nothing happens.

**Impact.** Silently dishonored configuration. Real attacker risk is low
(this runs local user code), but anyone using the knob defensively gets a
false sense of security.

**Holistic remediation.** Either (a) honor the whitelist when non-empty, or
(b) delete the field from `CodeInterpreterAdapterConfig` and `_validate_python`'s
parameter list. (b) is cheaper and matches actual behavior. Cost: 10 lines.

---

### MEDIUM ── No tests for three core LLM-driven modules

**Evidence.** Modules with NO dedicated test file:

- `llm/synthesizer.py` (51 lines) — runs after every content-tool step
- `llm/validator.py` (54 lines) — pre-synthesis raw-output check
- `channels/responder.py` (84 lines) — chat-only path responder
- `orchestration/executor.py` (187 lines) — actually executes every tool call
- `tools/registry.py` (722 lines) — builds the entire tool catalog
- `tools/contracts.py` (672 lines) — every Pydantic input schema (some are
  exercised indirectly through planner tests)
- `tools/adapter_factory.py` — runtime adapter scaffolding

The first three are LLM-call sites I added during the fix passes. They are
load-bearing in the new validate-then-synthesize flow but have zero
unit-level coverage.

**Impact.** When I changed `validator.py` to switch from "validate answer" to
"validate raw output before synthesis", no test caught the breaking
signature change — only the live runs. Each LLM call site should have its
own test fixture with a stub provider.

**Holistic remediation.** Add `test_validator.py`, `test_synthesizer.py`,
`test_executor.py`, `test_responder.py`. ~30 lines each, stub providers
already exist in `tests/test_classifier.py`. Cost: half a day.

---

### MEDIUM ── 38 broad `except Exception` blocks that swallow context

**Evidence.** Ran an AST-grade scan. 38 broad-except blocks where the next
real statement is `pass`, `continue`, `return None`, or a swallowing return.
Highlights:

- [worker.py:562](backend/src/agent_control/orchestration/worker.py#L562) — silent except returning None
- [providers.py:193](backend/src/agent_control/llm/providers.py#L193) — silent False
- [schemas.py:576](backend/src/agent_control/schemas.py#L576) — silent continue in a list comprehension
- [synthesizer.py:46](backend/src/agent_control/llm/synthesizer.py#L46) — silent None on provider failure
- [validator.py:48](backend/src/agent_control/llm/validator.py#L48) — silent True (intentional — to avoid blocking completion)

**Impact.** A real provider hiccup looks identical to "no relevant output";
debugging becomes audit-log archaeology. Notably the project doesn't use
Python's `logging` at all (verified — 0 `import logging`, 0 `getLogger`
calls). Everything goes through the audit table or stdout `print`. The
audit table is *good* but doesn't capture all exceptions.

**Holistic remediation.** Introduce a single `logger = getLogger(__name__)`
per module, log every broad-except at warning level with `exc_info=True`,
keep the silent return so behavior doesn't change. Cost: 1 hour for find+replace
+ a one-line `logging.basicConfig` in `cli.py`.

---

### MEDIUM ── 141 config fields, several knobs orphaned

**Evidence.** `load_settings()` exposes 141 fields across `server`,
`identity`, `channels`, `llm`, `capabilities`, `approval_policy`, `storage`,
`scheduler`, `logging`, `limits`, `adapters`. Spot-checked orphans (config
present, code doesn't honor):

- `adapters.code_interpreter.allowed_imports` — never enforced (see above)
- `adapters.computer_use.allowed_apps` — set in `config.yaml` but I didn't
  grep a single consumer. (Unverified — may exist; flag as "needs audit".)
- `logging.*` config block exists, but no code uses Python `logging`.

**Impact.** Configuration drift. Users set knobs expecting them to do
something; nothing happens.

**Holistic remediation.** Audit each adapter config field: prove a consumer
exists or delete the field. Cost: 2-3 hours.

---

### MEDIUM ── No row-level locking on `plans`, `audit_events`, `artifacts`

**Evidence.** Concurrency audit found the worker-claim issue, but the same
pattern applies elsewhere: every repository method opens a fresh connection,
commits, and closes. No transactions span multiple operations.

`PlanRepository.create` ([repositories.py:361-376](backend/src/agent_control/storage/repositories.py#L361))
is a single INSERT — fine in isolation. But if two workers were racing on the
same task (the CRITICAL above), both could try to insert plans concurrently,
hit the UNIQUE constraint on `plans.id`, and one would fail. That's the
`UNIQUE constraint failed: plans.id` we saw earlier — I fixed it by
overwriting `plan.id` at persist time, but the structural issue (no
serialization of writes to the same task) remains.

**Impact.** Subtler than the claim race — usually invisible — but the
symptoms include audit events out of order and the occasional UNIQUE
constraint surprise.

**Holistic remediation.** Wrap multi-step worker operations in a single
`with self.database.connect() as conn` block so the entire process_task
runs in one transaction. Or accept that SQLite is fundamentally serial-write
and move to Postgres if you ever go multi-process. Cost: depends on path
chosen.

---

### LOW ── 0 `import logging` / 0 `getLogger` across the source tree

**Evidence.** Grep across `backend/src` returns zero. Observability is 100%
via the audit table or stdout `print()` (4 occurrences). No structured logs.

**Impact.** Can't easily filter logs by level / module / task without
custom SQL on the audit table. Audit is comprehensive but not designed for
debug-time grepping.

**Holistic remediation.** Standard `logging.basicConfig(level=INFO,
format=...)` in CLI entry points. Module-level `logger = getLogger(__name__)`.
Doesn't change behavior; gives you a free debug knob (`AGENT_LOG_LEVEL=DEBUG`).

---

### LOW ── `desktop.screenshot` Capability + intent route alias survives even though tool is gone

**Evidence.** After Priority 1 (orphan tool removed), four references to
`Capability.DESKTOP_SCREENSHOT` remain:

- `channels/responder.py:63` — policy gate for the legacy `/screenshot` command
- `channels/telegram.py:708` — same legacy command path
- `policy/access_modes.py:57` — read-capability bucket
- `tools/registry.py:603` — leftover comment

And the intent-route alias `"desktop.screenshot" → IntentRoute.DESKTOP_OBSERVE`
in `schemas.py:324` still exists. These are correct (the capability is a
distinct concept from the tool), but easy to confuse with the deleted tool.

**Impact.** Low — current behavior is correct. But future readers will see
"desktop.screenshot" in different layers and wonder what's connected to what.

**Holistic remediation.** Either rename the capability to
`DESKTOP_OBSERVE_SCREENSHOT` so it's obvious the *capability* isn't the *tool*,
or leave it and add a short comment in `schemas.py:Capability` explaining the
distinction. Cost: trivial.

---

### LOW ── Pydantic deprecation warnings on `model_fields` instance access

**Evidence.** Running any code that does `model.model_fields` warns:
`PydanticDeprecatedSince211: Accessing the 'model_fields' attribute on the
instance is deprecated.` Will become an error in Pydantic V3.

**Impact.** Will break on Pydantic upgrade. Currently noisy in CLI scripts.

**Holistic remediation.** Replace `instance.model_fields` with `type(instance).model_fields`
or `MyModel.model_fields`. Probably a handful of sites. Cost: 30 minutes.

---

## What's NOT a gap (deliberately called out)

These show up in casual scans but are actually fine:

- **Async-blocking `subprocess.Popen` calls** — present in `browser.py`,
  `computer_use.py`, `local_workspace.py`. They're for spawning long-running
  things (Chrome, an app, a static server). All are fire-and-forget; none
  block the asyncio loop on `wait()`. The actual cancellable subprocess —
  `code_interpreter`'s Python script execution — uses
  `asyncio.create_subprocess_exec` with proper await. Verified
  [code_interpreter.py:445](backend/src/agent_control/tools/code_interpreter.py#L445).

- **0 TODO/FIXME comments** — actually a positive signal (the project is
  disciplined about not leaving noise) but worth noting so we don't read it
  as "code is finished".

- **57 `except Exception` blocks total** — most ARE doing something useful
  (wrapping the exception into a `ToolCallResult.error_message`, etc.). The
  38 silent ones above are the actual concern; the other 19 are appropriate.

---

## Priority ranking for follow-up

If I were going to invest one focused week post-FIX_PLAN, I'd do:

1. **Atomic worker claim** (CRITICAL) — eliminates an entire failure class
2. **Default_plans purge** (HIGH) — reduces cognitive surface by ~3,000 lines
3. **Single admin UI** (HIGH) — reduces cognitive surface by ~1,500 lines
4. **Tests for validator / synthesizer / executor** (MEDIUM) — covers the
   load-bearing LLM call sites I added
5. **Honor or delete `allowed_imports`** (MEDIUM) — closes a misleading knob
6. Everything else as time permits.

Total of the top 5 is roughly 3 days of work; the codebase would be ~5,000
lines smaller and structurally tighter at the end.

## Honest unknowns

- I didn't audit the `policy/` module for capability-gate correctness.
- I didn't trace what `adapters.computer_use.allowed_apps` actually does;
  flagged as "needs audit" above.
- I didn't measure the audit table's growth rate or test `cleanup_period_days`
  behavior — the table has thousands of events already.
- I don't know how the `recovery_plan_factory` interacts with the LLM planner
  on complex multi-step tasks beyond what the FIX_PLAN already documents.
