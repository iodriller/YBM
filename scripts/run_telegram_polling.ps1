$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
$env:PYTHONPATH = "$Root\backend\src"

python -m agent_control.cli poll-telegram
