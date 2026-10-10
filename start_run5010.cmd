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
rem Local private config (external API keys etc.) lives in a gitignored .cmd; keys never enter the repo.
if exist "%~dp0provenance\case01.local.cmd" call "%~dp0provenance\case01.local.cmd"
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
set SEED=20261015
set RUN_ID=
rem ---------------------------------------------------------------------------

set EXTRA=
if not "%NODES%"==""  set EXTRA=%EXTRA% --nodes %NODES%
if not "%HOLD%"==""   set EXTRA=%EXTRA% --hold %HOLD%
if not "%SEED%"==""   set EXTRA=%EXTRA% --seed %SEED%
if not "%RUN_ID%"=="" set EXTRA=%EXTRA% --run-id %RUN_ID%

rem --- config tool (mavis config_tool, port 8060) ---------------------------
rem The 5010 top nav links to 8060, but that is a SEPARATE process nobody starts,
rem so the link is dead. Start it here (background, output redirected to a file).
rem Path is relative on purpose -- do NOT hard-code a drive letter.
rem
rem !! Never put ( or ) inside an echo that sits in a parenthesised block:
rem    the ) closes the if/else early and BOTH branches run (2026-10-10: the first
rem    real run of this file printed "config tool: ...8060/" AND "[skip] not found"
rem    one after the other). Keep the echo text paren-free.
rem NOTE: keep this block ASCII-only (see the header note about the ANSI code page).
set CFG=%~dp0..\mavis\config_tool
if exist "%CFG%\app.py" (
  if not exist "%TEMP%\dsh_srv" mkdir "%TEMP%\dsh_srv" 2>nul
  start "config_tool 8060" /min "%PY%" "%CFG%\app.py" > "%TEMP%\dsh_srv\config_tool.out" 2>&1
  echo config tool: http://127.0.0.1:8060/  log: %TEMP%\dsh_srv\config_tool.out
) else (
  echo config tool: [skip] not found at %CFG% -- the top-nav link will not open
)
rem ---------------------------------------------------------------------------

echo interpreter: %PY%
echo mode: FULL RUN (branch decided by the AI's T0 answer)
rem The produced record's seed fields come from SEED/RUN_ID below -- say what they will be,
rem because an empty SEED silently means "no seed" while the runbook answers "seed was set".
if "%SEED%"=="" echo [warn] SEED is empty -^> run.json gets manifest.seed=null and NO rng.json
if "%RUN_ID%"=="" echo [note] RUN_ID is empty -^> every round mints a new name, no stable deep link
if not "%SEED%"=="" echo seed: %SEED%
if not "%RUN_ID%"=="" echo run-id: %RUN_ID%
echo entry: http://127.0.0.1:5010/
echo stop: close this window, or run: python live_switch.py --stop case01
echo.

"%PY%" live_switch.py --start case01 %EXTRA%

echo.
echo [exited] If there is a [FAIL] line above, follow its hint.
pause
