$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
Set-Location $Root
$env:PYTHONPATH = "$Root\backend\src"

python -m agent_control.cli poll-telegram
