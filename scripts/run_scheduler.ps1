$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
Set-Location (Join-Path $Root "backend")

python -m agent_control.cli run-scheduler
