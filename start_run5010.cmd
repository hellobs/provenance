@echo off
rem ===========================================================================
rem  Start a REAL case01 run on port 5010 (actually runs the simulation).
rem
rem  Difference from start_live5010.cmd:
rem    start_live5010.cmd  -> review-only: only replays existing records, no GPU.
rem    this file           -> full run: actually runs the sim (needs Ollama/GPU).
rem
rem  Branch is DECIDED, not configured: T0 runs first, then the AI's own
rem  answer decides which branch the timeline follows. There is no --branch/
rem  --branch-mode option any more (preset branches were removed 2026-10-10).
rem
rem  Why a separate file: 5010 is long-running, so start it yourself in your own
rem  window -- a process started in the background by the agent gets reclaimed
rem  between turns.
rem
rem  NOTE: keep this file ASCII-only. cmd.exe parses .cmd using the system ANSI
rem  code page (GBK on zh-CN); non-ASCII text here breaks command parsing.
rem ===========================================================================
setlocal
rem 2026-10-10:本地私密配置(Ethan 外部 API 等)在这里加载,密钥不进仓库。
if exist "%~dp0provenance\case01.env.local" call "%~dp0provenance\case01.env.local"
cd /d "%~dp0provenance"

set PY=%~dp0provenance\.venv-live\Scripts\python.exe
if not exist "%PY%" (
  echo [FAIL] interpreter not found: %PY%
  echo        Run setup.cmd first to prepare venv-live.
  pause
  exit /b 1
)

rem --- tunables (edit if needed) ---------------------------------------------
set NODES=0
set HOLD=1800
set SEED=
set RUN_ID=
rem ---------------------------------------------------------------------------

set EXTRA=
if not "%NODES%"==""  set EXTRA=%EXTRA% --nodes %NODES%
if not "%HOLD%"==""   set EXTRA=%EXTRA% --hold %HOLD%
if not "%SEED%"==""   set EXTRA=%EXTRA% --seed %SEED%
if not "%RUN_ID%"=="" set EXTRA=%EXTRA% --run-id %RUN_ID%

echo interpreter: %PY%
echo mode: FULL RUN (branch decided by the AI's T0 answer)
echo entry: http://127.0.0.1:5010/
echo stop: close this window, or run: python live_switch.py --stop case01
echo.

"%PY%" live_switch.py --start case01 %EXTRA%

echo.
echo [exited] If there is a [FAIL] line above, follow its hint.
pause
