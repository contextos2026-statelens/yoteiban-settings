"""質問回答と稼働接続を別々に記録する、秘密を含まない状態形式。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from datetime import datetime, timezone

LEGACY_STATES = {"not_requested", "pending_user_action", "checking", "connected", "degraded", "needs_attention", "disabled"}
STATES = {"未設定", "利用しない", "設定準備中", "本人の操作待ち", "接続テスト待ち", "接続成功", "接続失敗", "再確認が必要"}
LEGACY_MAP = {"not_requested":"未設定", "pending_user_action":"本人の操作待ち", "checking":"接続テスト待ち",
              "connected":"接続成功", "degraded":"接続失敗", "needs_attention":"再確認が必要", "disabled":"利用しない"}


def update_status(path: str | Path, route: str, state: str, *, detail_code: str = "",
                  step: str = "", at: str | None = None) -> dict:
    if not isinstance(route, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", route):
        raise ValueError("unknown integration id")
    if state in LEGACY_STATES:
        legacy = state
        state = LEGACY_MAP[state]
    elif state in STATES:
        legacy = next((old for old,new in LEGACY_MAP.items() if new == state), "")
    else:
        raise ValueError("unknown integration status")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("schema_version", 1)
    data.setdefault("setup_answers", {})  # 既存回答・未知キーは保持
    connections = data.setdefault("connections", {})
    previous = connections.get(route) if isinstance(connections.get(route), dict) else {}
    entry = {**previous, "state": state,
             "checked_at": at or datetime.now(timezone.utc).isoformat(),
             "detail_code": detail_code[:80]}
    if legacy:
        entry["legacy_state"] = legacy
    if step:
        entry["step"] = step[:80]
    connections[route] = entry
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    return data
