$ErrorActionPreference = "Stop"

. "$PSScriptRoot\..\lib\common.ps1"
Set-Location $Script:YbmRoot
$env:PYTHONPATH = "$Script:YbmRoot\backend\src"

& (Get-YbmPython) -m streamlit run "$Script:YbmRoot\backend\src\agent_control\admin_streamlit.py" `
  --server.address 127.0.0.1 `
  --server.port 8501 `
  --server.headless true `
  --browser.gatherUsageStats false `
  --client.toolbarMode minimal
