#!/usr/bin/env bash
# 箱で毎晩：台帳を公開鍵で暗号化した控えを作り、Macへ渡す箱を用意する（箱の外には何も送らない）。
# 使い方: box_snapshot.sh <台帳DB> <公開鍵PEM> <送り出し用フォルダ> <coreのパス>
set -euo pipefail
DB="$1"; PUB="$2"; OUTBOX="$3"; CORE="$4"
mkdir -p "$OUTBOX"; chmod 700 "$OUTBOX"
NAME="yoteiban-$(TZ=Asia/Tokyo date +%Y%m%d-%H%M).enc"
cd "$CORE"
python3 -m yoteiban_core.backup snapshot --db "$DB" --out "$OUTBOX/$NAME" --public-key "$PUB"
# 平文が混ざっていないかの最後の確かめ（SQLiteの見出しがあれば止める）
if head -c 16 "$OUTBOX/$NAME" | grep -q "SQLite format"; then echo "平文の台帳です。止めます" >&2; rm -f "$OUTBOX/$NAME"*; exit 1; fi
python3 -m yoteiban_core.backup rotate --dir "$OUTBOX" --keep-daily 3 --keep-monthly 0 >/dev/null
echo "$OUTBOX/$NAME"
