# Before making this repository public

Going public is irreversible in the ways that matter: history, forks, caches
and mirrors. This list separates what was **checked and is clean** from what
still needs doing, so the remaining work is small and specific.

Audited 2026-08-10 against 175 commits.

## Already clean — verified, no action

Recorded because these are the usual fears, and they do not apply here:

- **No real credentials in any commit.** Every commit swept for OpenAI,
  GitHub, Slack, AWS, JWT and Telegram-bot token shapes. Every hit is a
  deliberate test canary: `AKIAIOSFODNN7EXAMPLE` (AWS's own documented
  example), the RFC 7519 sample JWT, `ghp_abcdefghij…`, `xoxb-1234567890-…`,
  and the `sk-live-EVOLEAK-*` / `sk-should-never-appear` family.
- **`.env` was never committed** — `git log --all -- .env` is empty.
- **No Telegram bot token, API ID, or chat ID** anywhere in tracked files or
  in history.
- **`config/config.example.yaml` carries no personal values**, and matches the
  settings schema (0 keys documented that the schema does not have).
- **Generated and private state is ignored**: `.agent_control/`, `.venv`,
  `node_modules`, `backend/agent_control.db`, `static/`, `config/config.yaml`.
- **LICENSE** (MIT) and **SECURITY.md** with a private reporting route are
  present, as are issue and PR templates, CONTRIBUTING and CODE_OF_CONDUCT.
- **`e2e/all_cases.json`'s "career"/"resume" strings are synthetic** test-case
  names (`autonomy_career_fixture`, `resume_task` — the verb).

## Blockers

### B1 — The secret scan has never actually scanned history

`.github/workflows/ci.yml`'s `secrets` job is named "Scan Git history for
leaked secrets" and sets `fetch-depth: 0`, but `gitleaks-action` on a `push`
event runs `gitleaks detect --log-opts=-1` — **the last commit only**. The full
history has never been scanned by the tool.

The sweep above is a good signal and is *not* a substitute: it matches known
shapes, and generic high-entropy secrets are exactly what pattern matching
misses.

**Do:** run gitleaks once over the whole history before flipping visibility —
`gitleaks detect --log-opts=--all --redact -v` locally, or add
`--log-opts=--all` to the workflow. Fix anything it finds *before* the repo is
public, because rewriting history afterwards does not un-publish anything.

### B2 — Your Windows username is in 19 tracked files

`oneye` appears in recorded scenario fixtures (13 files), three tests, a doc,
and one source file, almost always as
`C:\Users\oneye\AppData\Local\Temp\ybm_scenario_scratch\…`.

Mild as disclosures go, but it is permanent and it links the repo to a personal
account. The fixtures are regenerable, so this is cheap to clear:

- Extend `scripted_llm._normalize` to collapse the user segment of a Windows
  temp path the way it already collapses `pytest-of-<user>` and the scenario
  scratch root, then re-record. The keys become user-independent too, which is
  worth having on its own.
- Or accept it, deliberately, and note that decision here.

### B3 — `mcp_client.py` hardcodes that path in a model-facing example

`tools/mcp_client.py:444`:

```python
{"operation": "install_server", "name": "filesystem", "command": "npx",
 "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\\\Users\\\\oneye"]},
```

This sits in a `ToolDefinition`'s `examples=`, so it is both public source and
part of what the model is shown. A placeholder (`C:\\Users\\you` or the
platform-appropriate home) is strictly better on both counts.

**Note the cost:** tool examples feed the operator's tool catalog, so changing
this is a prompt change and invalidates the scenario fixtures. Bundle it with
B2's re-record rather than paying that twice.

### B4 — CI is red — cause now known

One scenario test fails on Linux and macOS (`test_file_find_and_read`), 908 of
909 pass. A public repo whose first impression is a red badge invites the
question in every issue thread.

`harness.assert_completed` reported the cause on the next run:

```
expected="...ch\file_find_and_read'}"     recorded on Windows
actual  ="...tch/file_find_and_read'}"    on POSIX
```

A tool-input validation failure is fed back into the operator's next prompt,
and pydantic renders the offending dict as `input_value={...}` — **truncating
its own middle with a literal `...`**. That truncation discards the scenario
scratch-root prefix that `scripted_llm._normalize` matches on, so the only part
left is a tail still carrying the platform's path separator. Nothing else in
the prompt differs.

**Fix:** normalize separators inside an `input_value={…}` span in `_normalize`.
It is test-infrastructure only — no production behaviour changes — but it
alters the fixture key, so the affected fixtures (four entries in
`file_find_and_read`, plus any other prompt containing a path inside
`input_value=`) must be re-recorded, which needs the local model running.

This is the third distinct way a recorded prompt has proved host-dependent
(after the pytest `tmp_path` counter and `sort_keys` reordering the recorded
payload). Worth a single test that asserts a representative prompt normalises
identically under Windows and POSIX shapes, rather than finding the fourth on
CI.

## Should do

### S1 — Turn on the GitHub features that only exist for public repos

- **Secret scanning + push protection.** Free on public repos, and it blocks a
  future accidental commit rather than reporting it afterwards.
- **Dependabot alerts.** Not the pull requests — those were switched off
  deliberately (`2e422d2`, with `THREAT_MODEL.md` updated to say updates are
  reviewed manually). Alerts respect that policy and pair with the scheduled
  audit in `.github/workflows/security-audit.yml`.
- **Branch protection on `main`**: require CI green, and disallow force-push.
  A solo project still benefits from not being able to overwrite its own
  history by accident.
- Decide whether **Discussions** and **Wiki** should be on; both default on and
  are extra surface to moderate.

### S2 — Tag a release

`pyproject.toml` says `0.1.0`, there are no tags, and `CHANGELOG.md`'s only
section is `[Unreleased]`. Tag `v0.1.0` at the commit you publish so
`ybm check-updates` has something to compare against other than the branch tip.

### S3 — Update the README the moment it goes public

The Quickstart currently states, accurately, that the remote one-liner returns
404 because the repository is private. That paragraph becomes wrong at the
instant you flip the switch — and it is the first thing a visitor reads.

Verify the one-liner end to end on a clean machine at the same time. It has
never been runnable, so it has never been tested.

### S4 — Decide about publishing your own unfixed weaknesses

`docs/KNOWN_GAPS.md` and `docs/THREAT_MODEL.md` name specific gaps that are not
yet closed — including that tool output is not marked as untrusted in the
operator prompt.

This is a deliberate, defensible choice for a security-sensitive tool, and
`THREAT_MODEL.md` already takes that position. It is worth making the call
consciously rather than discovering it after the fact.

### S5 — Small cleanups

- Two tracked files reference the local checkout path `C:\for fun\…`.
- `.claude/settings.json` is committed. It only suppresses commit attribution —
  harmless, but it is a personal workflow preference now in public.
- Add a CI badge to the README once CI is green.
- Check `CODE_OF_CONDUCT.md`'s enforcement contact resolves to something real.

## Optional

- **A public container image.** `Dockerfile` and `docker-compose.yml` exist but
  have never been built (see `docs/KNOWN_GAPS.md`); publishing to GHCR only
  makes sense once one has been.
- **MCP registry listing** (PulseMCP, Smithery, the official registry). Only
  possible once public, and worth it — the server exposes seven real tools.
- **Screenshots in the README.** The console is the product; the README
  describes it in prose.

## Order

B1 first, and before anything else — its whole value is being done while the
repository is still private. B4 next so the first public CI run is green. B2
and B3 together in one re-record. Then S1 immediately after flipping, S3 in the
same commit as the flip.
