$ErrorActionPreference = "Stop"

. "$PSScriptRoot\..\lib\common.ps1"
Import-DotEnv

$LocalDeployRoot = $env:YBM_LOCALDEPLOY_ROOT
if (-not $LocalDeployRoot) {
  throw "YBM_LOCALDEPLOY_ROOT is not set. Add it to .env (see .env.example), or point llm.profiles in config/config.yaml at a different OpenAI-compatible endpoint and run 'ybm start -NoLocalDeploy'."
}
if (-not (Test-Path -LiteralPath $LocalDeployRoot)) {
  throw "LocalDeploy root not found: $LocalDeployRoot (check YBM_LOCALDEPLOY_ROOT in .env)"
}

$python = Join-Path $LocalDeployRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  $python = "python"
}

Set-Location $LocalDeployRoot
& $python "api_server.py"
