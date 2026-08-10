# One-command bootstrap for YBM Control on Windows:
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/iodriller/YBM/main/scripts/install.ps1 | iex"
#
# Or, with no terminal at all: download YBM-Setup.cmd from the repo root and
# double-click it. That is the recommended path for a desktop install.
#
# Requires nothing preinstalled. Not git, not Python.
#
#   - uv is a standalone binary that needs no Python, and `uv python install`
#     provides the interpreter. An earlier version of this script demanded
#     Python 3.12+ on PATH and then never used it: `uv sync` builds the venv
#     against a uv-managed interpreter (see backend/.venv/pyvenv.cfg's `home`),
#     and common.ps1's Get-YbmPython returns that venv. The gate turned a
#     working machine away and cost every first-time user a Python download
#     plus the "Add to PATH" checkbox nobody ticks.
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

# Pinned on purpose. An unpinned https://astral.sh/uv/install.ps1 means two
# machines a week apart get different uv versions, and a bad uv release breaks
# every YBM install at once with nothing changed on our side.
$UvVersion   = "0.9.7"
$UvInstaller = "https://astral.sh/uv/$UvVersion/install.ps1"
$PythonVersion = "3.12"

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

# --- 1. uv ---------------------------------------------------------------
# Resolved to an absolute path rather than trusting PATH. The old script
# prepended ~\.local\bin and re-checked Get-Command, then gave up with "open a
# new PowerShell window and re-run" - a dead end halfway through an install,
# because a freshly written PATH entry is not visible to the running process.

function Resolve-Uv {
    foreach ($candidate in @(
        (Join-Path $HOME ".local\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe")
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $onPath = Get-Command uv -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    return $null
}

$uv = Resolve-Uv
if ($uv) {
    Write-Step "uv already installed"
    Write-Info $uv
} elseif ($DryRun) {
    Write-Step "uv not found"
    Write-Plan "would install uv $UvVersion from $UvInstaller"
    $uv = "uv"
} else {
    Write-Step "Installing uv $UvVersion (standalone; no Python needed)"
    # UV_NO_MODIFY_PATH: we call uv by absolute path, so there is no reason to
    # rewrite the user's PATH during an install they may cancel.
    $env:UV_NO_MODIFY_PATH = "1"
    try {
        Invoke-RestMethod $UvInstaller -UseBasicParsing | Invoke-Expression
    } catch {
        Fail "could not install uv from $UvInstaller ($($_.Exception.Message))" `
             "Check your internet connection, then re-run. uv is the only thing YBM needs to bootstrap."
    }
    $uv = Resolve-Uv
    if (-not $uv) {
        Fail "uv installed but could not be located" `
             "Looked in ~\.local\bin and %LOCALAPPDATA%\Programs\uv. Set YBM_UV_PATH and re-run."
    }
    Write-Good "uv at $uv"
}

# --- 2. Python, provided by uv -------------------------------------------
if ($DryRun) {
    Write-Plan "would run: uv python install $PythonVersion"
} else {
    Write-Step "Ensuring Python $PythonVersion (downloaded by uv, not from your system)"
    & $uv python install $PythonVersion
    if ($LASTEXITCODE -ne 0) {
        Fail "uv could not provide Python $PythonVersion" `
             "Re-run, or install Python $PythonVersion yourself and re-run - YBM will use it."
    }
}

# --- 3. The code: git if present, source zip if not ----------------------
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

# --- 4. Hand off to ybm.ps1 ----------------------------------------------
if ($DryRun) {
    Write-Plan "would run: $RepoDir\scripts\ybm.ps1 run"
    if ($Verify) { Write-Plan "would then run: ybm.ps1 doctor (--Verify)" }
    Write-Host ""
    Write-Host "Dry run complete - nothing was installed or changed." -ForegroundColor Yellow
    exit 0
}

Set-Location $RepoDir
Write-Step "Installing dependencies and starting YBM Control"
# `run` is already non-interactive - it is the double-click path - so -NoPrompt
# has nothing to suppress here and is not forwarded.
& "$RepoDir\scripts\ybm.ps1" run
if ($LASTEXITCODE -ne 0) {
    Fail "startup failed (exit $LASTEXITCODE)" `
         "Run '$RepoDir\scripts\ybm.ps1 doctor' to diagnose. Logs: $RepoDir\.agent_control\logs"
}

# --- 5. Optional post-install proof --------------------------------------
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
