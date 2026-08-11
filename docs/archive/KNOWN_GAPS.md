# Known Gaps

Open items carried forward deliberately, each with the file that causes it and
what "done" looks like. Recorded rather than left in a commit message so they
stay findable.

Companion to `docs/E2E_FINDINGS.md` (which holds the live-run evidence) and
`docs/THREAT_MODEL.md` (which holds the security posture this measures against).

## Blocking CI

### G1 — Five scenario fixtures need re-recording

`code_interpreter_csv_summary`, `code_interpreter_generate_file`,
`code_interpreter_json_transform`, `document_pdf_summary`, and
`implicit_code_interpreter_numbers_report`.

Their negative cases fail on `harness.assert_rejected` because the fixtures
still carry the key order written before `scripted_llm._save` stopped sorting
recorded payloads. Until then the same tests passed **vacuously** — a replay
miss satisfies `status != COMPLETED` exactly as a real refusal does, so they
would have passed with the policy check deleted.

**Done when:** `ybm scenario record <name>` for each, suite green.
**Cost:** roughly 15 minutes of local inference.

### G2 — `operator_decide_failed:` carries an empty message

Three of the re-records above failed with exactly that string and nothing after
the colon, which made a reproducible failure indistinguishable from a flake and
cost real debugging time.

The operator raises with a `str(exc)` that is empty for some provider errors.
Whatever the underlying cause (timeout, transport error, empty completion), it
should reach `last_worker_error`.

**Where:** `orchestration/worker.py` (`operator_decide_failed` construction) and
`orchestration/operator.py`'s retry loop.
**Done when:** a failed model call names its cause; ideally the exception type
when there is no message.

## Security

### G3 — Tool output is not marked as untrusted in the operator prompt

`orchestration/operator.py::_format_history` renders tool results as plain
`output: <contents>`. A file or web page containing "ignore previous
instructions" arrives as ordinary context, indistinguishable from YBM's own
framing.

`docs/THREAT_MODEL.md` already states the position: capabilities, risk levels,
scopes and approvals bound what a successful injection can *do*, and do not
make the content safe. That is a legitimate layered stance, and current
practice agrees prompt instructions alone are a nudge rather than a control.
What is missing is the cheap half — *spotlighting*: wrapping untrusted content
in randomised delimiters that the system prompt declares opaque data. It is
reported to measurably reduce attack success at minimal cost.

**Done when:** tool output is delimited with a per-run random marker and
`prompts/base/operator_system.md` tells the model that region is data, never
instructions.
**Cost:** changes the system prompt, so **all 16 scenario fixtures need
re-recording**. Schedule it with recording time available.

### G4 — Redaction is pattern-only

`storage/redaction.py` matches credential-shaped *names* and known provider
token shapes. Current practice for secret detection is hybrid: patterns plus
entropy, because pattern matching alone cannot cover generic secrets and no
regex set covers every provider.

A high-entropy value with no credential-shaped key name and no recognised
provider prefix still passes through.

**Deliberately not done yet:** entropy scanning would also flag git SHAs,
UUIDs, base64 blobs and hashes, and this redactor runs on user-facing answers.
The false-positive cost needs a decision before adding it.

### G5 — Operator does not retry an unsupported operation

Carried over from `docs/E2E_FINDINGS.md` P0-2a. One `unsupported operation`
failure ends the attempt even though the error message lists every valid
operation and step budget remains.

Lowest priority by that document's own ordering: with the fulfillment and
anti-fabrication guards in place, this produces an honest failure rather than a
fabricated success, so it is a quality issue rather than a safety one.

## Maintainability

### G6 — `orchestration/worker.py` is 2,519 lines

43 module-level functions and 23 methods in one file; it grew by ~1,150 lines
in a single commit. The operator loop, the approval resume path, the
fulfillment/anti-fabrication guards, and a dozen input-normalisation helpers
all live together. The normalisation helpers (`_coding_agent_input_*`,
`_filesystem_search_input_*`, `_existing_absolute_directories`) are pure
functions with no worker state and would move cleanly.

### G7 — `_safe_segment` is duplicated three times

Identical body in `tools/adapter_factory.py`, `tools/code_interpreter.py` and
`tools/local_workspace.py` — same regex, same `strip("._")`, differing only in
fallback string. It sanitises path segments, so it is security-adjacent: a fix
to one copy does not reach the other two.

### G8 — `_trim` is duplicated

Byte-identical in `channels/task_notify.py` and
`channels/telegram_notifications.py`. `task_notify.py` was extracted *out of*
`telegram_notifications.py` and the original kept its copy; both are live
(`cli.py` imports the notifier).

## Installation

### G9 — Remote install is impossible while the repository is private

A private GitHub repository answers **404**, not 403, to unauthenticated
requests. So `raw.githubusercontent.com/.../install.sh`,
`codeload.../zip/refs/heads/main` and `api.github.com/repos/.../commits/main`
all 404, and the `curl … | sh` one-liner the README led with could never have
worked on a fresh machine.

The installers now detect this and explain it. The underlying choice — public
repo, authenticated install, or publishing built artifacts separately — is a
product decision, not a code one, and everything else in
`docs/INSTALL_UX_PLAN.md` depends on it.

The prerequisite problem noted here previously (git and Python 3.12+ demanded
up front, with the Python requirement spurious) is **fixed**: `uv` provides the
interpreter and git is optional.
