$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
Set-Location $Root
$env:PYTHONPATH = "$Root\backend\src"

python -m streamlit run "$Root\backend\src\agent_control\admin_streamlit.py" `
  --server.address 127.0.0.1 `
  --server.port 8501 `
  --server.headless true `
  --browser.gatherUsageStats false
