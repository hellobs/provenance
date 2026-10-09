@echo off
rem ===========================================================================
rem  起 5010 实时面(case01,只翻已有记录,不占 GPU)
rem
rem  为什么单独有这个文件:5010 是常驻服务,必须由使用者在自己的窗口启动 ——
rem  agent 侧后台起的进程会被会话回收(同轮内活着,跨轮次就没了)。
rem  本文件用仓内 venv-live 解释器,不依赖 PATH 里的 python。
rem
rem  用法:双击本文件,窗口保持不关;看效果访问 http://127.0.0.1:5010/
rem        停:关掉窗口,或另开窗口跑 serve.cmd --stop
rem ===========================================================================
setlocal
chcp 65001 >nul 2>nul
cd /d "%~dp0"

set PY=%~dp0provenance\.venv-live\Scripts\python.exe
if not exist "%PY%" (
  echo [FAIL] 找不到 %PY%
  echo         请先跑 setup.cmd 准备 venv-live。
  pause
  exit /b 1
)

echo 解释器:%PY%
echo 入口:http://127.0.0.1:5010/(只翻记录,不重跑模拟)
echo 停止:直接关掉本窗口。
echo.

"%PY%" tools\serve_all.py --only 5010

echo.
echo [已退出] 若上面有 [FAIL] 行,请按提示排查。
pause
