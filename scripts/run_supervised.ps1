param(
  [Parameter(Mandatory = $true)][string]$Name,
  [Parameter(Mandatory = $true)][string]$ScriptPath,
  [int]$RestartDelaySeconds = 5
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
$RunDir = Join-Path $Root ".agent_control\run"
$LogDir = Join-Path $Root ".agent_control\logs"
New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null

$StatusFile = Join-Path $RunDir "$Name.status.json"
$StopFile = Join-Path $RunDir "$Name.stop"
$RestartCount = 0

function Write-ServiceStatus {
  param(
    [string]$Status,
    [Nullable[int]]$ChildPid,
    [Nullable[int]]$LastExitCode,
    [string]$Message
  )
  $payload = [ordered]@{
    name = $Name
    status = $Status
    supervisor_pid = $PID
    child_pid = $ChildPid
    restart_count = $RestartCount
    last_exit_code = $LastExitCode
    message = $Message
    updated_at = (Get-Date).ToUniversalTime().ToString("o")
  }
  $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StatusFile -Encoding UTF8
}

if (Test-Path -LiteralPath $StopFile) {
  Remove-Item -LiteralPath $StopFile -Force
}

Write-ServiceStatus -Status "starting" -ChildPid $null -LastExitCode $null -Message "supervisor starting"

while (-not (Test-Path -LiteralPath $StopFile)) {
  $RestartCount += 1
  $childOut = Join-Path $LogDir "$Name.child.out.log"
  $childErr = Join-Path $LogDir "$Name.child.err.log"
  $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
  Write-ServiceStatus -Status "starting" -ChildPid $null -LastExitCode $null -Message "starting child process"
  $child = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList $arguments `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $childOut `
    -RedirectStandardError $childErr `
    -PassThru

  Write-ServiceStatus -Status "running" -ChildPid $child.Id -LastExitCode $null -Message "child process running"
  while (-not $child.HasExited -and -not (Test-Path -LiteralPath $StopFile)) {
    Start-Sleep -Seconds 5
    $child.Refresh()
    Write-ServiceStatus -Status "running" -ChildPid $child.Id -LastExitCode $null -Message "child process running"
  }

  if (-not $child.HasExited) {
    Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue
    Write-ServiceStatus -Status "stopped" -ChildPid $child.Id -LastExitCode $null -Message "stop requested"
    break
  }

  Write-ServiceStatus -Status "exited" -ChildPid $child.Id -LastExitCode $child.ExitCode -Message "child exited; restarting"
  Start-Sleep -Seconds $RestartDelaySeconds
}

Write-ServiceStatus -Status "stopped" -ChildPid $null -LastExitCode $null -Message "supervisor stopped"
