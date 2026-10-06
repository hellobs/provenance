#!/usr/bin/env sh
# ============================================================================
#  新机一条命令配置(macOS / Linux)
#
#  用法:  ./setup.sh                 走完整流程
#         ./setup.sh --check         只读自检,不装任何东西
#         ./setup.sh --yes           所有询问自动继续
#         ./setup.sh --skip-models   不拉 Ollama 模型(省约 8GB 下载)
#
#  它只做一件事:找到 Python,把 tools/setup_all.py 跑起来;
#  真正的逻辑与所有提示都在那个脚本里(Windows / macOS 共用同一份)。
# ============================================================================
set -u
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3.12 python3.13 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    # 版本要 >= 3.8 才能跑这个引导脚本;venv 里另找 3.12+
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)' >/dev/null 2>&1; then
      PY="$c"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo
  echo "[失败] 没找到 Python 3.8+。请先安装 Python 3.12 或更高:"
  echo "        macOS:  brew install python@3.12"
  echo "        Linux:  sudo apt install python3.12 python3.12-venv"
  echo "        或到 https://www.python.org/downloads/ 下载"
  exit 1
fi

exec "$PY" tools/setup_all.py "$@"
