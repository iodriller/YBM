# Streamlining installation

Status: **implemented**, except for the one step that needs a human decision.

| Tier | State |
|---|---|
| 0 - one file instead of two | Done. `Install-YbmUv` in `scripts/lib/common.ps1`; `YBM-Setup.cmd` deleted. |
| 1 - releases with the console prebuilt | Done. `.github/workflows/release.yml` + `scripts/package_release.ps1`. Fires on a `v*` tag. |
| 2 - per-user Windows installer | Done. `packaging/windows/ybm.iss`, built and published by the same workflow. |
| 3 - winget | Manifests and renderer done (`packaging/winget/`). **Submitting the PR is deliberately manual.** |

Nothing here publishes anything on its own: no tag has been cut, and listing a
package on a public index is an external write that should be somebody's
decision rather than a side effect. See `packaging/winget/README.md`.

The rest of this document is the original analysis, kept because it is the
reasoning behind the shape of what was built.

## What a Windows user does today

From the README's Windows section, plus what the scripts actually require:

1. Open the GitHub page, find **Code -> Download ZIP** (not a download button; a
   menu item under a green button labelled with something other than "download").
2. Extract the folder. Browsing the ZIP in place and double-clicking from inside
   Explorer's archive preview does not work, because `YBM-Setup.cmd` resolves
   `%~dp0scripts\install.ps1` beside itself.
3. Install Node.js 22.22+ separately, or the admin console is never built and
   `/admin` serves build instructions instead of the product.
4. Double-click `YBM-Setup.cmd`, and clear whatever Windows says about it.
5. Wait for uv, Python, dependencies, and the console build.
6. Answer the browser wizard (model, optionally Telegram).
7. From then on, double-click a **different** file, `YBM.bat`.

That is seven touchpoints and three independent ways to end up with something
that does not work.

## Why there are two files

One line, in `scripts/ybm.ps1`:

```powershell
if (-not $uvCmd) {
  throw "uv is not installed. Install it from https://docs.astral.sh/uv/ then re-run '.\scripts\ybm.ps1 setup'."
}
```

`YBM.bat` calls `ybm.ps1 run`, which calls `Invoke-YbmSetup`, which refuses to
bootstrap `uv`. `YBM-Setup.cmd` exists only to run `scripts/install.ps1`, whose
extra job over `ybm run` is installing `uv` (`Resolve-Uv` plus a pinned
`astral.sh` installer) and, when the repo is absent, fetching the source.

So the second file is not a design decision about first-run versus later runs.
It is a workaround for one `throw`.

## Three problems the step count hides

**The console needs a toolchain the user does not have.**
`backend/src/agent_control/static/` is gitignored, so a source install ships no
built console. Without Node.js the primary UI does not exist. This is the
largest functional gap in the current flow, and it is invisible until after
install.

**There are no releases.** `gh release list` is empty and there is no release
workflow (`.github/workflows/` has `ci.yml` and `security-audit.yml` only).
`backend/src/agent_control/updates.py` polls
`api.github.com/repos/iodriller/YBM/releases/latest`, so the built-in update
check can only ever return `no_releases`. The feature exists and cannot work.

**Script files are the worst possible download format on Windows.** Files
carrying Mark-of-the-Web get a SmartScreen reputation check, and under Windows
11 Smart App Control, `.bat`, `.cmd`, and `.ps1` are among the types blocked
from launching outright when MOTW is present ([Microsoft Learn][motw],
[text/plain][motw2]). Smart App Control starts in evaluation mode rather than
on, so this does not hit every machine, but it is a hard failure when it does,
and the April 2026 servicing update lets users turn it on directly.

## Proposal

Four tiers. Each is independently shippable and each one is useful alone.

### Tier 0 - one file instead of two (small)

Move `Resolve-Uv` and the pinned uv bootstrap out of `scripts/install.ps1` into
`scripts/lib/common.ps1`, and call it from `Invoke-YbmSetup` in place of the
`throw`. Then:

- `YBM.bat` handles first run and every run after. "Double-click YBM.bat" is the
  entire instruction, permanently.
- `YBM-Setup.cmd` is deleted, or kept for one release as a thin alias that
  prints "use YBM.bat" and forwards.
- `scripts/install.ps1` keeps its real job: the `irm | iex` one-liner and
  getting the source onto a machine that has none.

This removes step 7 and the "keep this file beside scripts/" caveat. It changes
nothing about steps 1 to 3.

### Tier 1 - publish releases with the console prebuilt (highest value)

Add `.github/workflows/release.yml`: on a tag, run `npm ci && npm run build` in
`frontend/`, then attach a versioned archive that includes
`backend/src/agent_control/static/admin/`.

Consequences:

- **Node.js stops being an end-user requirement.** It becomes a contributor
  requirement, which it already is.
- The update check in `updates.py` starts returning real answers.
- The download becomes one named asset rather than "Code -> Download ZIP".

This is the single change that removes the most failure, because it deletes the
one prerequisite a non-developer cannot reasonably be asked to satisfy.

### Tier 2 - a real Windows installer (removes the ZIP and the script download)

Build a per-user installer from the Tier 1 archive. Inno Setup is the usual
choice; `PrivilegesRequired=none` gives a no-admin install into
`%LOCALAPPDATA%`, the pattern Chrome and Firefox use ([kinook][inno]).

- No extract step, so the "must stay beside scripts/" class of error disappears.
- Start Menu and desktop shortcuts, so no file to locate and double-click.
- A `.exe` still triggers SmartScreen's unknown-publisher prompt, but it is not
  in the category Smart App Control blocks outright the way `.cmd` is.
- Uninstall entry, which the current flow has no concept of.

### Tier 3 - winget (discovery, and no warning at all)

`winget install YBM` is one line, needs no download decision from the user, and
shows no SmartScreen prompt. Submission is a free PR to
[microsoft/winget-pkgs][winget].

Note the ordering constraint: winget accepts MSIX, MSI, APPX, and executable
installers, and **does not accept script-based installers** ([Microsoft
Learn][wingetdocs]). Tier 2 is a hard prerequisite for Tier 3.

## Why .exe, and not .msi, .msix, or a .bat

Asked and settled, so it does not get relitigated:

**A single .bat is the worst option, not the safest one.** Under Windows 11
Smart App Control, `.bat`, `.cmd`, and `.ps1` carrying Mark-of-the-Web are
blocked from launching outright, while an `.exe` gets a prompt the user can
clear. A script can never be Authenticode signed, so it can never accumulate
reputation or carry provenance. It has no uninstall entry. And winget rejects
script-based installers, so choosing `.bat` would forfeit the one distribution
path that shows no warning at all. The plain-folder option still exists for
people who want it - that is what the source ZIP plus `YBM.bat` is - but it is
the fallback, not the front door.

**.msi carries no trust advantage over .exe.** SmartScreen judges signature and
reputation, not container format; an unsigned MSI shows the same unknown
publisher warning. MSI earns its keep in enterprise deployment (Group Policy,
Intune, SCCM), which is not this audience. Worth adding later if organisations
ask to deploy YBM centrally; not worth a second artifact and a WiX toolchain
now.

**.msix is the most trusted format and technically cannot work here.** MSIX
confines an app's declared filesystem to `%USERPROFILE%\AppData` and its
registry to HKCU, and it must be signed to install at all. YBM exists to work on
the user's real machine - organise a Downloads folder, read PDFs on a desktop,
drive a browser and a terminal. The sandbox that makes MSIX trustworthy is the
thing that would break the product.

**.exe is also what the comparable tools ship**: Ollama, LM Studio, VS Code,
Chrome, Docker Desktop.

The lever for trust is not the extension. It is, in order: winget (no download
decision at all), build provenance, published checksums, and eventually a
certificate.

## On code signing

Worth setting expectations before anyone budgets for it:

- Azure Trusted Signing (now Azure Artifact Signing) is the cheap option at
  $9.99/month, but since April 2025 onboarding is restricted to organizations in
  the US or Canada with three or more years of verifiable history
  ([Microsoft][trustedsigning]). An individual maintainer is very likely not
  eligible.
- An OV certificate from a commercial CA is the fallback, in the low hundreds of
  dollars per year.
- Neither buys instant trust. SmartScreen reputation accrues with download
  volume against a file hash either way, so early users see a prompt regardless
  ([Microsoft Learn][smartscreen]).

Recommendation: do not buy a certificate to fix the current problem. Tiers 0 to
2 remove far more friction per unit of effort, and Tier 3 removes the prompt for
the users who take that path.

## What I would not do

- **Bundle a single .exe with PyInstaller.** It fights the design: YBM is a
  local service with a venv, a config file, a database, and an optional Node
  sidecar. Freezing it buys a smaller download and costs the update path, the
  MCP `uv run` entry point, and every "just edit config.yaml" instruction.
- **Auto-install Node.js from the installer.** Tier 1 makes it unnecessary.
  Shipping the built artifact is strictly better than shipping a build
  toolchain.
- **Auto-apply updates.** `updates.py` is deliberately report-only and that
  should stay; Tier 1 only makes the report accurate.

## Suggested order

Tier 0 first: it is hours of work, deletes a file and a documentation caveat,
and is worth doing whether or not the rest happens. Tier 1 next, because it is
the one that makes a double-clicked install actually produce a working console.
Tiers 2 and 3 together, when there is appetite for owning an installer build.

Target end state, Windows:

1. Download `YBM-Setup.exe` from the releases page, or `winget install YBM`.
2. Run it.
3. Answer the browser wizard.

[motw]: https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation
[motw2]: https://textslashplain.com/2016/04/04/downloads-and-the-mark-of-the-web/
[inno]: https://kinook.com/blog2/inno-setup.html
[winget]: https://github.com/microsoft/winget-pkgs
[wingetdocs]: https://learn.microsoft.com/en-us/windows/package-manager/package/manifest
[trustedsigning]: https://techcommunity.microsoft.com/blog/microsoft-security-blog/trusted-signing-is-now-open-for-individual-developers-to-sign-up-in-public-previ/4273554
[smartscreen]: https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation
