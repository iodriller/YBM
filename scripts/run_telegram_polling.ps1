$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
$env:PYTHONPATH = "$Root\backend\src"

& "$Root\scripts\start_localdeploy.ps1"

python -m agent_control.cli poll-telegram
