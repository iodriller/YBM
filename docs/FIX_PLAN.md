# Fix plan — from 49% pass rate to ~80%

Anchored to the failure analysis from `.agent_control/e2e_results/run_20260525_125109`
(47 stages, 23 pass / 24 fail = 49%).

Goal: knock out the 15 code-bug failures (63% of remaining failures) with low-risk,
well-scoped changes. The remaining 9 cases break down as 6 LLM-capacity, 2
operational, 1 correct-behavior. Those are *not* in scope for this plan unless
explicitly flagged.

## Order of execution

The priorities are ordered by (impact ÷ risk). Earlier priorities should land
first because they have larger blast radius reduction and lower regression risk.
Each priority is independent — if one of them runs into trouble, the others
can still ship.

| # | Title | Cases recovered | Risk | Effort |
|---|---|---|---|---|
| 1 | Remove the orphan `desktop.screenshot` tool from the registry | 7 | low | ~5 min |
| 2 | Stop the fulfillment validator from inferring postconditions from objective keywords when the plan already has steps | 3 | low–medium | ~15 min |
| 3 | Make `artifact.deliver send_latest` find the freshly-registered code-interpreter artifact | 3 | medium | ~25 min |
| 4 | Tolerate tool-name-as-capability aliases on `ApprovalGate.capability` (same alias pattern we already added to `PlanStep.required_capabilities`) | 1 | low | ~5 min |
| 5 | Auto-rename or accept-overwrite for `filesystem.manage.write_text_file` when the destination exists | 1 | low | ~10 min |
| 6 | Tighten `code.interpreter` sandbox so generated scripts can't write outside their workspace (or fail more clearly) | 1 | low | ~10 min |

Operational and LLM-capacity items are listed at the end as "follow-ups" — they
are tracked but not executed in this pass.

---

## Priority 1 — Remove the orphan `desktop.screenshot` tool from the registry

### Why this exists (root cause)

`backend/src/agent_control/tools/registry.py:597-608` declares a `ToolDefinition`
for `desktop.screenshot` with `enabled=True`, but no `adapters["desktop.screenshot"] = …`
follows it anywhere in the file. The tool is advertised to the planner via
`registry.context()` (with a worked example, `{"operation": "capture"}`), so the
planner happily picks it. At execution time the executor looks it up in
`adapters[...]`, doesn't find it, and reports `tool adapter not registered:
desktop.screenshot`. Replan storms 3× then fails the task.

This pattern killed 7 of the 24 failures:

- `desktop_inspection`, `screenshot_capture`, `voice_message_intake`,
  `chat_history_desktop_followup` — direct desktop observations
- `browser_open_navigation`, `browser_form_fill` — the planner inserted a
  `desktop.screenshot` step for a "screenshot" sub-action
- `vscode_file_tree_visibility` — same, the user asked for a screenshot of VS Code

The actual screenshot work is already covered by:
- `desktop.observe` / `computer.use observe` — captures + includes a screenshot
- `artifact.deliver send_screenshot` — delivers the latest screenshot via Telegram
- The legacy `/screenshot` command path in `telegram.py` (a completely different
  surface, not a worker tool)

So `desktop.screenshot` as a worker tool is redundant *and* broken.

### The change

In `backend/src/agent_control/tools/registry.py`, delete the entire `definitions.append(ToolDefinition(name="desktop.screenshot", …))` block (lines 597-608).

That is the only change. Everything else stays.

### What this could break (regression analysis)

I grepped all references to the string `desktop.screenshot` in the codebase
before proposing this:

| Reference | What it is | Affected? |
|---|---|---|
| `Capability.DESKTOP_SCREENSHOT` enum in `schemas.py` | Capability name, different concept | **No** — still in use by `computer.use` and the legacy command path |
| `intent.route="desktop.observe"` alias `"desktop.screenshot"` in `schemas.py` | Old alias for the *route* enum | **No** — routes are unaffected by tool removal |
| `intent.operation="screenshot"` mapping in `schemas.py` | Operation-name alias | **No** — operations are not tool names |
| `telegram.py` `/screenshot` command handler | Legacy direct-command path | **No** — not a worker tool |
| `cli.py:170` `screenshot_enabled` gating | Settings flag | **No** — gates a different code path |
| `observation/screenshot.py` | The actual screenshot service | **No** — invoked by `computer.use` and the command path |
| `fulfillment.py:163` `if step.tool_name == "desktop.screenshot"` | Postcondition inference | Becomes dead code — harmless, but worth noting |
| `registry.py:597-608` | The orphan tool definition | **Delete this** |

No code reads `adapters["desktop.screenshot"]` other than the executor doing
runtime lookup. No tests pin the existence of this tool by name (verified by
grepping `backend/tests/`). Safe to delete.

### Verification

After the edit:
1. `python -m pytest backend/tests/` — should be 267/267 still.
2. `python -X utf8 -c "import sys; sys.path.insert(0,'backend/src'); from agent_control.tools.registry import build_tool_registry; from agent_control.config import load_settings; r=build_tool_registry(load_settings(),backend_base_url='http://127.0.0.1:8765'); print([d.name for d in r.definitions])"` — confirm `desktop.screenshot` is not in the list.
3. Re-probe the live classifier with "Tell me what is on my desktop right now." and verify the planner picks `computer.use` / `desktop.observe`, not `desktop.screenshot`.
4. The next e2e run should show all 7 bucket-A cases either pass or fail for a different (downstream) reason.

### Estimated impact

+7 passes → 30 / 47 = **64%** (assuming nothing else regresses).

---

## Priority 2 — Don't infer postconditions from objective keywords when the plan already has steps

### Why this exists (root cause)

`backend/src/agent_control/orchestration/fulfillment.py:46-54`:

```python
def expected_postconditions(task, plan):
    if plan and plan.postconditions:
        return tuple(plan.postconditions)
    inferred = []
    if plan is not None:
        inferred.extend(_postconditions_from_plan(plan))      # ← (1) infer from steps
    inferred.extend(_postconditions_from_objective(task.objective))  # ← (2) AND from objective text
    return _dedupe(inferred)
```

`_postconditions_from_objective` is too eager. It looks at the objective text and
adds `WORKSPACE_DIR` when it sees the words `create/build/write/make` + the words
`file/files/code/script/app/website` (lines 185-208). That fires on:

- `organize_by_filename` → "create sensible categories" + "files" → demands WORKSPACE_DIR. But the plan is filesystem.manage + filesystem.manage + artifact.deliver. No workspace step. → blocked.
- `intent_based_tool_routing` → "implement a small example" + "files" → same.
- `scheduled_jobs` → "create a job" + … → same.

In every case, the plan from `_postconditions_from_plan` correctly inferred the
right postconditions (or none, because no step actually creates a workspace).
The objective-keyword inference is *adding* a phantom requirement on top.

### The change

In `backend/src/agent_control/orchestration/fulfillment.py`, change
`expected_postconditions` so that:

- If the plan has explicit `plan.postconditions` → use those (unchanged).
- Else if the plan has any tool steps that produced postconditions → use ONLY plan-derived postconditions; do NOT also run objective inference.
- Only when both fail to produce postconditions → fall back to objective inference (this preserves behavior for plans that have no tool steps at all, e.g. before the planner has run).

Pseudocode for the rewrite:

```python
def expected_postconditions(task, plan):
    if plan and plan.postconditions:
        return tuple(plan.postconditions)
    plan_inferred = _postconditions_from_plan(plan) if plan is not None else []
    if plan_inferred:
        return tuple(_dedupe(plan_inferred))
    # No plan-derived postconditions — fall back to objective keywords.
    return tuple(_dedupe(_postconditions_from_objective(task.objective)))
```

### What this could break (regression analysis)

The risk: some case that previously **passed** because objective keywords
correctly anticipated a postcondition (e.g. a "create an app" request where the
plan's workspace.manage step would also infer WORKSPACE_DIR — same postcondition,
so dedupe absorbed it).

For cases where the plan's tool steps DO produce postconditions:
- workspace.manage step → adds WORKSPACE_DIR via `_postconditions_from_plan`
- adapter.factory step → adds ADAPTER_PROPOSAL via `_postconditions_from_plan`
- artifact.deliver step → adds ARTIFACT_DELIVERED via `_postconditions_from_plan`
- desktop.screenshot step → adds DESKTOP_OBSERVATION via `_postconditions_from_plan` (after Priority 1 this branch is dead, harmless)

So for any case where the plan correctly uses the right tool, we already have
the right postcondition from the plan. The objective inference is purely
additive *and* drift-prone.

For cases where the plan has no tool steps (no LLM planner output, or pre-plan
state) → objective inference still runs. This preserves the safety net.

### Verification

1. `python -m pytest backend/tests/` — should be 267/267.
2. Verify the 23 currently-passing cases still pass by re-running them: `python scripts/run_all_e2e_tests.py --only folder_open_inspection,send_found_pdf,browser_dizibox_new_shows`. None of those rely on objective-keyword-inferred postconditions, so they should remain green.
3. The three blocked cases in this bucket (`organize_by_filename`, `intent_based_tool_routing`, `scheduled_jobs`) should now reach `completed` if the plan execution itself is sound.

### Estimated impact

+3 passes → 33 / 47 = **70%**.

---

## Priority 3 — RECLASSIFIED — was operational (duplicate workers race), not a code bug

After P1+P2 landed, diagnosis of `code_interpreter_excel_generate_and_load` audit
log revealed that **two workers and two pollers were running simultaneously**
(one set from `start_stack.ps1`'s supervised processes, one set started manually).
Both workers were grabbing the same task in `WORKABLE_STATUSES` and racing:

- Worker A: generated plan A, ran step 1 (code.interpreter), set current=step2
- Worker B: concurrently generated plan B with different step IDs, set
  current=step1B before Worker A could process step 2A
- Plan A's step 2 (artifact.deliver) never ran
- Worker A eventually saw current_step_id pointing at a step from plan B that
  doesn't exist in plan A, hit `_next_runnable_step → None`, treated the task
  as complete, and `_transition(COMPLETED)` triggered fulfillment validation,
  which correctly reported `expected_artifact_delivered_missing` because no
  artifact had been delivered.

The pattern repeated across all 3 cases in this bucket.

### The actual fix (applied)

Ran `scripts/stop_stack.ps1` followed by a clean single-process restart of
LocalDeploy + admin API + one `poll-telegram` + one `run-worker`. Sanity test:

```
python scripts/run_all_e2e_tests.py --only code_interpreter_excel_generate_and_load
```

Result: **PASS in 77.8s**, artifact.deliver step ran and delivered correctly.

### No code change required

`artifact.deliver`'s `send_latest` lookup, the artifact registration from
`code.interpreter`, and the `ARTIFACT_DELIVERED` postcondition check all work
correctly when only one worker is alive. The defensive hypotheses (H1
lookup-miss, H2 postcondition-check-miss) were both wrong.

### Long-term operational guardrail to consider

Two avenues to prevent this recurring:

- **Acquire a row-level lock in `process_next`**: change
  `repositories.tasks.list_by_statuses(WORKABLE_STATUSES, limit=1)` to do a
  conditional UPDATE that atomically claims the task (e.g. `UPDATE tasks SET status='claimed_by_worker_<id>' WHERE id IN (SELECT id FROM ... LIMIT 1)`).
  Two workers would still both call `list_by_statuses` but only one would
  successfully claim the task.
- **Single-instance worker enforcement**: a PID file in `.agent_control/run/worker.pid`
  with kill-old-on-start semantics. `start_stack.ps1` and the manual launch
  would both respect it.

Neither is necessary right now — the issue manifested only because of stale
supervised processes alongside a manual launch. Hygiene via `stop_stack.ps1`
between sessions is sufficient. Filing as a follow-up.

### Recovered cases

Re-test of `code_interpreter_excel_generate_and_load`: PASS.
The other two (`implicit_code_interpreter_numbers_report`,
`implicit_code_interpreter_markdown_from_notes`) follow the same pattern and
should also pass on the next full run.

---

## (was Priority 3 — kept here for archive) Make `artifact.deliver send_latest` find the just-registered code-interpreter artifact

### Why this exists (root cause)

Three cases failed with `fulfillment_gap: expected_artifact_delivered_missing`:

- `implicit_code_interpreter_numbers_report` → plan: code.interpreter generate_and_run + artifact.deliver `send_latest`
- `implicit_code_interpreter_markdown_from_notes` → same pattern
- `code_interpreter_excel_generate_and_load` → same pattern

`code.interpreter generate_and_run` already registers `files_created` as task
artifacts (the fix I added earlier in `code_interpreter.py:_register_created_artifacts`).
So `artifact.deliver send_latest` should find them. But the fulfillment validator
says nothing was delivered.

Two hypotheses, need to verify by reading the audit log of one stage:

**H1**: `send_latest` runs but returns `delivered: False` because its lookup
order doesn't find the just-created code-interpreter artifact. Looking at
`artifact_delivery.py:_resolve_artifact_path`, the fallback (no `path`/`artifact_id`)
iterates `self.artifacts.list_for_task(task_id)` *in reverse* — newest first. So
it should pick up the code-interpreter artifact. If `artifact.uri` exists and
the file is at that path, it would deliver successfully.

**H2**: `send_latest` runs successfully, sets `delivered: True` in its output,
but the fulfillment validator's check (`_postcondition_satisfied` line 281-286)
only reads `task.metadata["artifact_delivery"]` or `task.metadata["last_tool_result"].output.delivered`. If the executor doesn't persist `delivered: True` into the right field, the validator misses it.

### The change

Step 1 — diagnose by reading audit of one failed case:

```bash
python -X utf8 -c "
import json, sqlite3
conn = sqlite3.connect('agent_control.db')
c = conn.cursor()
c.execute(\"SELECT actor, event_type, payload_json FROM audit_events WHERE task_id IN (SELECT id FROM tasks WHERE objective LIKE '%numbers-summary%' ORDER BY created_at DESC LIMIT 1) ORDER BY created_at\")
for r in c.fetchall():
    print(r[0], '|', r[1], '|', r[2][:200])
"
```

If H1 (lookup miss): fix `_resolve_artifact_path` so the no-path-no-id branch
also calls `_artifact_by_basename`-style fuzzy resolution, or expands the
search to include `ArtifactType.GENERATED_FILE` ordered by `created_at`.

If H2 (validator misread): change `_postcondition_satisfied` for
`ARTIFACT_DELIVERED` so it ALSO accepts any artifact registered for this task
during this run (i.e., `len(artifacts.list_for_task(task_id)) > 0 and last_tool_name == "artifact.deliver"`). This is a structural fix that grounds the
postcondition in the artifact graph rather than a metadata flag.

I'll pick the fix after the diagnostic.

### What this could break (regression analysis)

H1 fix: only adds another lookup path. Doesn't change existing
`artifact_id`/`path` paths.

H2 fix: makes ARTIFACT_DELIVERED satisfied easier. Could cause cases where
artifact.deliver was attempted but failed (and a real delivery error exists) to
be falsely marked "delivered". To avoid that, only accept if the
`artifact.deliver` step's `ToolResult.status == SUCCEEDED`.

### Verification

1. `python -m pytest backend/tests/` — 267/267.
2. Test sanity: `python scripts/run_all_e2e_tests.py --only code_interpreter_excel_generate_and_load`.
3. Manually check `artifacts` table for the resulting task — should have a row with `artifact_type=generated_file` and `uri` pointing at the generated xlsx.
4. Manually check the Telegram chat got the file.

### Estimated impact

+3 passes → 36 / 47 = **77%**.

---

## Priority 4 — Tolerate tool-name aliases in `ApprovalGate.capability`

### Why this exists (root cause)

`safe_file_operations` failed with `LLM structured output failed validation:
3 validation errors for PlanModel ↳ approval_gates.0.capability …`. The LLM
put a string like `"artifact.deliver"` (a tool name) in
`approval_gates[].capability` (a Capability enum field). Same class of mistake
the planner used to make on `PlanStep.required_capabilities`, which I fixed
earlier with a `field_validator(mode="before")` that maps common tool-name
aliases to their underlying capability strings.

`ApprovalGate.capability` doesn't have that alias mapping. So the same model
mistake reaches Pydantic and gets rejected.

### The change

In `backend/src/agent_control/schemas.py`, add a `field_validator(mode="before")`
on `ApprovalGate.capability` that calls the existing
`_capability_alias_normalize()` helper used by `PlanStep.required_capabilities`.
(Or reuse the same alias dict.)

### What this could break (regression analysis)

The alias normalizer maps strings the LLM picks. Existing valid capability
strings pass through unchanged. The only thing that changes: a previously
rejected plan with `approval_gates[0].capability="artifact.deliver"` now gets
silently normalized to `capability="telegram.send"`. That's the intended
behavior. No call-sites read `approval_gates[].capability` for anything other
than the policy lookup, which is keyed by the enum value.

### Verification

1. `python -m pytest backend/tests/` — 267/267.
2. Direct schema check: `ApprovalGate(capability="artifact.deliver", risk_level=RiskLevel.HIGH, summary="x")` should produce `capability=Capability.TELEGRAM_SEND`.

### Estimated impact

+1 pass → 37 / 47 = **79%**.

---

## Priority 5 — Auto-rename or accept overwrite for `filesystem.manage.write_text_file` when destination exists

### Why this exists (root cause)

`output_delivery` failed with `filesystem operation failed: destination already
exists: …`. The adapter (`filesystem_manage.py:199`) raises when `path.exists()
and not overwrite`. The planner didn't set `overwrite=true`, and the path was
left over from a prior run's fixture.

The right behavior for a "create X" request when X already exists is one of:
1. Overwrite (matches "create" semantics for most users)
2. Auto-rename to `X-2.txt`, `X-3.txt`, …
3. Surface the conflict to the planner so it can decide

Option 2 is safest — never lose data, never silently fail. Match the
artifact-delivery `_search_for_filename` pattern we already have.

### The change

In `backend/src/agent_control/tools/filesystem_manage.py:_write_text_file`,
when `path.exists() and not overwrite`:
- If `overwrite` is `false` AND the request input has `allow_rename=True` (or by default), generate `path` with an incrementing suffix until it doesn't exist
- Otherwise keep the current ValueError
- Return the actual written path in the output so subsequent steps can chain to it

### What this could break (regression analysis)

Cases that EXPECTED the conflict error (e.g. tests that check no-overwrite
behavior). Search:

```bash
grep -rn "destination already exists\|already exists" backend/tests/
```

If any test asserts the error message, decide between (a) keep the explicit
`overwrite=false` + no-rename path as the strict default, and add an explicit
`allow_rename=true` opt-in; or (b) flip the default to auto-rename.

### Verification

1. `python -m pytest backend/tests/` — 267/267.
2. Manually invoke the adapter on an existing path; confirm the new file is
   written at `name-2.txt`.
3. The `output_delivery` case should now complete.

### Estimated impact

+1 pass → 38 / 47 = **81%**.

---

## Priority 6 — Tighten the code interpreter sandbox so generated scripts can't escape

### Why this exists (root cause)

`general_adapter_creation` failed with `workspace operation failed: file path
escaped workspace: C:\for fun\YBM\.agent_control\code_interpreter\…`. The
LLM-generated script tried to write to a path outside its assigned workspace.
The sandbox correctly refused but the task failed instead of recovering.

### The change

Two parts:

1. In `code_interpreter.py`, when a `_run_python` raises a "path escaped workspace" error, surface that into the LLM-aware retry block (already exists for parse/runtime errors). Add `kind="sandbox_violation"` to `_previous_attempt_block` and feed the script + violation to a re-generation pass.

2. In the regeneration prompt, append: *"This script tried to write outside its workspace directory. Write only to relative paths or paths under `{{workspace_dir}}` — never absolute paths."*

### What this could break (regression analysis)

Adds one more retry kind. Doesn't change happy path. Risk is bounded to the
already-failing path.

### Verification

1. `python -m pytest backend/tests/` — 267/267.
2. Direct invocation with a script that writes to `C:\foo.txt`; confirm the
   adapter retries with a corrected script.

### Estimated impact

+1 pass → 39 / 47 = **83%**.

---

## After all 6 priorities

| Bucket | Cases | After fix |
|---|---|---|
| A — `desktop.screenshot` orphan | 7 | recovered |
| B — fulfillment over-eager | 3 | recovered |
| C — artifact_delivered postcondition mismatch | 3 | recovered |
| D — LLM hallucinated paths | 2 | **still failing** (LLM capacity) |
| E — LLM Python crashed | 2 | **still failing** (LLM capacity) |
| F — destination already exists | 1 | recovered |
| G — sandbox violation | 1 | recovered |
| H — tool output insufficient | 1 | **still "failing"** (correct behavior — needs case adjustment, not a fix) |
| I — ApprovalGate alias | 1 | recovered |
| J — Telegram intake stuck | 2 | **still failing** (operational) |
| K — document.manage retry exhausted | 1 | **still failing** (LLM capacity, in retry loop) |

Pass rate: **39 / 47 = 83%** if all six fixes land cleanly and nothing regresses.

---

## Follow-up items NOT in this plan

These are NOT in scope for this pass; calling out so we don't forget:

- **LLM capacity (6 cases — D, E, I, K)**: planner picks wrong paths, writes
  broken Python, can't repair its own schema mistakes. Mitigations available
  but not cheap:
  - Use `major_provider` (gemma3:12b) for planner only. Needs the
    `localdeploy_gemma3_12b` profile re-added to `config.yaml` (we removed
    it during earlier cleanup) and `major_profile: localdeploy_gemma3_12b`
    set in the `llm` block.
  - Self-consistency: run the planner 3× with the same prompt, pick the plan
    that passes registry validation. 3× LLM cost on the planner.
  - Per-pattern deterministic short-circuits (last resort — violates
    CLAUDE.md "no case-by-case logic").

- **Operational (2 cases — J)**: `rename_files_by_content` and
  `chat_history_pdf_followup` had no `message_classified` audit event at all.
  Either polling stalled briefly or Telegram returned 409. Mitigation: run
  `scripts/stop_stack.ps1` before every e2e run; this is already documented in
  `e2e/README.md`. Could add a runner-level pre-flight that verifies the
  polling process is alive and reading the offset.

- **H — `audit_trail_final_summary`**: the validator correctly said the tool
  output didn't contain enough to answer. The case's expected behavior may
  need adjustment in `all_cases.json` rather than a code fix.

---

## How to execute

Each priority is self-contained. Suggested execution flow:

1. Apply Priority 1. Run tests. Run a 3-case sanity sweep.
2. Apply Priority 2. Run tests. Run another 3-case sanity sweep including one of the previously-blocked cases.
3. Apply Priority 3 (after diagnostic). Run tests.
4. Apply Priority 4. Run tests.
5. Apply Priority 5. Run tests.
6. Apply Priority 6. Run tests.
7. Full e2e run. Compare summary to baseline (`run_20260525_125109`).

After each priority, the unit tests should remain 267/267. If they drop, the
change introduced a regression and needs to be revised before continuing.

Each priority should also be a single logical commit so a regression can be
bisected to a specific change. Don't batch priorities into one commit even
though they're all in the same pass.

## Out-of-scope safety net

Before starting, verify:
- One `poll-telegram` process is running (check `Get-CimInstance Win32_Process | Where { $_.CommandLine -like "*poll-telegram*" }`)
- One `run-worker` process is running
- No supervised `start_stack.ps1` instances colliding with the manual ones
  (`scripts/stop_stack.ps1` cleanly resolves this)

Run `python -m pytest backend/tests/` once before starting. Baseline number:
**267 passed**. After each priority, the number must be exactly the same.
