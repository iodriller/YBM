# One-command bootstrap for YBM on Windows:
#   iwr https://raw.githubusercontent.com/iodriller/YBM/main/scripts/install.ps1 -UseBasicParsing | iex
#
# Clones the repo (if not already inside it), delegates venv/dependency
# setup to the existing, Windows-tested `scripts\ybm.ps1 setup`, then hands
# off to the interactive `ybm onboard` wizard for the LLM/Telegram choice,
# `doctor`, and starting the stack. See docs/LOCAL_SETUP.md and
# CONTRIBUTING.md for what onboarding configures and the dev-only path.

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

$inRepo = (Test-Path "pyproject.toml") -and (Test-Path "AGENTS.md") -and (Test-Path "backend")
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
Write-Step "Setting up backend\.venv and dependencies"
& "$RepoDir\scripts\ybm.ps1" setup
if ($LASTEXITCODE -ne 0) {
  Fail "ybm.ps1 setup failed (exit $LASTEXITCODE)."
}

Write-Step "Starting the onboarding wizard"
& "$RepoDir\backend\.venv\Scripts\ybm.exe" onboard
