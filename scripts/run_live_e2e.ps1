param(
  [string[]]$Case,
  [string[]]$Tag,
  [switch]$All,
  [switch]$IncludeGuarded,
  [switch]$DryRun,
  [string]$BackendUrl = "http://127.0.0.1:8765",
  [string]$LogRoot = ".agent_control/live_e2e_runs"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path "$PSScriptRoot\.."
Set-Location $Root

$argsList = @(
  "e2e/live_telegram_e2e.py",
  "--backend-url", $BackendUrl,
  "--log-root", $LogRoot
)

foreach ($item in $Case) {
  $argsList += @("--case", $item)
}
foreach ($item in $Tag) {
  $argsList += @("--tag", $item)
}
if ($All) {
  $argsList += "--all"
}
if ($IncludeGuarded) {
  $argsList += "--include-guarded"
}
if ($DryRun) {
  $argsList += "--dry-run"
}

python @argsList
