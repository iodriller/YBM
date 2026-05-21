param(
  [string]$ApiId = $env:TELEGRAM_API_ID,
  [string]$ApiHash = $env:TELEGRAM_API_HASH,
  [string]$Session = ".agent_control/telegram_e2e_user"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path "$PSScriptRoot\.."
Set-Location $Root

if (-not $ApiId) {
  $ApiId = Read-Host "Telegram API ID"
}
if (-not $ApiHash) {
  $ApiHash = Read-Host "Telegram API Hash"
}

python e2e/telegram_login.py --api-id $ApiId --api-hash $ApiHash --session $Session
