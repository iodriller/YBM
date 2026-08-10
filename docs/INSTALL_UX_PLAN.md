# Install UX Plan

Goal: get a person from "found YBM" to "talking to it" with the fewest steps
that still leave a working, updatable install.

## Status

Phases 1, 2 (partly), 4 and 5 are **implemented**. What remains is listed at
the bottom under "Still open".

## Blocker found while implementing: the repository is private

Every documented remote install URL returns **404** to anyone who is not
authenticated, because a private GitHub repository answers 404 rather than 403:

| URL | Result |
|---|---|
| `raw.githubusercontent.com/iodriller/YBM/main/scripts/install.ps1` | 404 |
| `raw.githubusercontent.com/iodriller/YBM/main/scripts/install.sh` | 404 |
| `codeload.github.com/iodriller/YBM/zip/refs/heads/main` | 404 |
| `api.github.com/repos/iodriller/YBM/commits/main` | 404 |

The README's `curl … | sh` one-liner therefore cannot work on a fresh machine
today, and no amount of click-reduction changes that. **Distribution model is
the real decision**, and it is upstream of everything else here:

- **Make the repository public.** Everything in this document then works as
  written, including the one-liner and a winget manifest.
- **Keep it private.** Remote install must authenticate: `gh auth login` plus
  `gh repo clone`, or a private release asset fetched with a token. Neither is
  a "one command with nothing installed" story.
- **Publish built artifacts** to a public location while the source stays
  private.

Until that is decided, the installer detects the 404 and says exactly this,
rather than blaming the user's network.

Measured against `scripts/install.ps1` / `scripts/install.sh` as they are, and
against what current one-command installers do (OpenClaw's installer internals,
`uv`'s bootstrap). Every claim below names the line that causes it.

## Where the time actually goes today

The README promises "a couple of minutes on a machine with nothing installed
but git and Python 3.12+". That last clause is the problem: it is two manual
installs, each a download, an installer, a restart of the shell, and for Python
a checkbox most people miss.

| Step | Cost now | Cause |
|---|---|---|
| Install git | download + installer + shell restart | `install.ps1:26` hard-fails without it |
| Install Python 3.12+ | download + installer + "Add to PATH" checkbox | `install.ps1:30` hard-fails without it |
| Open PowerShell, paste a command | one terminal interaction | README Quickstart |
| Clone + venv + deps | ~1–2 min, unattended | `ybm.ps1 run` |
| Pick a model / Telegram | browser wizard | first-run wizard |

**Two of those five are avoidable outright, and one of them is avoidable
today with a five-line change.**

## The single highest-value fix: drop the Python prerequisite

`install.ps1:30-33` refuses to continue without `python` on PATH. Nothing then
uses it.

- `install.ps1:37-44` installs `uv` if missing.
- `uv` is a standalone binary that needs no Python at all.
- `ybm.ps1 setup` builds the venv with `uv sync`, and `uv` downloads its own
  interpreter — the current `backend/.venv/pyvenv.cfg` on this machine reads
  `home = ...\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none`.
- `common.ps1::Get-YbmPython` returns the venv interpreter and only falls back
  to `python` when the venv is missing, which is the broken-install path.

So the gate rejects machines that would have worked. Removing it turns "install
Python first, with the right version, with the PATH checkbox" into nothing at
all.

**Change:** delete the `python` check; after ensuring `uv`, run
`uv python install 3.12` so the interpreter is present before `ybm.ps1 run`.
Keep a *diagnostic* mention of any system Python, not a gate.

## Phase 1 — Zero prerequisites (biggest win, smallest diff)

1. **Remove the Python gate** as above.
2. **Pin the uv installer.** `install.ps1:39` fetches `https://astral.sh/uv/install.ps1`
   (floating latest). Pin a version in the URL so two machines installing a week
   apart get the same uv, and a bad uv release cannot break YBM installs.
3. **Stop relying on PATH inheritance.** Line 40 prepends `$HOME\.local\bin`
   and line 41 re-checks `Get-Command uv`. Resolve and call the absolute
   `uv.exe` path instead — `UV_NO_MODIFY_PATH=1` plus an explicit path removes
   the "open a new PowerShell window and re-run" failure at line 42, which is a
   dead end in the middle of an install.
4. **Make git optional.** Fall back to the GitHub zip
   (`codeload.github.com/iodriller/YBM/zip/refs/heads/main`) when `git` is
   absent, recording the resolved commit in a version file so `check-updates`
   still works. OpenClaw instead bootstraps a portable MinGit under
   `%LOCALAPPDATA%`; the zip is lighter and enough here, since updates can use
   the same download path.

**Result:** prerequisites 2 → 0. Works on a fresh Windows box with nothing
installed.

## Phase 2 — Remove the terminal

Even a perfect one-liner requires opening PowerShell and pasting. For the
"double-click" audience `YBM.bat` already serves after install, the gap is
first-run only.

1. **`YBM-Setup.cmd`** — a small committed file the user downloads and
   double-clicks. It self-elevates only if needed, runs the Phase 1 bootstrap,
   and leaves the console open on failure so the error is readable. No typing,
   no PowerShell knowledge, no execution-policy flag to explain.
2. **A winget manifest** — `winget install iodriller.YBM` for people who prefer
   a package manager, and it gives update-through-winget for free.
3. Keep the `irm | iex` one-liner for SSH and CI. It should not be the
   recommended path for a desktop user.

**Result:** terminal interactions 1 → 0 for the primary path.

## Phase 3 — Fewer clicks after install

The wizard is already well placed (browser, not terminal), and Telegram is
already optional. What remains:

1. **Auto-select the local model when there is exactly one sane choice.**
   `bootstrap.py` already probes Ollama at `127.0.0.1:11434`. If it is
   reachable and a suitable model is present, pre-select it and show it as a
   confirmable default rather than an empty required field.
2. **Offer to pull a model when Ollama is present but empty.** That is the one
   case where the user currently has to leave YBM, find a model name, and come
   back.
3. **Defer everything not needed for the first message.** Telegram, WhatsApp,
   desktop control and Git push are all disabled by default; none should appear
   before the first successful conversation. First screen ideally reads
   "You're ready — say something", with a Settings link.

## Phase 4 — Unattended and verifiable

Parity with what mature installers expose, and what makes this testable:

1. `--no-prompt` / `YBM_NO_PROMPT=1` — never block on input.
2. `--dry-run` — print the plan, change nothing. Also the cheapest way to test
   installer changes without a clean VM.
3. `--verify` — post-install smoke test: backend health, `ybm doctor`, one
   round-trip through the local model. Exit non-zero on failure, so a broken
   install reports itself instead of surfacing later as a confusing runtime
   error.
4. `--json` NDJSON progress events for CI and for a future GUI progress bar.
5. `YBM_INSTALL_DIR` already exists (`install.ps1:21`); document it alongside
   the rest.

## Phase 5 — Honest failure

The current script fails with a bare message and `exit 1` at four places. Each
should say what to do next:

- git missing → after Phase 1 this is no longer fatal; say it is downloading a
  zip instead.
- `ybm.ps1 run` failed → already suggests `doctor`; also print the log path.
- Port already in use → detect and offer the next free port rather than
  failing.
- No LLM reachable → the install still succeeded; say so, and point at the
  wizard rather than exiting non-zero.

## What was implemented

- **Phase 1, complete.** The Python gate is gone; `uv python install 3.12`
  provides the interpreter. The uv installer URL is pinned to a version. uv is
  resolved to an absolute path with `UV_NO_MODIFY_PATH=1`, so the "open a new
  window and re-run" dead end cannot happen. git is optional, with an archive
  fallback on both platforms.
- **Phase 2, partly.** `YBM-Setup.cmd` at the repo root is the double-click
  entry point; it forwards `--dry-run` / `--verify` / `--no-prompt` and always
  pauses, so a failure message survives the window closing. A winget manifest
  is blocked on the distribution decision above.
- **Phase 4, complete.** `--dry-run`, `--verify`, `--no-prompt`,
  `--install-dir`, plus `YBM_DRY_RUN` / `YBM_NO_PROMPT` / `YBM_INSTALL_DIR`.
- **Phase 5, complete.** Every failure names a cause and a next step; the 404
  case explains the private-repo situation instead of blaming the network.

## Still open

- **Phase 3** (fewer clicks after install): auto-select the sole local model,
  offer to pull one when Ollama is present but empty, and defer every optional
  channel until after the first successful message.
- **winget manifest** and the remote one-liner, both blocked on distribution.
- **Port-in-use handling**: offer the next free port instead of failing.
- `install.ps1` and `install.sh` are maintained as parallel implementations and
  have drifted before. They are in step as of this change; a shared contract
  test would be better than vigilance.

## Verification

An install-UX change is only credible on a machine that does not already have
the tooling.

- A clean Windows VM with **no** git, **no** Python: `YBM-Setup.cmd` must reach
  a working console unattended.
- The same VM with Ollama present but no models pulled (Phase 3.2).
- A machine that already has everything: the installer must be a no-op that
  just starts, and must not reinstall or upgrade anything unasked.
- `--dry-run` output reviewed against each of the above before running for real.
