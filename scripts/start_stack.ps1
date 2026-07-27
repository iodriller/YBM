# Thin shim for muscle memory - the real implementation is scripts/ybm.ps1.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest = @())
& "$PSScriptRoot\ybm.ps1" start @Rest
exit $LASTEXITCODE
