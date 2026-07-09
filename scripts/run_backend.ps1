$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
Set-Location $Root
$env:PYTHONPATH = "$Root\backend\src"

python -m uvicorn agent_control.main:app --host 127.0.0.1 --port 8765
