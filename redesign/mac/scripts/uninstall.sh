#!/usr/bin/env bash
set -euo pipefail

DEST="$HOME/yoteiban-watch"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
WATCH_LABEL="com.yoteiban.watch"
CLOCK_LABEL="com.yoteiban.clock"

launchctl bootout "$DOMAIN/$WATCH_LABEL" >/dev/null 2>&1 || true
launchctl bootout "$DOMAIN/$CLOCK_LABEL" >/dev/null 2>&1 || true
rm -f "$AGENTS/$WATCH_LABEL.plist" "$AGENTS/$CLOCK_LABEL.plist"
rm -rf "$DEST/app"
rm -f "$DEST/config.json" "$DEST/env"
printf '見張り番と時計を停止・削除しました。state は保持しています: %s/state\n' "$DEST"
