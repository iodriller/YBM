$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
Set-Location $Root

$env:PYTHONPATH = "backend/src"
python -m agent_control.cli run-coding-session-watcher
