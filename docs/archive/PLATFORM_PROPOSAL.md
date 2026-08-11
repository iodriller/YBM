# Platform Proposal

What YBM should add to sit comfortably alongside other self-hosted agent
projects, and what it should not bother adding because it already has it.

Written after inventorying the repo rather than from a generic checklist. Each
item says what exists today, what is missing, and what "done" looks like.

## Already here — do not rebuild

Worth stating plainly, because these are the usual first suggestions:

| Thing | State |
|---|---|
| **MCP client** | `tools/mcp_client.py`, a server catalog, per-server config under `mcp.servers`, and `install_server` behind an approval gate |
| **MCP server** | `mcp_server.py` exposes seven tools (`create_task`, `get_task`, `list_tasks`, `answer_task_question`, `coding_sessions`, `coding_session_log`, `stop_coding_session`), with `.mcp.json` for Claude Code |
| **Docker sandboxing** | `tools/code_interpreter.py`'s `DockerBackend` — `--network none`, read-only rootfs, non-root user, container removal |
| **CI** | 4 jobs, 3-OS matrix, ruff, pip-audit, gitleaks history scan |
| **Repo hygiene** | LICENSE, SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, issue + PR templates |
| **Structured logging** | structlog with redaction, plus an audit trail and `ybm trace <task_id>` |
| **Health checks** | `/health` on backend and localdeploy, `ybm doctor`, `ybm status` |

So the MCP work is not "add MCP" — it is "finish and modernise the MCP we
have". Likewise Docker: the sandbox exists; what is missing is running *YBM
itself* in a container.

## The gaps

### P1 — Containerised deployment (missing entirely)

No Dockerfile, no compose file, no `.dockerignore`. Today the only way to run
YBM is to install it on the host.

Every comparable project ships a compose file, and the reason is not fashion:
it fixes the runtime version, the system dependencies for audio and vision, the
network surface exposed to Telegram webhooks, and the persistent state across
restarts — the four things that break a self-host.

**The tension that has to be resolved first.** YBM is not a pure server. It
does desktop control, screenshots, browser automation, and reads the user's
Desktop. A container cannot do most of that. So containerising is not a
lift-and-shift; it is defining a **headless profile**:

- *In the container:* Telegram/WhatsApp intake, the operator loop, filesystem
  tools scoped to mounted volumes, code interpreter, MCP, the admin console.
- *Not in the container:* desktop control, screenshots, the VS Code bridge, and
  browser automation that needs a real display.

That split should be explicit in config and in `ybm doctor`, so a containerised
install reports those capabilities as unavailable rather than failing at call
time.

**Done when:** `docker compose up` gives a working console with Telegram and a
local model, with `.agent_control/` and `config/` on named volumes, a
`HEALTHCHECK`, memory/CPU limits, and a documented list of what the headless
profile cannot do.

**Shape:** multi-stage build on `uv`'s image (the installer already standardises
on uv, so the container and the host install agree on how Python is provided).
Optionally a second compose service for Ollama, so `docker compose up` gets a
model too — that is the single biggest "it just worked" moment available here.

### P2 — `.mcp.json` breaks on a machine installed the new way

```json
"command": "python", "env": {"PYTHONPATH": "backend/src"}
```

Bare `python` plus a relative `PYTHONPATH`. It resolves on a dev box that
happens to have a system Python with the dependencies — verified working here
only because Anaconda supplies them. After the installer change, a fresh
install has **no system Python at all**: `uv` provides the interpreter inside
`backend/.venv`. So the shipped MCP config cannot work on a clean install.

**Done when:** it points at the venv interpreter (or a `ybm mcp` console
script), with an absolute-path resolution that works from any CWD.

Small change, and it is the difference between the MCP server being usable and
being decorative.

### P3 — MCP server is stdio-only

`mcp.run()` with no transport argument. Fine for local single-user tooling, and
that is genuinely YBM's main case today. But the 2026 production bar for
anything remote or multi-client is **Streamable HTTP with OAuth 2.1**, and MCP
servers are now formalised as OAuth Resource Servers.

YBM already runs a FastAPI app with admin-token auth. Mounting the MCP server
on it as Streamable HTTP is a natural fit and would let a phone or another
machine drive YBM through MCP, not just a local Claude Code.

**Done when:** stdio stays the default for local use, `--transport http`
exposes it on the existing app behind the existing auth, and the docs say which
to use when.

**Note:** public registry listing (PulseMCP, Smithery, the official registry)
is worth doing but is blocked by the same private-repo decision as the
installer.

### P4 — Release engineering

`version = "0.1.0"` in `pyproject.toml`, no git tags, no CHANGELOG, no
releases. `ybm check-updates` exists but there is nothing to compare against
except the default branch.

**Done when:** semver tags, a CHANGELOG, GitHub Releases, and `check-updates`
comparing against the latest release rather than `main`. Container images
tagged to match.

### P5 — Supply chain

- **Dependabot was disabled** (commit `2e422d2`). The two pypdf CVEs this week
  were caught by `pip-audit` in CI *after* they were already merged and shipped
  — Dependabot would have opened a PR when the advisory landed.
- **No pre-commit hooks.** ruff and gitleaks both run in CI; running them on
  commit catches the same problems before a red build. The gitleaks canary
  false-positive this week would have surfaced locally.
- **No SBOM or build provenance.** Once images are published, attestation
  matters more.

### P6 — Observability

structlog and the audit trail are good, and `ybm trace` is better than most
projects have. What is missing is anything a standard tool can read:
OpenTelemetry spans for the operator loop (one span per step, with tool name,
risk level, and policy decision as attributes) would make a slow or looping
task visible in any OTel backend rather than only through YBM's own viewer.

Lower priority than the above — it improves debugging, not capability.

## UX

Separate from the platform items, because this is where the day-to-day
experience actually lives.

### U1 — Time to first message

The installer now needs no prerequisites, but the wizard still asks for a model
before anything can happen. `bootstrap.py` already probes Ollama at
`127.0.0.1:11434`.

1. If Ollama is reachable with exactly one suitable model, **pre-select it** and
   show it as a confirmable default rather than an empty required field.
2. If Ollama is reachable with **no** models, offer to pull one. That is the
   only point in onboarding where the user must leave YBM, find a model name,
   and come back.
3. Show nothing optional before the first successful message. Telegram,
   WhatsApp, desktop control and Git push are all disabled by default already;
   none belongs on the first screen.

**Target:** from `docker compose up` or `YBM-Setup.cmd` to a reply, with zero
required fields when a local model is present.

### U2 — Failures should explain themselves

`operator_decide_failed:` with an empty message (see `KNOWN_GAPS.md` G2) cost
real debugging time this week: a reproducible failure was indistinguishable
from a flake. Every terminal failure the user can see should name a cause and a
next step, the way the installer now does.

### U3 — The phone case

The stated goal is driving this from a phone. That makes the admin console's
small-screen behaviour a primary surface, not an afterthought — the chat page,
the approval banner, and the task trace especially. Approvals are the one
interaction that *must* work on a phone, because a task blocks until answered.

**Needs measurement first:** I have not audited the console at mobile widths.
Worth a pass before committing to changes.

### U4 — Progress visibility

A task can run for minutes. The Telegram path sends a "task started" ping; the
web console should show live step progress from the same operator history the
trace view already reads, so a long task does not look hung.

## Suggested order

1. **P2** — one-line fix, and the MCP server is currently broken on a clean
   install without it.
2. **U1** — largest UX gain per unit of work, and unblocked.
3. **P1** — containerisation, starting with the headless-profile definition,
   because the split determines everything else about the image.
4. **P5** — re-enable Dependabot and add pre-commit; both are configuration.
5. **P4** — tags, CHANGELOG, releases, then image tags to match.
6. **P3** — Streamable HTTP transport.
7. **U3/U4**, then **P6**.

## What this deliberately does not propose

- **Kubernetes manifests / Helm.** A single-user local agent does not need
  them, and they would imply a multi-tenant story YBM does not have.
- **A hosted/cloud mode.** The threat model assumes a loopback-bound,
  single-operator install; changing that is a product decision, not a feature.
- **Rewriting the sandbox.** The Docker code interpreter backend already does
  network isolation, a read-only rootfs and a non-root user.
