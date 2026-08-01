# Shared helpers for scripts/ybm.ps1 and the internal run_*.ps1 launchers.
# Not a user-facing entry point - dot-source this, don't run it directly.

$Script:YbmRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
$Script:YbmVenvPython = Join-Path $Script:YbmRoot "backend\.venv\Scripts\python.exe"
$Script:YbmRunDir = Join-Path $Script:YbmRoot ".agent_control\run"
$Script:YbmLogDir = Join-Path $Script:YbmRoot ".agent_control\logs"

function Get-YbmPython {
  if (Test-Path -LiteralPath $Script:YbmVenvPython) {
    return $Script:YbmVenvPython
  }
  Write-Warning "backend\.venv not found - falling back to 'python' on PATH. Run '.\scripts\ybm.ps1 setup' first."
  return "python"
}

function Get-YbmPythonW {
  # Windowless variant (no console flash) - for the tray app specifically,
  # which is meant to run detached from a shortcut, not a terminal.
  $pythonw = Join-Path (Split-Path $Script:YbmVenvPython -Parent) "pythonw.exe"
  if (Test-Path -LiteralPath $pythonw) {
    return $pythonw
  }
  Write-Warning "backend\.venv\Scripts\pythonw.exe not found - falling back to 'pythonw' on PATH. Run '.\scripts\ybm.ps1 setup' first."
  return "pythonw"
}

function Import-DotEnv {
  param([string]$Path = (Join-Path $Script:YbmRoot ".env"))
  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }
  foreach ($line in Get-Content -LiteralPath $Path) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
      continue
    }
    $parts = $trimmed.Split("=", 2)
    $key = $parts[0].Trim()
    $value = $parts[1].Trim().Trim('"')
    if (-not (Test-Path "Env:$key")) {
      Set-Item -Path "Env:$key" -Value $value
    }
  }
}

function Test-YbmHttpOk {
  param([string]$Url, [int]$TimeoutSec = 5)
  try {
    Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec $TimeoutSec | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Test-YbmPidAlive {
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

function Stop-YbmProcessTree {
  param([int]$ProcessId)
  $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
  foreach ($child in $children) {
    Stop-YbmProcessTree -ProcessId ([int]$child.ProcessId)
  }
  $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($process) {
    Stop-Process -Id $ProcessId -Force
  }
}

function Read-YbmServiceStatus {
  param([string]$Name)
  $path = Join-Path $Script:YbmRunDir "$Name.status.json"
  if (-not (Test-Path -LiteralPath $path)) {
    return $null
  }
  try {
    # Status files are written by run_supervised.ps1 with -Encoding UTF8, which
    # on Windows PowerShell prepends a BOM; utf-8-sig strips it on read.
    $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    return $raw | ConvertFrom-Json
  } catch {
    return $null
  }
}

# All service names started by `ybm start`, in dependency order.
$Script:YbmServiceOrder = @(
  "localdeploy", "backend", "telegram_polling", "worker",
  "coding_session_watcher", "scheduler"
)
