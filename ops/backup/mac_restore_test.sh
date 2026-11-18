#!/usr/bin/env bash
# Mac miniで：yoteiban-backup- の一番新しい控えを、一時的な場所に戻して中身の件数を見る（本番の台帳には触れない）。
# 秘密の鍵とパスフレーズはMacの外に出さない。パスフレーズはキーチェーンから読む。
# 使い方: mac_restore_test.sh <yoteiban-settings の取り出し場所>
set -euo pipefail
SETTINGS="${1:?yoteiban-settings の場所を渡してください}"
WORK="${YB_BACKUP_WORK:-$HOME/yoteiban-watch/backup-repo}"
PRIV="${YB_BACKUP_PRIVATE_KEY:-$HOME/yoteiban-keys/backup_private.pem}"
python3 -c "import cryptography" 2>/dev/null || { echo "python3 の cryptography がありません（確認表Bの準備で入れます）" >&2; exit 2; }
export GIT_SSH_COMMAND="ssh -i ${YB_BACKUP_DEPLOY_KEY:-$HOME/.ssh/yoteiban_backup_deploy} -o IdentitiesOnly=yes -o IdentityAgent=none"
git -C "$WORK" pull -q --ff-only origin main || true
latest=$(ls -1 "$WORK"/daily/*.enc | sort | tail -1)
TMP=$(mktemp -d); trap 'rm -rf "$TMP"; unset YB_RESTORE_PASS' EXIT
YB_RESTORE_PASS=$(security find-generic-password -a "$USER" -s yoteiban-backup-key -w); export YB_RESTORE_PASS
cd "$SETTINGS/core"
python3 -m yoteiban_core.backup restore --in "$latest" --private-key "$PRIV" --out-db "$TMP/restored.sqlite" --passphrase-env YB_RESTORE_PASS >/dev/null
python3 - "$TMP/restored.sqlite" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
for t in ("cases", "raw_inputs", "operations", "goal_nodes", "twin_prefs"):
    try: print(t, c.execute(f"select count(*) from {t}").fetchone()[0])
    except Exception as e: print(t, "なし")
print("integrity", c.execute("pragma integrity_check").fetchone()[0])
PY
echo "戻す試験：OK（一時ファイルは消しました）: $(basename "$latest")"
