# One-command bootstrap for YBM Control on Windows:
#   iwr https://raw.githubusercontent.com/iodriller/YBM/main/scripts/install.ps1 -UseBasicParsing | iex
#
# Clones the repo (if not already inside it), then delegates everything
# else to `scripts\ybm.ps1 run` (docs/UI_UX_AUDIT.md Phase 10) - venv/
# dependency setup, config.yaml, admin/vault tokens, the update check,
# starting the stack, and opening the admin console. The LLM/Telegram
# choice happens in that browser (the first-run wizard), not in this
# terminal - see docs/LOCAL_SETUP.md for what `setup` configures and
# CONTRIBUTING.md for the development (not just install) path. The
# interactive `ybm onboard` CLI wizard still exists for headless/SSH-only
# installs with no browser to open.
#
# This script only needs to run once, to get the code onto the machine -
# every launch after that is YBM.bat (double-click, no terminal) or
# `ybm run`, both idempotent: nothing to install/update just starts it.

$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/iodriller/YBM.git"
$InstallDir = if ($env:YBM_INSTALL_DIR) { $env:YBM_INSTALL_DIR } else { Join-Path $HOME "ybm" }

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Fail "git is required. Install it (https://git-scm.com/downloads) and re-run."
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  Fail "Python 3.12+ is required. Install it (https://www.python.org/downloads/) and re-run."
}
$versionOutput = & python --version 2>&1
Write-Step "Using $versionOutput"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Step "uv not found - installing it (https://astral.sh/uv)"
  irm https://astral.sh/uv/install.ps1 | iex
  $env:Path = "$HOME\.local\bin;$env:Path"
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Fail "uv install did not put 'uv' on PATH - open a new PowerShell window and re-run."
  }
}

$inRepo = (Test-Path "backend\pyproject.toml") -and (Test-Path "AGENTS.md") -and (Test-Path "scripts\ybm.ps1")
if ($inRepo) {
  Write-Step "Already inside a YBM checkout - using $(Get-Location)"
  $RepoDir = (Get-Location).Path
} else {
  if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Step "Found existing checkout at $InstallDir - pulling latest"
    git -C $InstallDir pull --ff-only
  } else {
    Write-Step "Cloning $RepoUrl into $InstallDir"
    git clone $RepoUrl $InstallDir
  }
  $RepoDir = $InstallDir
}

Set-Location $RepoDir
Write-Step "Installing and starting YBM Control"
& "$RepoDir\scripts\ybm.ps1" run
if ($LASTEXITCODE -ne 0) {
  Fail "ybm.ps1 run failed (exit $LASTEXITCODE). Run '.\scripts\ybm.ps1 doctor' to diagnose."
}
Write-Host ""
Write-Host "Pick a model and (optionally) Telegram in the admin console that just opened." -ForegroundColor Cyan
Write-Host "Next time, just double-click YBM.bat in $RepoDir - no terminal needed." -ForegroundColor Cyan
