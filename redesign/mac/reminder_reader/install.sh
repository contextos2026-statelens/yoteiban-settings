#!/bin/bash
set -euo pipefail

if [[ "${1:-}" != "使う" ]]; then
  read -r -p '通知取得アプリを使いますか？「使う」と入力してください: ' choice
  [[ "$choice" == "使う" ]] || { echo '変更せず終了しました。'; exit 0; }
fi

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
APP="$HOME/yoteiban-watch/reminder_reader"
mkdir -p "$APP"
cp "$ROOT/redesign/mac/reminder_reader/reader.py" "$APP/reader.py"
mkdir -p "$HOME/yoteiban-watch/app/yoteiban_core"
cp "$ROOT/core/yoteiban_core/reminder_kind.py" "$HOME/yoteiban-watch/app/yoteiban_core/reminder_kind.py"
cat > "$APP/run.sh" <<'RUNNER'
#!/bin/bash
set -euo pipefail
ROOT="$HOME/yoteiban-watch"
if [[ -f "$ROOT/env" ]]; then
  set -a
  source "$ROOT/env"
  set +a
fi
export YOTEIBAN_WEBHOOK_FORMAT=envelope
export PYTHONPATH="$ROOT/app:$ROOT/reminder_reader${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$ROOT/reminder_reader/reader.py" --config "$ROOT/config.json"
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
