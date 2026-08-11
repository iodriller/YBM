# Before making this repository public

Going public is irreversible in the ways that matter: history, forks, caches
and mirrors. This list separates what was **checked and is clean** from what
still needs doing, so the remaining work is small and specific.

Audited 2026-08-10 against 175 commits.

## Already clean - verified, no action

Recorded because these are the usual fears, and they do not apply here:

- **No real credentials in any commit.** Every commit swept for OpenAI,
  GitHub, Slack, AWS, JWT and Telegram-bot token shapes. Every hit is a
  deliberate test canary: `AKIAIOSFODNN7EXAMPLE` (AWS's own documented
  example), the RFC 7519 sample JWT, `ghp_abcdefghij…`, `xoxb-1234567890-…`,
  and the `sk-live-EVOLEAK-*` / `sk-should-never-appear` family.
- **`.env` was never committed** - `git log --all -- .env` is empty.
- **No Telegram bot token, API ID, or chat ID** anywhere in tracked files or
  in history.
- **`config/config.example.yaml` carries no personal values**, and matches the
  settings schema (0 keys documented that the schema does not have).
- **Generated and private state is ignored**: `.agent_control/`, `.venv`,
  `node_modules`, `backend/agent_control.db`, `static/`, `config/config.yaml`.
- **LICENSE** (MIT) and **SECURITY.md** with a private reporting route are
  present, as are issue and PR templates, CONTRIBUTING and CODE_OF_CONDUCT.
- **`e2e/all_cases.json`'s "career"/"resume" strings are synthetic** test-case
  names (`autonomy_career_fixture`, `resume_task` - the verb).

## Blockers - all cleared

### B1 - The secret scan had never scanned history - FIXED

`.github/workflows/ci.yml`'s `secrets` job is named "Scan Git history for
leaked secrets" and sets `fetch-depth: 0`, but `gitleaks-action` on a `push`
event runs `gitleaks detect --log-opts=-1` - **the last commit only**. The full
history has never been scanned by the tool.

The sweep above is a good signal and is *not* a substitute: it matches known
shapes, and generic high-entropy secrets are exactly what pattern matching
misses.

Run over all 175 commits, it found **four** hits - all in `test_redaction.py`,
the fake values added this week for the quoted-key and multi-word-value cases.
Nothing real, but they would have failed CI the moment the scan was fixed.

New canaries now carry a `NOTAREALKEY` marker allowlisted once, so a future
redaction test needs no allowlist change; the pre-marker literals are
allowlisted explicitly, because a commit already made cannot be un-made.
History now scans clean.

The job runs a pinned, checksum-verified gitleaks binary with an explicit
`--log-opts=--all` rather than the action, so the scope is visible in the diff.

Worth knowing: scanning the working tree rather than git finds 702 hits - 671 in
`.agent_control/`, **six real ones in `.env`**, two in `__pycache__`. All
gitignored. `.gitignore` is what stands between those and a public repository.

### B2 - Account name in tracked files - FIXED

Was 19 files, now **zero**. The cause was that the recorder stored **raw**
prompts, so every recording carried the recording machine's absolute paths. It
now stores normalized ones, `_normalize` collapses a Windows user directory, and
responses are stored with a `<scenario_scratch_root>` placeholder that
`_rebase_scenario_paths` expands again at replay.

Source code inside a response keeps its path shape - rewriting it can change
parse or runtime behaviour, which is why `_rebase_scenario_paths` skips it - but
the account name in it is scrubbed to `<user>`. The path already resolved
nowhere but the recording machine, so that segment costs nothing to remove.

### B3 - `mcp_client.py` model-facing example - FIXED

The `install_server` example no longer names a home directory. It is shown to
the model in the tool catalog, so it was both public source and prompt content.

### B4 - CI red on POSIX - FIXED

Pydantic renders the offending value into its validation message as
`input_value={...}` and truncates that repr at a fixed length. Two things then
differed per platform: the path separator in whatever survived, and *where* the
truncation fell, because the underlying path lengths differ
(`C:\Users\...\AppData\Local\Temp\...` against `/tmp/...`). Normalizing only
the separator left a second failure on the next CI run.

The whole span is now replaced with a constant. The field name and error type
either side of it still carry the meaning; the rendered value never did, and it
is only ever hashed and diffed, never executed.

No fixtures needed re-recording: `_reindex` recomputes every key from the stored
prompts with the current normalizer, so both sides move together. That holds for
any key-derivation change - only a change to prompt *content* (a prompt file, a
tool's description or examples) genuinely invalidates a recording.

## Should do

### S1 - Turn on the GitHub features that only exist for public repos

- **Secret scanning + push protection.** Free on public repos, and it blocks a
  future accidental commit rather than reporting it afterwards.
- **Dependabot alerts.** Not the pull requests - those were switched off
  deliberately (`2e422d2`, with `THREAT_MODEL.md` updated to say updates are
  reviewed manually). Alerts respect that policy and pair with the scheduled
  audit in `.github/workflows/security-audit.yml`.
- **Branch protection on `main`**: require CI green, and disallow force-push.
  A solo project still benefits from not being able to overwrite its own
  history by accident.
- Decide whether **Discussions** and **Wiki** should be on; both default on and
  are extra surface to moderate.

### S2 - Tag a release

`pyproject.toml` says `0.1.0`, there are no tags, and `CHANGELOG.md`'s only
section is `[Unreleased]`. Tag `v0.1.0` at the commit you publish so
`ybm check-updates` has something to compare against other than the branch tip.

### S3 - Update the README the moment it goes public

The Quickstart currently states, accurately, that the remote one-liner returns
404 because the repository is private. That paragraph becomes wrong at the
instant you flip the switch - and it is the first thing a visitor reads.

Verify the one-liner end to end on a clean machine at the same time. It has
never been runnable, so it has never been tested.

### S4 - Decide about publishing your own unfixed weaknesses

`docs/KNOWN_GAPS.md` and `docs/THREAT_MODEL.md` name specific gaps that are not
yet closed - including that tool output is not marked as untrusted in the
operator prompt.

This is a deliberate, defensible choice for a security-sensitive tool, and
`THREAT_MODEL.md` already takes that position. It is worth making the call
consciously rather than discovering it after the fact.

### S5 - Small cleanups

- Two tracked files reference the local checkout path `C:\for fun\…`.
- `.claude/settings.json` is committed. It only suppresses commit attribution -
  harmless, but it is a personal workflow preference now in public.
- Add a CI badge to the README once CI is green.
- Check `CODE_OF_CONDUCT.md`'s enforcement contact resolves to something real.

## Optional

- **A public container image.** `Dockerfile` and `docker-compose.yml` exist but
  have never been built (see `docs/KNOWN_GAPS.md`); publishing to GHCR only
  makes sense once one has been.
- **MCP registry listing** (PulseMCP, Smithery, the official registry). Only
  possible once public, and worth it - the server exposes seven real tools.
- **Screenshots in the README.** The console is the product; the README
  describes it in prose.

## Order

B1 first, and before anything else - its whole value is being done while the
repository is still private. B4 next so the first public CI run is green. B2
and B3 together in one re-record. Then S1 immediately after flipping, S3 in the
same commit as the flip.
