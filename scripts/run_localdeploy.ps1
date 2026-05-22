$ErrorActionPreference = "Stop"

$LocalDeployRoot = "C:\for fun\LocalDeploy"
if (-not (Test-Path -LiteralPath $LocalDeployRoot)) {
  throw "LocalDeploy root not found: $LocalDeployRoot"
}

$python = Join-Path $LocalDeployRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  $python = "python"
}

Set-Location $LocalDeployRoot
& $python "api_server.py"
