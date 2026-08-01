<#
.SYNOPSIS
  Single entry point for YBM: setup, doctor, start/stop/status/logs, test, e2e, db, config.
  Replaces the 15+ separate scripts that used to live here (see docs/HISTORY.md P1).

.EXAMPLE
  .\scripts\ybm.ps1 run
  .\scripts\ybm.ps1 setup
  .\scripts\ybm.ps1 doctor
  .\scripts\ybm.ps1 start -NoTelegram
  .\scripts\ybm.ps1 status
  .\scripts\ybm.ps1 logs worker -Follow
  .\scripts\ybm.ps1 test
  .\scripts\ybm.ps1 db inspect
  .\scripts\ybm.ps1 config set server.port 8765
#>
param(
  [Parameter(Position = 0)]
  [ValidateSet("run", "setup", "doctor", "start", "stop", "restart", "status", "logs", "test", "e2e", "e2e-login", "send", "trace", "scenario", "db", "config", "clean", "package-extension", "tray", "autostart", "backup", "check-updates", "help")]
  [string]$Command = "help",

  [Parameter(Position = 1)]
  [string]$Sub = $null,

  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Rest = @()
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\lib\common.ps1"
Import-DotEnv

function Show-YbmHelp {
  @"
YBM - local agentic control stack

  ybm run                      the one command: install/update what's missing, start, open the console
                                (double-click YBM.bat at the repo root for the same thing, no terminal)
  ybm setup                    create venv, install deps, bootstrap config/.env
  ybm doctor                   preflight: env, config, connectivity, ports
  ybm start [flags]            start the stack (runs doctor first)
    -NoTelegram -NoWorker -NoScheduler -NoLocalDeploy -SkipDoctor -Open
  ybm stop                     stop all YBM background processes
  ybm restart [flags]          stop then start
  ybm status                   show per-service status and health
  ybm logs <service> [-Follow] tail a service's log (backend, worker, ...)
  ybm test [pytest-args]       run backend/tests
  ybm e2e [args]                passthrough to scripts/run_all_e2e_tests.py
  ybm db inspect|clean|reset   inspect / prune / wipe the local database
  ybm config show              print effective config
  ybm config set <path> <val>  set a dotted config path (e.g. server.port 8765)
  ybm clean [flags]            wipe generated artifacts (-Caches -Workspaces -AdapterProposals -AllGenerated)
  ybm e2e-login                bootstrap the Telethon user session for live E2E checks
  ybm send "<message>"         send one ad-hoc message through the full pipeline and trace it
  ybm trace <task_id> [--json] full post-mortem for one task - reads the DB directly, no running backend needed
  ybm scenario record <name> [--profile <name>]
                                re-record a scenario fixture against a live LLM (real API calls, may cost money)
  ybm package-extension        build the VS Code bridge extension .vsix
  ybm tray                     launch the system tray icon (Open Admin Console / Start / Stop / Status)
  ybm autostart enable|disable|status
                                run the tray icon automatically at login (per-user Startup folder shortcut)
  ybm backup [--out <dir>]     zip the database, config.yaml, .env, and secret vault (default: .agent_control/backups)
  ybm check-updates            compare the installed version against the latest GitHub release (read-only)
"@ | Write-Host
}

function Get-YbmAutostartShortcutPath {
  Join-Path ([Environment]::GetFolderPath("Startup")) "YBM Control.lnk"
}

function Invoke-YbmAutostart {
  param([string]$Sub)
  $shortcutPath = Get-YbmAutostartShortcutPath
  switch ($Sub) {
    "enable" {
      $shell = New-Object -ComObject WScript.Shell
      $shortcut = $shell.CreateShortcut($shortcutPath)
      $shortcut.TargetPath = Get-YbmPythonW
      $shortcut.Arguments = "`"$Script:YbmRoot\scripts\tray_app.py`""
      $shortcut.WorkingDirectory = $Script:YbmRoot
      $shortcut.Description = "YBM Control tray icon"
      $shortcut.Save()
      Write-Host "Autostart enabled - $shortcutPath will launch the tray icon at login."
      Write-Host "Launching it now too, so you don't have to log out and back in..."
      Start-Process -FilePath (Get-YbmPythonW) -ArgumentList "`"$Script:YbmRoot\scripts\tray_app.py`"" -WorkingDirectory $Script:YbmRoot
    }
    "disable" {
      if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath -Force
        Write-Host "Autostart disabled - removed $shortcutPath."
      } else {
        Write-Host "Autostart was not enabled (no shortcut at $shortcutPath)."
      }
    }
    "status" {
      if (Test-Path -LiteralPath $shortcutPath) {
        Write-Host "Autostart: enabled ($shortcutPath)"
      } else {
        Write-Host "Autostart: disabled"
      }
    }
    default {
      Write-Host "usage: ybm autostart enable | disable | status"
      exit 1
    }
  }
}

# NOTE: none of the helper functions below use a parameter named "Args" -
# that collides with PowerShell's automatic $args variable and silently
# discards whatever is passed in (confirmed the hard way - see git history).
# Use $Argv instead.

function Invoke-YbmSetup {
  param([string[]]$Argv)

  if (-not (Test-Path -LiteralPath $Script:YbmVenvPython)) {
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvCmd) {
      throw "uv is not installed. Install it from https://docs.astral.sh/uv/ then re-run '.\scripts\ybm.ps1 setup'."
    }
    Write-Host "Creating backend\.venv via uv sync (first run can take a minute)..."
    Push-Location (Join-Path $Script:YbmRoot "backend")
    try {
      # Keep this extras list identical to scripts/install.sh's uv sync line -
      # they drifted before (install.sh only had "--extra dev", silently
      # skipping pytest/telethon/voice/desktop on a fresh Linux/macOS install).
      # "dev" (ruff) is included so a fresh `ybm setup` can actually run the
      # `uv run --frozen ruff check .` step AGENTS.md/CONTRIBUTING.md document.
      $extraArgs = @("--extra", "test", "--extra", "e2e", "--extra", "voice", "--extra", "tray", "--extra", "dev")
      if ($Argv -notcontains "--no-desktop") {
        $extraArgs += @("--extra", "desktop")
      }
      & uv sync @extraArgs
      if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed (exit $LASTEXITCODE)"
      }
    } finally {
      Pop-Location
    }
  } else {
    Write-Host "backend\.venv already exists - skipping venv creation (run 'uv sync' in backend/ to update deps)."
  }

  $telegramToken = $null
  for ($i = 0; $i -lt $Argv.Count; $i++) {
    if ($Argv[$i] -eq "--telegram-token" -and $i + 1 -lt $Argv.Count) {
      $telegramToken = $Argv[$i + 1]
    }
  }

  $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
  $pyArgs = @("-m", "agent_control.cli", "setup")
  if ($telegramToken) {
    $pyArgs += @("--telegram-token", $telegramToken)
  }
  & (Get-YbmPython) @pyArgs
  # Deliberately no `exit` here (there used to be one) - Invoke-YbmRun calls
  # this as a sub-step and needs to keep going afterward. $LASTEXITCODE from
  # the python call above is left set in the caller's scope either way; the
  # "setup" dispatch case below is what turns it into a process exit code
  # when this runs as the top-level command.
}

function Invoke-YbmRun {
  # The one command a non-developer should ever need (docs/UI_UX_AUDIT.md
  # Phase 10): install whatever's missing, do nothing when there's nothing
  # to do, and start the console. Wrapped by the double-clickable YBM.bat
  # at the repo root, so "run this file" is the entire instruction.
  #
  # Every step below is already idempotent on its own - this is an
  # orchestration wrapper, not new install/start logic:
  #   - Invoke-YbmSetup already no-ops past venv creation once .venv
  #     exists, and config_manager already leaves an existing config.yaml
  #     alone.
  #   - `uv sync` is fast and does nothing when the lockfile hasn't moved
  #     since last time - run unconditionally so a venv from before this
  #     session's own new dependency (pystray, added for the tray icon)
  #     actually picks it up, which "venv exists -> skip" alone would not.
  #   - Invoke-YbmStart already runs doctor preflight and is the one that
  #     actually opens the browser (-Open).
  Write-Host "YBM Control" -ForegroundColor Cyan
  Write-Host "==========="
  Write-Host ""

  Write-Host "Checking install..." -ForegroundColor Cyan
  Invoke-YbmSetup -Argv @()
  if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Setup failed (exit $LASTEXITCODE) - see the message above." -ForegroundColor Red
    exit $LASTEXITCODE
  }

  $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
  if ($uvCmd) {
    Push-Location (Join-Path $Script:YbmRoot "backend")
    try {
      # No output redirection here on purpose: uv writes routine progress
      # ("Resolved N packages...") to stderr, and *>/2>&1 redirection
      # under this script's $ErrorActionPreference = "Stop" turns that
      # into a fatal NativeCommandError even on success - confirmed live,
      # not a hypothetical (this exact line halted `ybm run` the first
      # time). Letting it print normally avoids the whole gotcha.
      & uv sync --extra test --extra e2e --extra voice --extra tray --extra dev --extra desktop
    } finally {
      Pop-Location
    }
  }

  Write-Host ""
  Write-Host "Checking for updates..." -ForegroundColor Cyan
  $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
  & (Get-YbmPython) -m agent_control.cli check-updates
  # Deliberately informational only, never auto-applied: pulling and
  # restarting onto new, unreviewed code without being asked is an
  # external-write action this script doesn't take on its own - same
  # reasoning as `ybm check-updates` itself (docs/UI_UX_AUDIT.md Phase 6).

  Write-Host ""
  Write-Host "Starting..." -ForegroundColor Cyan
  Invoke-YbmStart -Argv @("-Open")
}

function Invoke-YbmDoctor {
  $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
  & (Get-YbmPython) -m agent_control.cli doctor
  # $LASTEXITCODE survives the function return; callers read it directly.
  # Do NOT wrap this call (or the caller's call to this function) in
  # parens/Out-Null - that captures the child's stdout instead of letting
  # it stream to the console, which silently swallows every doctor line.
}

function Stop-YbmOrphansForName {
  param([string]$Name)
  $pidFile = Join-Path $Script:YbmRunDir "$Name.pid"
  if (Test-YbmPidAlive $pidFile) {
    return
  }
  $patterns = switch ($Name) {
    "backend" { @("run_backend.ps1", "uvicorn agent_control.main:app") }
    "localdeploy" { @("run_localdeploy.ps1", "api_server.py") }
    "worker" { @("run_worker.ps1", "agent_control.cli run-worker") }
    "coding_session_watcher" { @("run_coding_session_watcher.ps1", "agent_control.cli run-coding-session-watcher") }
    "scheduler" { @("run_scheduler.ps1", "agent_control.cli run-scheduler") }
    "telegram_polling" { @("run_telegram_polling.ps1", "agent_control.cli poll-telegram") }
    default { @() }
  }
  if (-not $patterns) {
    return
  }
  $rootPath = $Script:YbmRoot
  $candidates = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $commandLine = $_.CommandLine
    if (-not $commandLine) { return $false }
    foreach ($pattern in $patterns) {
      $matchesPattern = $commandLine -like "*$pattern*"
      $isCliWorker = $pattern -like "agent_control.cli*"
      $isLocalDeploy = $Name -eq "localdeploy" -and $commandLine -like "*LocalDeploy*"
      if ($matchesPattern -and ($isCliWorker -or $isLocalDeploy -or $commandLine -like "*$rootPath*")) {
        return $true
      }
    }
    return $false
  }
  foreach ($candidate in $candidates) {
    $process = Get-Process -Id ([int]$candidate.ProcessId) -ErrorAction SilentlyContinue
    if ($process) {
      Stop-YbmProcessTree -ProcessId $process.Id
      Write-Host "Stopped orphan $Name process (pid $($process.Id))"
    }
  }
}

function Start-YbmService {
  param(
    [string]$Name,
    [string]$ScriptPath,
    [string]$ReadyUrl = $null,
    [int]$ReadyTimeoutSeconds = 30,
    [bool]$Required = $true
  )

  $pidFile = Join-Path $Script:YbmRunDir "$Name.pid"
  if (Test-YbmPidAlive $pidFile) {
    $existingPid = Get-Content -LiteralPath $pidFile
    return [pscustomobject]@{ Status = "ready"; Required = $Required; Detail = "already running (pid $existingPid)" }
  }

  Stop-YbmOrphansForName -Name $Name
  if ($ReadyUrl -and (Test-YbmHttpOk -Url $ReadyUrl -TimeoutSec 3)) {
    return [pscustomobject]@{ Status = "ready"; Required = $Required; Detail = "already running (external), reachable at $ReadyUrl" }
  }

  $out = Join-Path $Script:YbmLogDir "$Name.out.log"
  $err = Join-Path $Script:YbmLogDir "$Name.err.log"
  Remove-Item -LiteralPath (Join-Path $Script:YbmRunDir "$Name.status.json") -ErrorAction SilentlyContinue

  $supervisor = Join-Path $Script:YbmRoot "scripts\run_supervised.ps1"
  $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$supervisor`" -Name `"$Name`" -ScriptPath `"$ScriptPath`""
  $process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WorkingDirectory $Script:YbmRoot `
    -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
  Set-Content -LiteralPath $pidFile -Value $process.Id

  # Truthful readiness: poll for a real signal instead of assuming success
  # the instant Start-Process returns (see docs/HISTORY.md P0).
  $deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
    if ($ReadyUrl -and (Test-YbmHttpOk -Url $ReadyUrl -TimeoutSec 2)) {
      return [pscustomobject]@{ Status = "ready"; Required = $Required; Detail = "reachable at $ReadyUrl (pid $($process.Id))" }
    }
    $status = Read-YbmServiceStatus -Name $Name
    if ($status -and $status.status -eq "failed") {
      return [pscustomobject]@{ Status = "failed"; Required = $Required; Detail = "$($status.message) - see .agent_control\logs\$Name.child.err.log" }
    }
    if (-not $ReadyUrl -and $status -and $status.status -eq "running") {
      return [pscustomobject]@{ Status = "running"; Required = $Required; Detail = "process running (pid $($status.child_pid))" }
    }
  }

  $status = Read-YbmServiceStatus -Name $Name
  if ($status -and $status.status -eq "failed") {
    return [pscustomobject]@{ Status = "failed"; Required = $Required; Detail = "$($status.message)" }
  }
  if ($ReadyUrl) {
    return [pscustomobject]@{ Status = "warn"; Required = $Required; Detail = "started (pid $($process.Id)) but not reachable at $ReadyUrl within ${ReadyTimeoutSeconds}s - check logs" }
  }
  return [pscustomobject]@{ Status = "running"; Required = $Required; Detail = "started (pid $($process.Id))" }
}

function Invoke-YbmStart {
  param([string[]]$Argv)

  $noTelegram = $Argv -contains "-NoTelegram"
  $noWorker = $Argv -contains "-NoWorker"
  $noScheduler = $Argv -contains "-NoScheduler"
  $noLocalDeploy = $Argv -contains "-NoLocalDeploy"
  $skipDoctor = $Argv -contains "-SkipDoctor"
  # Only the installers pass this - never a bare `ybm.ps1 start`, which
  # would otherwise pop a nuisance browser tab on every dev restart.
  $openBrowser = $Argv -contains "-Open"

  if (-not $skipDoctor) {
    Write-Host "Preflight (ybm doctor)..."
    Write-Host ""
    Invoke-YbmDoctor
    if ($LASTEXITCODE -ne 0) {
      Write-Host ""
      Write-Host "Preflight failed. Fix the [FAIL] items above, run '.\scripts\ybm.ps1 setup', or pass -SkipDoctor to start anyway." -ForegroundColor Red
      exit 1
    }
    Write-Host ""
  }

  New-Item -ItemType Directory -Force -Path $Script:YbmRunDir, $Script:YbmLogDir | Out-Null

  $results = [ordered]@{}

  if (-not $noLocalDeploy) {
    $results["localdeploy"] = Start-YbmService -Name "localdeploy" -ScriptPath (Join-Path $Script:YbmRoot "scripts\services\run_localdeploy.ps1") `
      -ReadyUrl "http://127.0.0.1:8000/health" -ReadyTimeoutSeconds 30 -Required $false
  }

  $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
  & (Get-YbmPython) -m agent_control.cli init-db | Out-Null

  $results["backend"] = Start-YbmService -Name "backend" -ScriptPath (Join-Path $Script:YbmRoot "scripts\services\run_backend.ps1") `
    -ReadyUrl "http://127.0.0.1:8765/health" -ReadyTimeoutSeconds 45 -Required $true

  if (-not $noTelegram) {
    $results["telegram_polling"] = Start-YbmService -Name "telegram_polling" -ScriptPath (Join-Path $Script:YbmRoot "scripts\services\run_telegram_polling.ps1") -Required $true
  }
  if (-not $noWorker) {
    $results["worker"] = Start-YbmService -Name "worker" -ScriptPath (Join-Path $Script:YbmRoot "scripts\services\run_worker.ps1") -Required $true
    $results["coding_session_watcher"] = Start-YbmService -Name "coding_session_watcher" -ScriptPath (Join-Path $Script:YbmRoot "scripts\services\run_coding_session_watcher.ps1") -Required $true
  }
  if (-not $noScheduler) {
    $results["scheduler"] = Start-YbmService -Name "scheduler" -ScriptPath (Join-Path $Script:YbmRoot "scripts\services\run_scheduler.ps1") -Required $true
  }

  Write-Host ""
  Write-Host "Startup summary:"
  $width = ($results.Keys | Measure-Object -Property Length -Maximum).Maximum
  $hardFailure = $false
  foreach ($name in $results.Keys) {
    $r = $results[$name]
    $symbol = switch ($r.Status) {
      "ready" { "[OK]  " }
      "running" { "[OK]  " }
      "warn" { "[WARN]" }
      default { "[FAIL]" }
    }
    if ($r.Status -eq "failed" -and $r.Required) {
      $hardFailure = $true
    }
    Write-Host "$symbol $($name.PadRight($width))  $($r.Detail)"
  }
  Write-Host ""
  if ($hardFailure) {
    Write-Host "One or more required services failed to start. See .agent_control\logs, or run '.\scripts\ybm.ps1 logs <service>'." -ForegroundColor Red
    exit 1
  }
  $adminUrl = "http://127.0.0.1:8765/admin"
  Write-Host "Admin UI:        $adminUrl"
  Write-Host "Logs:            $Script:YbmLogDir"
  if ($openBrowser) {
    # Carries AGENT_ADMIN_TOKEN (if set) as a one-time ?token= URL param so
    # the browser's very first request is already authenticated - a fresh
    # install always generates a real token (bootstrap.run_setup), and
    # nothing else in the UI collects one from the user. lib/api.ts strips
    # it from the URL/history on load. Already loaded into $env: by this
    # script's own top-level Import-DotEnv call.
    $token = $env:AGENT_ADMIN_TOKEN
    $target = if ($token) { "${adminUrl}?token=$token" } else { $adminUrl }
    Start-Process $target
  }
}

function Invoke-YbmStop {
  if (-not (Test-Path -LiteralPath $Script:YbmRunDir)) {
    Write-Host "No stack pid directory found."
  } else {
    foreach ($pidFile in Get-ChildItem -LiteralPath $Script:YbmRunDir -Filter "*.pid") {
      $name = [System.IO.Path]::GetFileNameWithoutExtension($pidFile.Name)
      $stopFile = Join-Path $Script:YbmRunDir "$name.stop"
      New-Item -ItemType File -Force -Path $stopFile | Out-Null
      $processId = Get-Content -LiteralPath $pidFile.FullName -ErrorAction SilentlyContinue
      if ($processId) {
        $process = Get-Process -Id ([int]$processId) -ErrorAction SilentlyContinue
        if ($process) {
          Stop-YbmProcessTree -ProcessId $process.Id
          Write-Host "Stopped $name (pid $processId)"
        }
      }
      Remove-Item -LiteralPath $pidFile.FullName -Force
      Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue
    }
  }
  foreach ($name in $Script:YbmServiceOrder) {
    Stop-YbmOrphansForName -Name $name
  }
}

function Invoke-YbmStatus {
  Write-Host "Service".PadRight(24) "Status".PadRight(10) "Detail"
  foreach ($name in $Script:YbmServiceOrder) {
    $status = Read-YbmServiceStatus -Name $name
    if (-not $status) {
      Write-Host "$($name.PadRight(24)) $("not running".PadRight(10))"
      continue
    }
    # A status.json can outlive its process if the supervisor was killed
    # before it could write a final "stopped" state - verify the pid is
    # actually alive rather than trusting the file (see docs/HISTORY.md P6).
    $displayStatus = $status.status
    if ($status.child_pid -and $displayStatus -eq "running" -and -not (Get-Process -Id ([int]$status.child_pid) -ErrorAction SilentlyContinue)) {
      $displayStatus = "stale"
    }
    Write-Host "$($name.PadRight(24)) $($displayStatus.PadRight(10)) $($status.message) (pid $($status.child_pid), restarts $($status.restart_count))"
  }
  Write-Host ""
  foreach ($check in @(
    @{ Name = "LocalDeploy"; Url = "http://127.0.0.1:8000/health" },
    @{ Name = "Backend"; Url = "http://127.0.0.1:8765/health" },
    @{ Name = "Admin UI"; Url = "http://127.0.0.1:8765/admin" }
  )) {
    $ok = Test-YbmHttpOk -Url $check.Url -TimeoutSec 3
    $mark = if ($ok) { "[OK]  " } else { "[DOWN]" }
    Write-Host "$mark $($check.Name.PadRight(14)) $($check.Url)"
  }
}

function Invoke-YbmLogs {
  param([string]$Service, [string[]]$Argv)
  if (-not $Service) {
    Write-Host "usage: ybm logs <service> [-Follow] [-Tail N]"
    Write-Host "services: $($Script:YbmServiceOrder -join ', ')"
    return
  }
  $follow = $Argv -contains "-Follow"
  $tail = 40
  for ($i = 0; $i -lt $Argv.Count; $i++) {
    if ($Argv[$i] -eq "-Tail" -and $i + 1 -lt $Argv.Count) {
      $tail = [int]$Argv[$i + 1]
    }
  }
  $childOut = Join-Path $Script:YbmLogDir "$Service.child.out.log"
  $childErr = Join-Path $Script:YbmLogDir "$Service.child.err.log"
  $out = if (Test-Path -LiteralPath $childOut) { $childOut } else { Join-Path $Script:YbmLogDir "$Service.out.log" }
  $err = if (Test-Path -LiteralPath $childErr) { $childErr } else { Join-Path $Script:YbmLogDir "$Service.err.log" }
  if (-not (Test-Path -LiteralPath $out) -and -not (Test-Path -LiteralPath $err)) {
    Write-Host "No logs found for '$Service' yet."
    return
  }
  Write-Host "=== $out ==="
  if (Test-Path -LiteralPath $out) {
    Get-Content -LiteralPath $out -Tail $tail -Wait:$follow
  }
  if (-not $follow -and (Test-Path -LiteralPath $err)) {
    $errContent = Get-Content -LiteralPath $err -Tail $tail -ErrorAction SilentlyContinue
    if ($errContent) {
      Write-Host ""
      Write-Host "=== $err ==="
      $errContent | Write-Host
    }
  }
}

function Invoke-YbmTest {
  param([string[]]$Argv)
  $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
  # The wrapper imports the repo's .env at startup for normal operator
  # commands. The unit suite creates isolated TestClient instances that do
  # not send the real local admin token, so carrying it into pytest turns
  # otherwise-valid admin API tests into blanket 401s. Individual auth tests
  # set their own token explicitly with monkeypatch.
  $savedAdminToken = $env:AGENT_ADMIN_TOKEN
  Remove-Item Env:AGENT_ADMIN_TOKEN -ErrorAction SilentlyContinue
  & (Get-YbmPython) -m pytest "$Script:YbmRoot\backend\tests" @Argv
  $testExitCode = $LASTEXITCODE
  if ($null -ne $savedAdminToken) {
    $env:AGENT_ADMIN_TOKEN = $savedAdminToken
  }
  exit $testExitCode
}

function Invoke-YbmE2e {
  param([string[]]$Argv)
  $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
  & (Get-YbmPython) "$Script:YbmRoot\scripts\run_all_e2e_tests.py" @Argv
  exit $LASTEXITCODE
}

function Invoke-YbmDb {
  param([string]$Sub, [string[]]$Argv)
  $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
  switch ($Sub) {
    "inspect" { & (Get-YbmPython) -m agent_control.cli db-inspect }
    "clean" {
      $days = 30
      for ($i = 0; $i -lt $Argv.Count; $i++) {
        if ($Argv[$i] -eq "--days" -and $i + 1 -lt $Argv.Count) { $days = $Argv[$i + 1] }
      }
      & (Get-YbmPython) -m agent_control.cli db-clean --days $days
    }
    "reset" {
      $pyArgs = @("-m", "agent_control.cli", "db-reset")
      if ($Argv -contains "--yes") { $pyArgs += "--yes" }
      & (Get-YbmPython) @pyArgs
    }
    default {
      Write-Host "usage: ybm db inspect | clean [--days N] | reset [--yes]"
      exit 1
    }
  }
  exit $LASTEXITCODE
}

function Invoke-YbmConfig {
  param([string]$Sub, [string[]]$Argv)
  $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
  switch ($Sub) {
    "show" { & (Get-YbmPython) -m agent_control.cli config-summary }
    "set" {
      if ($Argv.Count -lt 2) {
        Write-Host "usage: ybm config set <dotted.path> <value>"
        exit 1
      }
      & (Get-YbmPython) -m agent_control.cli config-set $Argv[0] $Argv[1]
    }
    default {
      Write-Host "usage: ybm config show | set <dotted.path> <value>"
      exit 1
    }
  }
  exit $LASTEXITCODE
}

switch ($Command) {
  "help" { Show-YbmHelp }
  "setup" { Invoke-YbmSetup -Argv (@($Sub) + $Rest | Where-Object { $_ }); exit $LASTEXITCODE }
  "run" { Invoke-YbmRun }
  "doctor" { Invoke-YbmDoctor; exit $LASTEXITCODE }
  "start" { Invoke-YbmStart -Argv (@($Sub) + $Rest | Where-Object { $_ }) }
  "stop" { Invoke-YbmStop }
  "restart" {
    $restartArgv = @($Sub) + $Rest | Where-Object { $_ }
    Invoke-YbmStop
    Start-Sleep -Seconds 2
    Invoke-YbmStart -Argv $restartArgv
  }
  "status" { Invoke-YbmStatus }
  "logs" { Invoke-YbmLogs -Service $Sub -Argv $Rest }
  "test" { Invoke-YbmTest -Argv (@($Sub) + $Rest | Where-Object { $_ }) }
  "e2e" { Invoke-YbmE2e -Argv (@($Sub) + $Rest | Where-Object { $_ }) }
  "db" { Invoke-YbmDb -Sub $Sub -Argv $Rest }
  "config" { Invoke-YbmConfig -Sub $Sub -Argv $Rest }
  "clean" {
    # clean_agent_control.ps1's params are all [switch]. Splatting an ARRAY of
    # "-Caches"-shaped strings does NOT re-parse them as flags - PowerShell
    # passes each element positionally instead, and every param here is a
    # switch with no positional slot, so every flag was silently dropped
    # ("ybm clean -Caches" printed the "choose at least one switch" usage
    # message no matter what flag was passed). Splatting a HASHTABLE does
    # bind correctly to named/switch parameters, so build one from the raw
    # "-Name" tokens instead.
    $cleanSwitches = @{}
    foreach ($arg in (@($Sub) + $Rest | Where-Object { $_ })) {
      if ($arg -match '^-(\w+)$') {
        $cleanSwitches[$matches[1]] = $true
      }
    }
    & "$Script:YbmRoot\scripts\clean_agent_control.ps1" @cleanSwitches
    exit $LASTEXITCODE
  }
  "e2e-login" {
    # Was a separate scripts/login_telegram_e2e.ps1 whose only job was to
    # prompt for two values and shell out; it also called bare `python`
    # rather than the venv interpreter, so it used whatever Python happened
    # to be on PATH (usually not the one with telethon installed).
    $apiId = $env:TELEGRAM_API_ID
    $apiHash = $env:TELEGRAM_API_HASH
    $session = if ($Sub) { $Sub } else { ".agent_control/telegram_e2e_user" }
    if (-not $apiId) { $apiId = Read-Host "Telegram API ID" }
    if (-not $apiHash) { $apiHash = Read-Host "Telegram API Hash" }
    if (-not $apiId -or -not $apiHash) {
      Write-Host "TELEGRAM_API_ID and TELEGRAM_API_HASH are required (get them from https://my.telegram.org)."
      exit 1
    }
    Set-Location $Script:YbmRoot
    & (Get-YbmPython) "$Script:YbmRoot\e2e\telegram_login.py" `
      --api-id $apiId --api-hash $apiHash --session $session
    exit $LASTEXITCODE
  }
  "send" {
    if (-not $Sub) {
      Write-Host 'usage: ybm send "<message>"'
      exit 1
    }
    $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
    & (Get-YbmPython) "$Script:YbmRoot\scripts\test_e2e.py" $Sub
    exit $LASTEXITCODE
  }
  "package-extension" {
    & "$Script:YbmRoot\scripts\package_vscode_extension.ps1"
    exit $LASTEXITCODE
  }
  "tray" {
    & (Get-YbmPython) "$Script:YbmRoot\scripts\tray_app.py"
    exit $LASTEXITCODE
  }
  "autostart" {
    Invoke-YbmAutostart -Sub $Sub
  }
  "backup" {
    $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
    & (Get-YbmPython) -m agent_control.cli backup @Rest
    exit $LASTEXITCODE
  }
  "check-updates" {
    $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
    & (Get-YbmPython) -m agent_control.cli check-updates
    exit $LASTEXITCODE
  }
  "trace" {
    if (-not $Sub) {
      Write-Host 'usage: ybm trace <task_id> [--json]'
      exit 1
    }
    $env:PYTHONPATH = "$Script:YbmRoot\backend\src"
    & (Get-YbmPython) -m agent_control.cli trace-task $Sub @Rest
    exit $LASTEXITCODE
  }
  "scenario" {
    if ($Sub -ne "record" -or $Rest.Count -lt 1) {
      Write-Host 'usage: ybm scenario record <name> [--profile <name>]'
      exit 1
    }
    & (Get-YbmPython) "$Script:YbmRoot\backend\tests\scenario\record.py" @Rest
    exit $LASTEXITCODE
  }
  default { Show-YbmHelp }
}
