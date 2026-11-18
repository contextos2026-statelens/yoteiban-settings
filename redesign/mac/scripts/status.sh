#!/usr/bin/env bash
set -euo pipefail

DEST="$HOME/yoteiban-watch"
DOMAIN="gui/$(id -u)"
PYTHON_BIN="${YOTEIBAN_PYTHON:-$(command -v python3)}"
for LABEL in com.yoteiban.watch com.yoteiban.clock; do
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    printf '%s: 動作中\n' "$LABEL"
  else
    printf '%s: 停止中\n' "$LABEL"
  fi
done
"$PYTHON_BIN" - "$DEST/state" <<'PY'
import json, pathlib, sqlite3, sys
state = pathlib.Path(sys.argv[1])
for name in ("watch", "clock"):
    p = state / f"heartbeat_{name}.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        print(f"{name} 最終心拍: {data.get('at', '不明')} (ok={data.get('ok', False)})")
    except (OSError, ValueError):
        print(f"{name} 最終心拍: まだありません")
db = state / "watch.db"
try:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    n = con.execute("select count(*) from items where state in ('受け取り済み','仕分け済み','起こし失敗')").fetchone()[0]
    con.close()
except sqlite3.Error:
    n = 0
print(f"pending 件数: {n}")
PY
