param(
  [switch]$NoTelegram,
  [switch]$NoWorker,
  [switch]$NoAdminUi
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
$RunDir = Join-Path $Root ".agent_control\run"
$LogDir = Join-Path $Root ".agent_control\logs"
New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null

function Test-HttpOk {
  param([string]$Url)
  try {
    Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 2 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Test-Pid {
  param([string]$PidFile)
  if (-not (Test-Path -LiteralPath $PidFile)) {
    return $false
  }
  $processId = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue
  if (-not $processId) {
    return $false
  }
  return $null -ne (Get-Process -Id ([int]$processId) -ErrorAction SilentlyContinue)
}

function Stop-ProcessTree {
  param([int]$ProcessId)
  $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
  foreach ($child in $children) {
    Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
  }
  $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($process) {
    Stop-Process -Id $ProcessId -Force
  }
}

function Stop-OrphanProcessesForName {
  param([string]$Name)
  $pidFile = Join-Path $RunDir "$Name.pid"
  if (Test-Pid $pidFile) {
    return
  }

  $patterns = switch ($Name) {
    "backend" { @("run_backend.ps1", "uvicorn agent_control.main:app") }
    "worker" { @("run_worker.ps1", "agent_control.cli run-worker") }
    "telegram_polling" { @("run_telegram_polling.ps1", "agent_control.cli poll-telegram") }
    "admin_ui" { @("run_admin_ui.ps1", "admin_streamlit.py") }
    default { @() }
  }
  if (-not $patterns) {
    return
  }

  $rootPath = $Root.Path
  $candidates = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $commandLine = $_.CommandLine
    if (-not $commandLine) {
      return $false
    }
    foreach ($pattern in $patterns) {
      $matchesPattern = $commandLine -like "*$pattern*"
      $isCliWorker = $pattern -like "agent_control.cli*"
      if ($matchesPattern -and ($isCliWorker -or $commandLine -like "*$rootPath*")) {
        return $true
      }
    }
    return $false
  }
  foreach ($candidate in $candidates) {
    $process = Get-Process -Id ([int]$candidate.ProcessId) -ErrorAction SilentlyContinue
    if ($process) {
      Stop-ProcessTree -ProcessId $process.Id
      Write-Host "Stopped orphan $Name process (pid $($process.Id))"
    }
  }
}

function Start-StackScript {
  param(
    [string]$Name,
    [string]$ScriptPath,
    [switch]$Supervise
  )
  $pidFile = Join-Path $RunDir "$Name.pid"
  if (Test-Pid $pidFile) {
    Write-Host "$Name already running (pid $(Get-Content -LiteralPath $pidFile))"
    return
  }

  $out = Join-Path $LogDir "$Name.out.log"
  $err = Join-Path $LogDir "$Name.err.log"
  if ($Supervise) {
    $supervisor = Join-Path $Root "scripts\run_supervised.ps1"
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$supervisor`" -Name `"$Name`" -ScriptPath `"$ScriptPath`""
  } else {
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
  }
  $process = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList $arguments `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $out `
    -RedirectStandardError $err `
    -PassThru
  Set-Content -LiteralPath $pidFile -Value $process.Id
  $mode = if ($Supervise) { "supervised" } else { "direct" }
  Write-Host "Started $Name ($mode pid $($process.Id))"
}

& "$Root\scripts\start_localdeploy.ps1"
& "$Root\scripts\init_db.ps1"

Stop-OrphanProcessesForName -Name "backend"
if (Test-HttpOk "http://127.0.0.1:8765/health") {
  Write-Host "backend already running at http://127.0.0.1:8765"
} else {
  Start-StackScript -Name "backend" -ScriptPath "$Root\scripts\run_backend.ps1" -Supervise
  for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 1
    if (Test-HttpOk "http://127.0.0.1:8765/health") {
      Write-Host "backend is ready at http://127.0.0.1:8765"
      break
    }
  }
}

if (-not $NoTelegram) {
  Stop-OrphanProcessesForName -Name "telegram_polling"
  Start-StackScript -Name "telegram_polling" -ScriptPath "$Root\scripts\run_telegram_polling.ps1" -Supervise
}

if (-not $NoWorker) {
  Stop-OrphanProcessesForName -Name "worker"
  Start-StackScript -Name "worker" -ScriptPath "$Root\scripts\run_worker.ps1" -Supervise
}

if (-not $NoAdminUi) {
  Stop-OrphanProcessesForName -Name "admin_ui"
  if (Test-HttpOk "http://127.0.0.1:8501") {
    Write-Host "admin_ui already running at http://127.0.0.1:8501"
  } else {
    Start-StackScript -Name "admin_ui" -ScriptPath "$Root\scripts\run_admin_ui.ps1" -Supervise
  }
}

Write-Host ""
Write-Host "Admin UI: http://127.0.0.1:8501"
Write-Host "Legacy FastAPI admin: http://127.0.0.1:8765/admin"
Write-Host "Logs: $LogDir"
