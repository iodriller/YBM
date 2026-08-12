# Build the Windows installer (MSI) from a staged payload.
#
#   python scripts/package_release.py --version 0.1.0
#   .\scripts\build_msi.ps1 -Version 0.1.0
#
# Exists because `wix build` resolves paths in the .wxs relative to the .wxs
# file rather than the working directory, so the payload and icon have to be
# passed as absolute paths. Doing that here keeps the WiX definition readable
# and the build reproducible from any directory.
#
# WiX v5 on purpose: v6 introduced the Open Source Maintenance Fee, whose EULA
# has to be accepted before the binaries will run. v5 is the last release
# without that, and accepting a licence on a maintainer's behalf is not a
# packaging script's decision to make.

[CmdletBinding()]
param(
    # Product version. MSI requires plain numeric a.b.c, so any prerelease
    # suffix is stripped for the package and kept only in the file name.
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$PayloadDir = "dist\payload",
    [string]$OutputDir = "dist"
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
if (-not [IO.Path]::IsPathRooted($PayloadDir)) { $PayloadDir = Join-Path $RepoRoot $PayloadDir }
if (-not [IO.Path]::IsPathRooted($OutputDir)) { $OutputDir = Join-Path $RepoRoot $OutputDir }

if (-not (Test-Path -LiteralPath $PayloadDir)) {
    throw "no staged payload at $PayloadDir - run: python scripts/package_release.py --version $Version"
}

$wix = Get-Command wix -ErrorAction SilentlyContinue
if (-not $wix) {
    $candidate = Join-Path $HOME ".dotnet\tools\wix.exe"
    if (Test-Path -LiteralPath $candidate) {
        $wix = $candidate
    } else {
        throw "wix not found. Install it with: dotnet tool install --global wix --version 5.0.2"
    }
} else {
    $wix = $wix.Source
}

# "0.1.0-rc.1" is a valid release tag and an invalid MSI ProductVersion, which
# must be numeric. Keep the full string for the file name and give the package
# the numeric part.
$productVersion = ($Version -split "-")[0]
if ($productVersion -notmatch '^\d+(\.\d+){0,3}$') {
    throw "MSI needs a numeric version like 1.2.3; got '$productVersion' from '$Version'"
}

$icon = Join-Path $RepoRoot "scripts\assets\logo.ico"
$license = Join-Path $RepoRoot "packaging\windows\license.rtf"
$wxs = Join-Path $RepoRoot "packaging\windows\ybm.wxs"
$output = Join-Path $OutputDir "YBM-Setup.msi"
$generatedWxs = Join-Path $OutputDir "payload.generated.wxs"

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "Building $output" -ForegroundColor Cyan
Write-Host "  product version $productVersion (from $Version)" -ForegroundColor DarkGray

& python "$RepoRoot\scripts\generate_wix_payload.py" `
    --payload-dir $PayloadDir `
    --output $generatedWxs
if ($LASTEXITCODE -ne 0) { throw "MSI payload generation failed (exit $LASTEXITCODE)" }

# WixToolset.Util supplies WixShellExec, which is what launches YBM once the
# install finishes. Added per-build rather than assumed present so a clean
# machine (and the release runner) resolves it the same way.
& $wix extension add -g WixToolset.Util.wixext/5.0.2 2>&1 | Out-Null
& $wix extension add -g WixToolset.UI.wixext/5.0.2 2>&1 | Out-Null

& $wix build `
    -arch x64 `
    -ext WixToolset.Util.wixext `
    -ext WixToolset.UI.wixext `
    -culture en-US `
    -d "Version=$productVersion" `
    -d "IconFile=$icon" `
    -d "LicenseFile=$license" `
    -o $output `
    $wxs `
    $generatedWxs
if ($LASTEXITCODE -ne 0) { throw "wix build failed (exit $LASTEXITCODE)" }

# ICE91 assumes a package might switch to per-machine installation. YBM is
# intentionally per-user and always targets LocalAppData, so that warning is
# inapplicable. ICE61 warns about AllowSameVersionUpgrades, which is deliberate
# for rebuilding an alpha installer. Every other standard ICE remains active.
& $wix msi validate -sice ICE61 -sice ICE91 $output
if ($LASTEXITCODE -ne 0) { throw "MSI validation failed (exit $LASTEXITCODE)" }

$hash = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash
$sizeMb = (Get-Item -LiteralPath $output).Length / 1MB
Write-Host ""
Write-Host "Installer: $output" -ForegroundColor Green
Write-Host ("  {0:N1} MB   SHA256 {1}" -f $sizeMb, $hash) -ForegroundColor DarkGray
exit 0
