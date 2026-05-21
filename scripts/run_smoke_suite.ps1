param(
    [string]$LogRoot = ".agent_control/smoke_runs"
)

$ErrorActionPreference = "Stop"

Write-Host "Running local smoke-oriented backend tests..."
$env:AGENT_SMOKE_LOG_ROOT = $LogRoot
python -m pytest backend/tests/test_capability_requirements_matrix.py backend/tests/test_artifact_delivery.py -q
