$ErrorActionPreference = "Stop"

. "$PSScriptRoot\..\lib\common.ps1"
Set-Location $Script:YbmRoot
$env:PYTHONPATH = "$Script:YbmRoot\backend\src"

& (Get-YbmPython) -m agent_control.serve_backend
