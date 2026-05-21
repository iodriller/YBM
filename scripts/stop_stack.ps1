$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
$RunDir = Join-Path $Root ".agent_control\run"

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

function Stop-StackOrphans {
  $rootPath = (Resolve-Path "$PSScriptRoot\..").Path
  $candidates = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $commandLine = $_.CommandLine
    if (-not $commandLine) {
      return $false
    }
    if (
      $commandLine -like "*agent_control.cli poll-telegram*" -or
      $commandLine -like "*agent_control.cli run-worker*" -or
      $commandLine -like "*agent_control.cli run-scheduler*"
    ) {
      return $true
    }
    $commandLine -like "*$rootPath*" -and (
      $commandLine -like "*admin_streamlit.py*" -or
      $commandLine -like "*run_admin_ui.ps1*" -or
      $commandLine -like "*run_backend.ps1*" -or
      $commandLine -like "*run_telegram_polling.ps1*" -or
      $commandLine -like "*run_worker.ps1*" -or
      $commandLine -like "*run_scheduler.ps1*" -or
      $commandLine -like "*uvicorn agent_control.main:app*"
    )
  }
  foreach ($candidate in $candidates) {
    $process = Get-Process -Id ([int]$candidate.ProcessId) -ErrorAction SilentlyContinue
    if ($process) {
      Stop-ProcessTree -ProcessId $process.Id
      Write-Host "Stopped orphan stack process (pid $($process.Id))"
    }
  }
}

if (-not (Test-Path -LiteralPath $RunDir)) {
  Stop-StackOrphans
  Write-Host "No stack pid directory found."
  return
}

foreach ($pidFile in Get-ChildItem -LiteralPath $RunDir -Filter "*.pid") {
  $name = [System.IO.Path]::GetFileNameWithoutExtension($pidFile.Name)
  $processId = Get-Content -LiteralPath $pidFile.FullName -ErrorAction SilentlyContinue
  if ($processId) {
    $process = Get-Process -Id ([int]$processId) -ErrorAction SilentlyContinue
    if ($process) {
      Stop-ProcessTree -ProcessId $process.Id
      Write-Host "Stopped $name (pid $processId)"
    }
  }
  Remove-Item -LiteralPath $pidFile.FullName -Force
}

Stop-StackOrphans
