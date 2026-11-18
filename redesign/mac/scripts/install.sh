#!/usr/bin/env bash
set -euo pipefail

MAC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/yoteiban-watch"
AGENTS="$HOME/Library/LaunchAgents"
TEMPLATE="$MAC_DIR/launchd.plist.template"
PYTHON_BIN="${YOTEIBAN_PYTHON:-$(command -v python3)}"
WATCH_LABEL="com.yoteiban.watch"
CLOCK_LABEL="com.yoteiban.clock"
DOMAIN="gui/$(id -u)"

mkdir -p "$DEST/app" "$DEST/state" "$AGENTS"
cp -R "$MAC_DIR/yoteiban_watch" "$DEST/app/"
if [[ ! -e "$DEST/config.json" ]]; then
  cp "$MAC_DIR/config.example.json" "$DEST/config.json"
fi
if [[ ! -e "$DEST/env" ]]; then
  cat > "$DEST/env" <<'EOF'
# 値は本人が設定する。ここには環境変数名だけを置く。
YOTEIBAN_WEBHOOK_URL=
YOTEIBAN_WEBHOOK_KEY=
YOTEIBAN_AI_KEY=
EOF
fi
chmod 600 "$DEST/config.json" "$DEST/env"

render_plist() {
  local label="$1" mode_arg="$2" log_name="$3" output="$4"
  "$PYTHON_BIN" - "$TEMPLATE" "$output" "$label" "$HOME" "$PYTHON_BIN" "$mode_arg" "$log_name" <<'PY'
import html, pathlib, sys
source, output, label, home, py, mode, log_name = sys.argv[1:]
text = pathlib.Path(source).read_text(encoding="utf-8")
values = {"{LABEL}": label, "{HOME}": home, "{PY}": py,
          "{MODE_ARG}": mode, "{LOG_NAME}": log_name}
for key, value in values.items():
    text = text.replace(key, html.escape(value, quote=False))
if "{" in text or "}" in text:
    raise SystemExit("plist template has unresolved placeholders")
pathlib.Path(output).write_text(text, encoding="utf-8")
PY
  chmod 600 "$output"
}

WATCH_PLIST="$AGENTS/$WATCH_LABEL.plist"
CLOCK_PLIST="$AGENTS/$CLOCK_LABEL.plist"
render_plist "$WATCH_LABEL" "" "watch" "$WATCH_PLIST"
render_plist "$CLOCK_LABEL" "--clock-only" "clock" "$CLOCK_PLIST"

launchctl bootout "$DOMAIN/$WATCH_LABEL" >/dev/null 2>&1 || true
launchctl bootout "$DOMAIN/$CLOCK_LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "$DOMAIN" "$WATCH_PLIST"
launchctl bootstrap "$DOMAIN" "$CLOCK_PLIST"
printf '見張り番と時計を読み込みました。設定: %s/config.json\n' "$DEST"
