#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
if [[ "${1:-}" == "--dry-run" ]]; then
  echo 'dry-run: Readerコード配置とログイン項目登録を行う予定です。設定・環境ファイルは変更しません。変更はありません。'
  exit 0
fi
if [[ "${1:-}" != "使う" ]]; then
  read -r -p '通知取得アプリを使いますか？「使う」と入力してください: ' choice
  [[ "$choice" == "使う" ]] || { echo '変更せず終了しました。'; exit 0; }
fi

APP="$HOME/yoteiban-watch/reminder_reader"
APP_ROOT="$HOME/yoteiban-watch/app"
STATE="$HOME/yoteiban-watch/state"
PYTHON_BIN="$(bash "$ROOT/redesign/mac/scripts/select_python.sh" "$HOME/yoteiban-watch")"
mkdir -p "$APP"
mkdir -p "$APP_ROOT/yoteiban_watch" "$APP_ROOT/yoteiban_core" "$APP_ROOT/scripts" "$STATE/backups"
if [[ ! -e "$HOME/yoteiban-watch/config.json" ]]; then
  cp "$ROOT/redesign/mac/config.example.json" "$HOME/yoteiban-watch/config.json"
fi
if [[ ! -e "$HOME/yoteiban-watch/env" ]]; then
  cat > "$HOME/yoteiban-watch/env" <<'ENVFILE'
# 値は本人がMac上で設定する。鍵やURLはチャットに貼らない。
YOTEIBAN_WEBHOOK_URL=
YOTEIBAN_WEBHOOK_KEY=
YOTEIBAN_WEBHOOK_AUTH=
YOTEIBAN_WEBHOOK_FORMAT=envelope
YOTEIBAN_AI_KEY=
ENVFILE
fi
chmod 600 "$HOME/yoteiban-watch/config.json" "$HOME/yoteiban-watch/env"
STAMP="$(date +%Y%m%d%H%M%S)"
if [[ -f "$APP/reader.py" ]]; then cp -p "$APP/reader.py" "$STATE/backups/reminder-reader.$STAMP.py"; fi
if [[ -f "$APP/run.sh" ]]; then cp -p "$APP/run.sh" "$STATE/backups/reminder-reader-run.$STAMP.sh"; fi
cp "$ROOT/redesign/mac/reminder_reader/reader.py" "$APP/reader.py"
for module in waker heartbeat; do
  if [[ -f "$APP_ROOT/yoteiban_watch/$module.py" ]]; then cp -p "$APP_ROOT/yoteiban_watch/$module.py" "$STATE/backups/$module.$STAMP.py"; fi
  cp "$ROOT/redesign/mac/yoteiban_watch/$module.py" "$APP_ROOT/yoteiban_watch/$module.py"
done
touch "$APP_ROOT/yoteiban_watch/__init__.py"
if [[ -f "$APP_ROOT/yoteiban_core/reminder_kind.py" ]]; then cp -p "$APP_ROOT/yoteiban_core/reminder_kind.py" "$STATE/backups/reminder_kind.$STAMP.py"; fi
cp "$ROOT/core/yoteiban_core/reminder_kind.py" "$APP_ROOT/yoteiban_core/reminder_kind.py"
cp "$ROOT/redesign/mac/scripts/select_python.sh" "$APP_ROOT/scripts/select_python.sh"
chmod 700 "$APP_ROOT/scripts/select_python.sh"
printf '%s\n' "$PYTHON_BIN" > "$STATE/python-path"
chmod 600 "$STATE/python-path"
cat > "$APP/run.sh" <<'RUNNER'
#!/bin/bash
set -euo pipefail
ROOT="$HOME/yoteiban-watch"
if [[ -f "$ROOT/env" ]]; then
  set -a
  source "$ROOT/env"
  set +a
fi
: "${YOTEIBAN_WEBHOOK_FORMAT:=envelope}"
export YOTEIBAN_WEBHOOK_FORMAT
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="$ROOT/app:$ROOT/reminder_reader${PYTHONPATH:+:$PYTHONPATH}"
PYTHON_BIN="$(bash "$ROOT/app/scripts/select_python.sh" "$ROOT")"
exec "$PYTHON_BIN" "$ROOT/reminder_reader/reader.py" --config "$ROOT/config.json"
RUNNER
chmod 700 "$APP/run.sh" "$APP/reader.py"

# Stop the legacy service first so it cannot overlap the Login Item.
launchctl bootout "gui/$(id -u)/com.yoteiban.reminder-notifications" 2>/dev/null || true
launchctl disable "gui/$(id -u)/com.yoteiban.reminder-notifications" 2>/dev/null || true

osascript - "$APP/run.sh" <<'APPLESCRIPT'
on run argv
  tell application "System Events"
    set scriptPath to item 1 of argv
    try
      delete login item "Yoteiban Reminder Reader"
    end try
    make login item at end with properties {name:"Yoteiban Reminder Reader", path:scriptPath, hidden:true}
  end tell
end run
APPLESCRIPT
echo 'ログイン項目を登録しました。com.yoteiban.watch は変更していません。'
