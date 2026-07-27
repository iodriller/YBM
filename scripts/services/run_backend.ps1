$ErrorActionPreference = "Stop"

. "$PSScriptRoot\..\lib\common.ps1"
Set-Location $Script:YbmRoot
$env:PYTHONPATH = "$Script:YbmRoot\backend\src"

& (Get-YbmPython) -m uvicorn agent_control.main:app --host 127.0.0.1 --port 8765
