$ErrorActionPreference = "Stop"

$LocalDeployRoot = "C:\for fun\LocalDeploy"
$HealthUrl = "http://127.0.0.1:8000/health"

function Test-LocalDeploy {
  try {
    $response = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 1
    return $null -ne $response
  } catch {
    return $false
  }
}

function Test-LocalDeployPort {
  $client = [System.Net.Sockets.TcpClient]::new()
  try {
    $connect = $client.BeginConnect("127.0.0.1", 8000, $null, $null)
    if (-not $connect.AsyncWaitHandle.WaitOne(500)) {
      return $false
    }
    $client.EndConnect($connect)
    return $client.Connected
  } catch {
    return $false
  } finally {
    $client.Close()
  }
}

if ((Test-LocalDeploy) -or (Test-LocalDeployPort)) {
  Write-Host "LocalDeploy is already running or listening at http://127.0.0.1:8000"
  return
}

if (-not (Test-Path -LiteralPath $LocalDeployRoot)) {
  throw "LocalDeploy root not found: $LocalDeployRoot"
}

$python = Join-Path $LocalDeployRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  $python = "python"
}

Write-Host "Starting LocalDeploy Gemma 3 API server..."
Start-Process -FilePath $python -ArgumentList "api_server.py" -WorkingDirectory $LocalDeployRoot -WindowStyle Hidden

for ($i = 0; $i -lt 90; $i++) {
  Start-Sleep -Seconds 1
  if (Test-LocalDeploy) {
    Write-Host "LocalDeploy is ready at $HealthUrl"
    return
  }
  if (Test-LocalDeployPort) {
    Write-Host "LocalDeploy is listening at http://127.0.0.1:8000"
    return
  }
}

throw "LocalDeploy did not become ready at $HealthUrl"
