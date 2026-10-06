@echo off
rem ===========================================================================
rem  One-command setup for a fresh machine (Windows)
rem
rem  Usage:  double-click this file          -> full setup
rem          setup.cmd --check               -> read-only self check
rem          setup.cmd --yes                 -> answer yes to everything
rem          setup.cmd --skip-models         -> skip the ~8GB Ollama download
rem
rem  This wrapper only finds a Python interpreter and runs
rem  tools\setup_all.py; all real logic and all messages live in that script
rem  (shared by Windows and macOS).
rem
rem  IMPORTANT: keep this file ASCII-only. cmd.exe parses .cmd in the console
rem  code page (GBK on zh-CN), so non-ASCII bytes here can be split into bogus
rem  commands and executed. Chinese output is produced by setup_all.py, and
rem  the code page is switched to UTF-8 below so it renders correctly.
rem ===========================================================================
setlocal
chcp 65001 >nul 2>nul
cd /d "%~dp0"

if not exist "tools\setup_all.py" (
  echo [FAIL] tools\setup_all.py not found. Run this file from the repository root.
  set RC=1
  goto finish
)

rem Prefer a real interpreter over the py launcher: on Windows "py -3" may pick
rem the Microsoft Store build, which redirects its own paths and is a poor host
rem for a venv. python.exe here is the ordinary installed interpreter.
where python >nul 2>nul
if not errorlevel 1 goto usepython
where py >nul 2>nul
if not errorlevel 1 goto usepy

echo.
echo [FAIL] No Python found. Install Python 3.12 or newer first:
echo        winget install Python.Python.3.12
echo        or https://www.python.org/downloads/  (tick "Add python.exe to PATH")
set RC=1
goto finish

:usepython
python -X utf8 "tools\setup_all.py" %*
goto done

:usepy
py -3 -X utf8 "tools\setup_all.py" %*
goto done

:done
set RC=%ERRORLEVEL%

:finish
echo.
if "%RC%"=="0" (
  echo [DONE] Everything is ready. Re-running this file is safe: finished steps are skipped.
) else (
  echo [INCOMPLETE] exit code %RC% -- read the [FAIL] lines above, fix, then re-run this file.
)
rem Pause only for a real double-click: no arguments AND the launch line carries the
rem full path of this file (that is what Explorer does; a terminal invocation does not).
if not "%~1"=="" goto nopause
echo %cmdcmdline% | find /i "%~f0" >nul
if errorlevel 1 goto nopause
pause
:nopause
exit /b %RC%
