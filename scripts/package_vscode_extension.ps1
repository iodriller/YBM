$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
Push-Location "$Root\vscode-extension"
try {
  npm install
  npm run compile
  npx @vscode/vsce package
}
finally {
  Pop-Location
}

