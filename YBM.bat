@echo off
rem The one file a non-developer should ever need to double-click
rem (docs/UI_UX_AUDIT.md Phase 10). Installs whatever's missing, does
rem nothing when there's nothing to do, and opens the console. Everything
rem real lives in scripts\ybm.ps1's "run" command - this is just the
rem double-clickable front door to it.
title YBM Control
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ybm.ps1" run
if %ERRORLEVEL% neq 0 (
  echo.
  echo Something went wrong - see the messages above.
  pause
) else (
  echo.
  echo YBM Control is running. This window can be closed.
  timeout /t 5 >nul
)
