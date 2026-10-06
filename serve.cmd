@echo off
rem ===========================================================================
rem  One command to bring the provenance faces up (Windows)
rem
rem  Usage:  double-click this file        -> start 5010 (case01, review-only) + 5002 + 5003 + 5020
rem          serve.cmd --all               -> also start 8060 (config tool, WRITE face)
rem          serve.cmd --status            -> only check, change nothing
rem          serve.cmd --stop              -> stop the faces THIS script started
rem          serve.cmd --full              -> 5010 runs a real simulation (needs Ollama/GPU)
rem
rem  All real logic lives in tools\serve_all.py (shared with macOS/Linux).
rem
rem  IMPORTANT: keep this file ASCII-only. cmd.exe parses .cmd in the console code
rem  page (GBK on zh-CN); non-ASCII bytes here can be split into bogus commands and
rem  executed. Chinese output comes from setup/serve scripts; the code page is
rem  switched to UTF-8 below so it renders.
rem ===========================================================================
setlocal
chcp 65001 >nul 2>nul
cd /d "%~dp0"

if not exist "tools\serve_all.py" (
  echo [FAIL] tools\serve_all.py not found. Run this file from the repository root.
  set RC=1
  goto finish
)

rem Prefer an ordinary interpreter over the py launcher (the Store build redirects
rem its own paths and is a poor host for a venv).
where python >nul 2>nul
if not errorlevel 1 goto usepython
where py >nul 2>nul
if not errorlevel 1 goto usepy

echo.
echo [FAIL] No Python found. Install Python 3.12 or newer, then run setup.cmd first.
set RC=1
goto finish

:usepython
python -X utf8 "tools\serve_all.py" %*
goto done

:usepy
py -3 -X utf8 "tools\serve_all.py" %*
goto done

:done
set RC=%ERRORLEVEL%

:finish
echo.
if "%RC%"=="0" (
  echo [DONE] Open http://127.0.0.1:5010/   Stop with: serve.cmd --stop
) else (
  echo [INCOMPLETE] exit code %RC% -- read the [FAIL] lines above.
)
rem Pause only for a real double-click: no arguments AND the launch line carries the
rem full path of this file (what Explorer does; a terminal invocation does not).
if not "%~1"=="" goto nopause
echo %cmdcmdline% | find /i "%~f0" >nul
if errorlevel 1 goto nopause
pause
:nopause
exit /b %RC%
