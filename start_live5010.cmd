@echo off
rem ===========================================================================
rem  Start the live UI on port 5010 (case01, replays existing records only).
rem
rem  Why a separate file: 5010 is a long-running service and must be started
rem  by the user in their own window -- a process started in the background by
rem  the agent gets reclaimed between turns (alive within the same turn, gone
rem  after that). This file uses the in-repo venv-live interpreter and does
rem  NOT depend on python from PATH.
rem
rem  NOTE: keep this file ASCII-only. cmd.exe parses .cmd files using the
rem  system ANSI code page (GBK on zh-CN), so non-ASCII text here gets
rem  mis-decoded and breaks command parsing.
rem
rem  Usage: double-click this file and keep the window open.
rem         Open http://127.0.0.1:5010/ to see the result.
rem         Stop: close the window, or run serve.cmd --stop elsewhere.
rem ===========================================================================
setlocal
cd /d "%~dp0"

set PY=%~dp0provenance\.venv-live\Scripts\python.exe
if not exist "%PY%" (
  echo [FAIL] interpreter not found: %PY%
  echo        Run setup.cmd first to prepare venv-live.
  pause
  exit /b 1
)

echo interpreter: %PY%
echo entry: http://127.0.0.1:5010/ (replay only, no re-run)
echo stop: close this window.
echo.

"%PY%" tools\serve_all.py --only 5010

echo.
echo [exited] If there is a [FAIL] line above, follow its hint.
pause
