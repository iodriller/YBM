# One-command bootstrap for YBM on Windows:
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/iodriller/YBM/main/scripts/install.ps1 | iex"
#
# Or, with no terminal at all: download and extract the complete repository,
# then double-click YBM.bat at its root - it installs whatever is missing,
# including uv, and starts the console. That is the only file a person needs.
#
# Git, Python, and uv do not need to be preinstalled. Node.js 22.22+ is only
# needed to build the admin console from source; release builds ship it
# prebuilt (.github/workflows/release.yml), so an installed copy needs no Node.
#
#   - uv is a standalone binary that needs no Python, and `uv sync` builds the
#     venv against a uv-managed interpreter (see backend/.venv/pyvenv.cfg's
#     `home`), which common.ps1's Get-YbmPython then returns. An earlier
#     version of this script demanded Python 3.12+ on PATH and never used it:
#     the gate turned working machines away and cost every first-time user a
#     Python download plus the "Add to PATH" checkbox nobody ticks.
#   - git is used when present, and a source zip is downloaded when it is not.
#
# Everything after getting the code onto the machine is `scripts\ybm.ps1 run`:
# venv/dependency setup, config.yaml, admin/vault tokens, the update check,
# starting the stack, and opening the admin console. The LLM/Telegram choice
# happens in that browser (the first-run wizard), not in this terminal.
#
# This runs once. Every launch after that is YBM.bat (double-click) or
# `ybm run`, both idempotent.

[CmdletBinding()]
param(
    # Print what would happen and change nothing. The cheapest way to check an
    # installer change without a clean VM.
    [switch]$DryRun,
    # Reserved for future prompts. Accepted now so scripted callers and CI can
    # pass it unconditionally; nothing on this path currently blocks on input.
    [switch]$NoPrompt,
    # After installing, prove it works: backend health plus `ybm doctor`.
    [switch]$Verify,
    # Where to install. Also honoured via $env:YBM_INSTALL_DIR.
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"

$RepoUrl  = "https://github.com/iodriller/YBM.git"
$ZipUrl   = "https://codeload.github.com/iodriller/YBM/zip/refs/heads/main"
$ApiUrl   = "https://api.github.com/repos/iodriller/YBM/commits/main"

if (-not $InstallDir) {
    $InstallDir = if ($env:YBM_INSTALL_DIR) { $env:YBM_INSTALL_DIR } else { Join-Path $HOME "ybm" }
}
if ($env:YBM_NO_PROMPT -eq "1") { $NoPrompt = $true }
if ($env:YBM_DRY_RUN -eq "1")   { $DryRun = $true }

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Info($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }
function Write-Good($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Write-Plan($msg) { Write-Host "[dry-run] $msg" -ForegroundColor Yellow }

function Fail($msg, $hint) {
    Write-Host ""
    Write-Host "ERROR: $msg" -ForegroundColor Red
    if ($hint) { Write-Host "  $hint" -ForegroundColor Yellow }
    exit 1
}

# --- 1. The code: git if present, source zip if not ----------------------
# uv is deliberately NOT bootstrapped here any more. It is installed by
# Install-YbmUv in scripts/lib/common.ps1, which `ybm.ps1 run` calls at the
# end of this script - one implementation, reachable from the double-click
# path as well as from this one. Nothing above this point needs uv: fetching
# the source uses git or a plain download, and `uv sync` provides Python
# itself, so the old separate `uv python install` step bought nothing.
$inRepo = (Test-Path "backend\pyproject.toml") -and (Test-Path "AGENTS.md") -and (Test-Path "scripts\ybm.ps1")
$git = Get-Command git -ErrorAction SilentlyContinue

if ($inRepo) {
    $RepoDir = (Get-Location).Path
    Write-Step "Already inside a YBM checkout"
    Write-Info $RepoDir
} elseif ($DryRun) {
    $RepoDir = $InstallDir
    if ($git) {
        Write-Plan "would clone $RepoUrl into $InstallDir (git found)"
    } else {
        Write-Plan "would download $ZipUrl into $InstallDir (no git; zip fallback)"
    }
} elseif ($git -and (Test-Path (Join-Path $InstallDir ".git"))) {
    Write-Step "Updating existing checkout at $InstallDir"
    & git -C $InstallDir pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        Write-Info "pull failed (local changes?) - continuing with the checkout as-is"
    }
    $RepoDir = $InstallDir
} elseif ($git) {
    Write-Step "Cloning into $InstallDir"
    & git clone --depth 1 $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { Fail "git clone failed" "Delete $InstallDir and re-run." }
    $RepoDir = $InstallDir
} else {
    # No git. Download the source zip instead of sending the user away to
    # install a second tool just to copy files onto their own machine.
    Write-Step "git not found - downloading the source zip instead"
    if (Test-Path (Join-Path $InstallDir "backend\pyproject.toml")) {
        Write-Info "existing install found at $InstallDir - leaving it in place"
        Write-Info "install git if you want in-place updates, or delete that folder to reinstall"
        $RepoDir = $InstallDir
    } else {
        $tempZip = Join-Path ([IO.Path]::GetTempPath()) "ybm-$([guid]::NewGuid().ToString('N')).zip"
        $tempDir = Join-Path ([IO.Path]::GetTempPath()) "ybm-$([guid]::NewGuid().ToString('N'))"
        try {
            Invoke-WebRequest -Uri $ZipUrl -OutFile $tempZip -UseBasicParsing
            Expand-Archive -LiteralPath $tempZip -DestinationPath $tempDir -Force
            $extracted = Get-ChildItem $tempDir -Directory | Select-Object -First 1
            if (-not $extracted) { Fail "the downloaded zip was empty" "Re-run, or install git and re-run." }
            New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir -Parent) | Out-Null
            Move-Item -LiteralPath $extracted.FullName -Destination $InstallDir
        } catch {
            # A private repository answers 404, not 401/403, to an unauthenticated
            # request - so "not found" here almost always means "not public".
            # Say that plainly instead of sending someone to check their wifi.
            $status = $_.Exception.Response.StatusCode.value__
            if ($status -eq 404) {
                Fail "the source archive is not publicly downloadable (HTTP 404)" @"
The repository is private, so anonymous download cannot work. Either:
  - make the repository public, or
  - install git and authenticate (gh auth login, or a credential helper), then re-run, or
  - copy an existing checkout onto this machine and run YBM.bat inside it.
"@
            }
            Fail "could not download the source zip ($($_.Exception.Message))" `
                 "Check your internet connection, or install git (https://git-scm.com/downloads) and re-run."
        } finally {
            Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
            Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        # Record which commit this is, so `ybm check-updates` still has a
        # baseline without a .git directory to read.
        try {
            $sha = (Invoke-RestMethod -Uri $ApiUrl -UseBasicParsing).sha
            Set-Content -Path (Join-Path $InstallDir ".ybm-source-version") -Value $sha -NoNewline
        } catch {
            Write-Info "could not record the source commit - 'ybm check-updates' will re-download to compare"
        }
        $RepoDir = $InstallDir
        Write-Good "downloaded to $InstallDir"
    }
}

# --- 2. Hand off to ybm.ps1 ----------------------------------------------
if ($DryRun) {
    Write-Plan "would run: $RepoDir\scripts\ybm.ps1 run"
    if ($Verify) { Write-Plan "would then run: ybm.ps1 doctor (--Verify)" }
    Write-Host ""
    Write-Host "Dry run complete - nothing was installed or changed." -ForegroundColor Yellow
    exit 0
}

Set-Location $RepoDir
Write-Step "Installing dependencies and starting YBM"
# `run` is already non-interactive - it is the double-click path - so -NoPrompt
# has nothing to suppress here and is not forwarded.
& "$RepoDir\scripts\ybm.ps1" run
if ($LASTEXITCODE -ne 0) {
    Fail "startup failed (exit $LASTEXITCODE)" `
         "Run '$RepoDir\scripts\ybm.ps1 doctor' to diagnose. Logs: $RepoDir\.agent_control\logs"
}

# --- 3. Optional post-install proof --------------------------------------
if ($Verify) {
    Write-Step "Verifying the install"
    & "$RepoDir\scripts\ybm.ps1" doctor
    if ($LASTEXITCODE -ne 0) {
        Fail "post-install verification failed" `
             "The stack installed but doctor reported problems - see the [FAIL] lines above."
    }
    Write-Good "verified"
}

Write-Host ""
Write-Host "Pick a model and (optionally) Telegram in the admin console that just opened." -ForegroundColor Cyan
Write-Host "Next time, just double-click YBM.bat in $RepoDir - no terminal needed." -ForegroundColor Cyan
