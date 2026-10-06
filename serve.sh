#!/usr/bin/env sh
# ============================================================================
#  One command to bring the provenance faces up (macOS / Linux)
#
#  Usage:  ./serve.sh                start 5010 (case01, review-only) + 5002 + 5003 + 5020
#          ./serve.sh --all          also start 8060 (config tool, WRITE face)
#          ./serve.sh --status       only check, change nothing
#          ./serve.sh --stop         stop the faces this script started
#          ./serve.sh --full         5010 runs a real simulation (needs Ollama/GPU)
#
#  All real logic lives in tools/serve_all.py (shared with Windows).
# ============================================================================
set -u
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3.12 python3.13 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)' >/dev/null 2>&1; then
      PY="$c"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo
  echo "[FAIL] No Python 3.8+ found. Install Python 3.12+ and run ./setup.sh first."
  exit 1
fi

exec "$PY" tools/serve_all.py "$@"
