@echo off
rem YBM - first-time setup, no terminal required.
rem
rem Keep this file inside the fully extracted YBM folder, then double-click it.
rem It provides Python through uv, installs project dependencies, and opens YBM.
rem Node.js 22.22+ is needed to build the web console from source.
rem
rem After this, use YBM.bat instead: same idempotent behaviour, and it is what
rem the repo root is for. This file exists because a first-time user should not
rem have to open PowerShell and paste a command to get started.
rem
rem Flags are passed straight through, so you can also run:
rem   YBM-Setup.cmd --dry-run     show what would happen, change nothing
rem   YBM-Setup.cmd --verify      prove the install works before returning
setlocal EnableExtensions

set "HERE=%~dp0"
set "PS=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not exist "%PS%" set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

set "PSARGS="
:parse
if "%~1"=="" goto run
if /I "%~1"=="--dry-run" set "PSARGS=%PSARGS% -DryRun"
if /I "%~1"=="--verify"  set "PSARGS=%PSARGS% -Verify"
if /I "%~1"=="--no-prompt" set "PSARGS=%PSARGS% -NoPrompt"
shift
goto parse

:run
echo.
echo   YBM - setup
echo   ===================
echo.

rem Run the installer sitting next to this file when there is one (the normal
rem case: you already have the folder). -ExecutionPolicy Bypass is scoped to
rem this one process, not written to the machine.
if exist "%HERE%scripts\install.ps1" (
  "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HERE%scripts\install.ps1" %PSARGS%
) else (
  echo   scripts\install.ps1 not found next to this file.
  echo   Run YBM-Setup.cmd from inside the YBM folder.
  echo.
  pause
  exit /b 1
)

set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo   Setup did not finish cleanly ^(exit %RC%^).
  echo   The messages above say what went wrong and what to do next.
) else (
  echo   Done. Next time, double-click YBM.bat.
)
echo.
rem Always pause: a double-clicked window closes instantly otherwise, taking
rem the error message with it.
pause
exit /b %RC%
