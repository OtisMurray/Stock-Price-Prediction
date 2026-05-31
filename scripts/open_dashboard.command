#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
SERVER_URL="http://127.0.0.1:8000/"
LOG_FILE="$REPO_ROOT/tmp/dashboard_server.log"

mkdir -p "$REPO_ROOT/tmp"

is_server_up() {
  curl -sf "$SERVER_URL" >/dev/null 2>&1
}

if ! is_server_up; then
  nohup "$PYTHON_BIN" "$REPO_ROOT/src/other/watchlist_dashboard.py" >"$LOG_FILE" 2>&1 &
  for _ in {1..30}; do
    if is_server_up; then
      break
    fi
    sleep 1
  done
fi

open "$SERVER_URL"
