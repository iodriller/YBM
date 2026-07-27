# Thin shim for muscle memory - the real implementation is scripts/ybm.ps1.
& "$PSScriptRoot\ybm.ps1" stop
exit $LASTEXITCODE
