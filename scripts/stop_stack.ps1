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

if (-not (Test-Path -LiteralPath $RunDir)) {
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
