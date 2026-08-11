# Public release checklist

The repository can be prepared locally while private, but the following steps
must be completed against GitHub at release time. Do not mark them complete
based only on local tests.

## Before changing visibility

- [ ] Review the final diff and confirm every untracked file is intentional.
- [ ] Run the full backend, frontend, extension, WhatsApp, secret, and
  container checks documented in [CONTRIBUTING.md](../CONTRIBUTING.md).
- [ ] Review the README animation frame-by-frame for tokens, usernames,
  private paths, messages, or account identifiers.
- [ ] Confirm `config/config.yaml`, `.env`, `.agent_control/`, databases, logs,
  screenshots, generated workspaces, caches, and built admin assets are not
  tracked.

## When the repository becomes public

- [ ] Change repository visibility and immediately run the `CI` and
  `Security audit` workflows. Current private-repository runs are blocked by
  the account billing state before any job starts; public Actions eligibility
  must be confirmed from the new run, not assumed.
- [ ] Require the passing CI jobs on `main`: backend matrix,
  backend-quality, frontend, WhatsApp bridge, VS Code extension, container,
  and secret-history scan.
- [ ] Enable private vulnerability reporting, dependency graph/alerts, and
  secret scanning where GitHub makes them available.
- [ ] Verify the repository description, topics, social preview, license
  detection, default branch, and issue/discussion settings from a signed-out
  browser.
- [ ] Test the installer from a clean, unauthenticated Windows machine and a
  clean macOS/Linux environment. Do not add a remote one-line install claim
  until that exact command succeeds anonymously.

## Release candidate validation

- [ ] Run the narrow live E2E cases with a configured model and review their
  traces; this makes real external calls.
- [ ] Exercise the three starter prompts with the documented default model.
- [ ] Transcribe one real web recording and one Telegram voice note.
- [ ] If WhatsApp is advertised beyond “optional text-only,” QR-pair a test
  account and verify receive, reply, allowlist refusal, and restart recovery.
- [ ] Create and push the first semantic version tag only after the checks
  above pass; then create release notes from [CHANGELOG.md](../CHANGELOG.md).
- [ ] After tags exist, change `ybm check-updates` from default-branch
  comparison to the latest stable release tag.
