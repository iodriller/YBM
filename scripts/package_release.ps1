# Stage and zip a release payload: everything a machine needs to RUN YBM,
# with the admin console already built.
#
# The point of this script is the Node.js requirement. A source checkout has no
# console until someone installs Node 22.22+ and builds it, which is the one
# prerequisite a non-developer cannot reasonably be asked to satisfy. Shipping
# backend/src/agent_control/static/admin inside the payload removes it: the
# release carries the built product, and Node becomes a contributor tool again.
#
# Run from the repo root, after `npm run build` in frontend/:
#
#   .\scripts\package_release.ps1 -Version 0.1.0
#
# Used by .github/workflows/release.yml, but deliberately runnable by hand so
# the packaging can be checked without cutting a tag.

[CmdletBinding()]
param(
    # Version string for the archive name. No "v" prefix.
    [Parameter(Mandatory = $true)]
    [string]$Version,
    # Where the .zip is written. Created if missing.
    [string]$OutputDir = "dist",
    # Where the staged tree is left, for the installer build to consume.
    [string]$StageDir
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$Console = Join-Path $RepoRoot "backend\src\agent_control\static\admin\index.html"

if (-not $StageDir) { $StageDir = Join-Path $RepoRoot "dist\payload" }
if (-not [IO.Path]::IsPathRooted($OutputDir)) { $OutputDir = Join-Path $RepoRoot $OutputDir }

# Fail loudly rather than shipping the exact problem this packaging exists to
# solve. A release without the console is worse than no release: it looks
# complete and serves build instructions.
if (-not (Test-Path -LiteralPath $Console)) {
    Write-Host "ERROR: the admin console is not built." -ForegroundColor Red
    Write-Host "  Expected: $Console" -ForegroundColor Yellow
    Write-Host "  Run 'npm ci && npm run build' in frontend/ first." -ForegroundColor Yellow
    exit 1
}

Write-Host "Staging YBM $Version" -ForegroundColor Cyan

if (Test-Path -LiteralPath $StageDir) { Remove-Item -LiteralPath $StageDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# Single files first.
foreach ($file in @("YBM.bat", "README.md", "LICENSE", "CHANGELOG.md")) {
    $source = Join-Path $RepoRoot $file
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $StageDir $file)
    }
}

# Directories, with the generated and developer-only trees left behind.
# backend/tests and the vscode extension are not runtime; .venv, node_modules,
# __pycache__, and .agent_control are per-machine state that must never be
# baked into a package handed to someone else.
$trees = @(
    @{ From = "backend\src";           To = "backend\src" },
    @{ From = "scripts";               To = "scripts" },
    @{ From = "whatsapp-bridge\src";   To = "whatsapp-bridge\src" }
)
foreach ($tree in $trees) {
    $from = Join-Path $RepoRoot $tree.From
    $to = Join-Path $StageDir $tree.To
    if (-not (Test-Path -LiteralPath $from)) { continue }
    New-Item -ItemType Directory -Force -Path $to | Out-Null
    # /MIR mirrors; /XD prunes directories anywhere in the tree. robocopy uses
    # exit codes 0-7 for success, so it needs explicit handling under
    # $ErrorActionPreference = "Stop".
    $null = robocopy $from $to /MIR /NFL /NDL /NJH /NJS /NP `
        /XD ".venv" "node_modules" "__pycache__" ".agent_control" ".pytest_cache" `
        /XF "*.pyc" "agent_control.db"
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed copying $($tree.From) (exit $LASTEXITCODE)" }
}

# Manifests that sit at a directory root, copied individually so the whole
# directory does not come along.
$rootFiles = @(
    @{ From = "backend\pyproject.toml";        To = "backend\pyproject.toml" },
    @{ From = "backend\uv.lock";               To = "backend\uv.lock" },
    @{ From = "config\config.example.yaml";    To = "config\config.example.yaml" },
    @{ From = "whatsapp-bridge\package.json";  To = "whatsapp-bridge\package.json" },
    @{ From = "whatsapp-bridge\package-lock.json"; To = "whatsapp-bridge\package-lock.json" }
)
foreach ($item in $rootFiles) {
    $source = Join-Path $RepoRoot $item.From
    if (-not (Test-Path -LiteralPath $source)) { continue }
    $target = Join-Path $StageDir $item.To
    New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target
}

# Records which release this tree came from, so `ybm check-updates` has a
# baseline in an installed copy that has no .git directory.
Set-Content -LiteralPath (Join-Path $StageDir ".ybm-release-version") -Value $Version -NoNewline

$stagedConsole = Join-Path $StageDir "backend\src\agent_control\static\admin\index.html"
if (-not (Test-Path -LiteralPath $stagedConsole)) {
    throw "the console did not survive staging - expected $stagedConsole"
}

$zipPath = Join-Path $OutputDir "YBM-$Version-windows.zip"
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Compress-Archive -Path (Join-Path $StageDir "*") -DestinationPath $zipPath

$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
$sizeMb = (Get-Item -LiteralPath $zipPath).Length / 1MB

Write-Host ""
Write-Host "Staged: $StageDir" -ForegroundColor Green
Write-Host "Archive: $zipPath" -ForegroundColor Green
Write-Host ("  {0:N1} MB   SHA256 {1}" -f $sizeMb, $hash) -ForegroundColor DarkGray

# robocopy reports success with exit codes 0-7 (1 means "files were copied"),
# and that value is still $LASTEXITCODE here. Without this the script "fails"
# every time it does its job, which would fail the release workflow.
exit 0
