#!/usr/bin/env bash
# Mac miniで：届いた暗号化済みの控え（*.enc）だけを yoteiban-backup- （非公開・末尾ハイフン）に push する。
# yoteiban-backup- 専用の書き込み鍵（デプロイキー）だけを使い、ほかのGitHubのログインは使わない。
# 使い方: mac_backup_push.sh   （置き場は下の変数。固有値は ~/yoteiban-watch/backup.env に置く）
set -euo pipefail
ENV_FILE="${YB_BACKUP_ENV:-$HOME/yoteiban-watch/backup.env}"
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
: "${YB_BACKUP_REMOTE:?backup.env に YB_BACKUP_REMOTE（yoteiban-backup- の「Code → SSH」の接続先）を書いてください}"
KEY="${YB_BACKUP_DEPLOY_KEY:-$HOME/.ssh/yoteiban_backup_deploy}"
INBOX="${YB_BACKUP_INBOX:-$HOME/yoteiban-backup-inbox}"
WORK="${YB_BACKUP_WORK:-$HOME/yoteiban-watch/backup-repo}"
KEEP_DAILY="${YB_BACKUP_KEEP_DAILY:-14}"
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o IdentityAgent=none -o StrictHostKeyChecking=accept-new"
mkdir -p "$INBOX"; chmod 700 "$INBOX"
shopt -s nullglob
files=("$INBOX"/*.enc)
[ ${#files[@]} -eq 0 ] && { echo "送る控えはありません"; exit 0; }
for f in "${files[@]}"; do
  # 暗号化済みの形（見出し）だけを通す。平文のSQLiteは送らない
  if head -c 16 "$f" | grep -q "SQLite format"; then echo "平文の台帳が混ざっています。止めます: $f" >&2; exit 1; fi
  if [ -f "$f.sha256" ]; then
    want=$(tr -d '\n' < "$f.sha256"); got=$(shasum -a 256 "$f" | cut -d' ' -f1)
    [ "$want" = "$got" ] || { echo "指紋が合いません: $f" >&2; exit 1; }
  fi
done
if [ ! -d "$WORK/.git" ]; then
  git clone -q "$YB_BACKUP_REMOTE" "$WORK" 2>/dev/null || { mkdir -p "$WORK" && git -C "$WORK" init -q -b main && git -C "$WORK" remote add origin "$YB_BACKUP_REMOTE"; }
fi
cd "$WORK"
git pull -q --ff-only origin main 2>/dev/null || true
mkdir -p daily monthly
for f in "${files[@]}"; do
  base=$(basename "$f"); cp "$f" "daily/$base"; [ -f "$f.sha256" ] && cp "$f.sha256" "daily/$base.sha256"
  month=$(echo "$base" | sed -E 's/yoteiban-([0-9]{6}).*/\1/'); [ -e "monthly/$month.enc" ] || { cp "$f" "monthly/$month.enc"; }
done
# 日ごとの控えは新しい方から KEEP_DAILY 個だけ残す（月ごとは全部残す）
ls -1 daily/*.enc 2>/dev/null | sort -r | tail -n +$((KEEP_DAILY+1)) | while read -r old; do git rm -q -f "$old" "$old.sha256" 2>/dev/null || rm -f "$old" "$old.sha256"; done
git add -A daily monthly
if git diff --cached --quiet; then echo "変更なし"; else
  git -c user.name="yoteiban-backup" -c user.email="yoteiban-backup@localhost" commit -q -m "控え $(TZ=Asia/Tokyo date +%Y-%m-%dT%H:%M)"
  git push -q origin HEAD:main
fi
for f in "${files[@]}"; do rm -f "$f" "$f.sha256"; done
echo "push済み: ${#files[@]} 件"
