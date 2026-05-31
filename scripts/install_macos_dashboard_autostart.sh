#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_TEMPLATE="$REPO_ROOT/scripts/macos/com.otismurray.stocknewsdashboard.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$TARGET_DIR/com.otismurray.stocknewsdashboard.plist"

mkdir -p "$TARGET_DIR"
sed "s#__REPO_ROOT__#$REPO_ROOT#g" "$PLIST_TEMPLATE" > "$TARGET_PLIST"
launchctl unload "$TARGET_PLIST" >/dev/null 2>&1 || true
launchctl load "$TARGET_PLIST"

echo "Installed LaunchAgent at $TARGET_PLIST"
echo "The dashboard will now start automatically at login and restart if it exits."
echo "Use '$REPO_ROOT/scripts/open_dashboard.command' to open it in your browser."
