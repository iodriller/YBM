$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
$env:PYTHONPATH = "$Root\backend\src"

pytest "$Root\backend\tests"

