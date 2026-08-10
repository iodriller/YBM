# Changelog

Notable changes to YBM Control. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [semantic versioning](https://semver.org/spec/v2.0.0.html).

`ybm check-updates` currently compares against the default branch. Once tagged
releases exist it should compare against the latest tag instead — see
`docs/KNOWN_GAPS.md`.

## [Unreleased]

### Added

- **Containerised headless profile.** `Dockerfile`, `docker-compose.yml` and
  `.dockerignore`. Telegram/WhatsApp intake, the operator loop, the code
  interpreter, MCP and the admin console all run in a container; desktop
  control, screenshots and the VS Code bridge cannot, and `ybm doctor` now
  reports them as unavailable rather than failing at call time.
- `ybm start --foreground`, which supervises until a service exits or a signal
  arrives. `start_all` spawns detached children and returns — correct for an
  interactive start, and an immediate exit to a container or systemd.
- `YBM-Setup.cmd`, a double-click first-run entry point.
- `--dry-run`, `--verify`, `--no-prompt` and `--install-dir` on both installers.
- Scheduled daily dependency audit (`.github/workflows/security-audit.yml`)
  that opens an issue rather than a pull request.
- Optional `.pre-commit-config.yaml` running the same ruff and gitleaks checks
  CI runs.
- `error_text.describe_exception`, so an error a human reads is never empty.
- `harness.assert_rejected`, which refuses to let a replay miss pass as a
  policy refusal.

### Changed

- **Installers require nothing preinstalled.** The Python 3.12+ gate is gone —
  `uv` is a standalone binary and provides the interpreter. git is optional,
  with an archive fallback. The uv installer URL is pinned to a version.
- `.mcp.json` launches the MCP server through `uv run` instead of a bare
  `python` with a relative `PYTHONPATH`, which only resolved on a machine that
  happened to have a system Python carrying the dependencies.
- The first-run wizard preselects a recommended Ollama model, and distinguishes
  "Ollama running with nothing pulled" from "no Ollama" — previously identical
  states, and the only point in onboarding that sent the user elsewhere.
- Scenario fixtures are rebuilt rather than merged when re-recording, dropping
  roughly 4,500 lines of unreachable keys.

### Fixed

- **Credential redaction missed two shapes.** A quoted key (`"api_key": "…"`,
  i.e. any JSON config) was never matched, and an unquoted value stopped at the
  first space, so a passphrase redacted to `*** horse battery staple`. The
  scrubbed answer is now written to the task row as well as the audit sink and
  the outbound message.
- Two harness defects that made every negative scenario test pass vacuously: a
  pytest `tmp_path` counter that changed each run so recorded keys could never
  be hit again, and `sort_keys=True` reordering recorded payloads so replay fed
  tools a differently-ordered dict than recording did.
- Workspace recovery from a user's message only ever matched Windows drive
  letters, so it was dead code on Linux and macOS.
- A test set `os.name` on the real `os` module, flipping `pathlib` to the
  Windows flavour process-wide and breaking every later `Path()` on POSIX.
- The anti-fabrication guard was disabled task-wide by any earlier write; it now
  compares claimed filenames against recorded ones.
- `filesystem.manage`'s desktop alias enumerated a directory that was not an
  allowed root — the one operation bypassing `_safe_path`.
- Admin token comparison is constant-time; scope matching refuses a `..`
  segment; three type-narrowing `assert`s that `python -O` strips became real
  raises.
- `pypdf` 6.14.2 → 6.15.0 (CVE-2026-71852, CVE-2026-71870).

### Known issues

See `docs/KNOWN_GAPS.md`. The largest: remote one-line install is impossible
while the repository is private (GitHub answers 404, not 403, to
unauthenticated requests), and tool output is not marked as untrusted in the
operator prompt.
