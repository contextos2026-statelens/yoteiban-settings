"""質問回答と稼働接続を別々に記録する、秘密を含まない状態形式。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime, timezone

STATES = {"not_requested", "pending_user_action", "checking", "connected", "degraded", "needs_attention", "disabled"}


def update_status(path: str | Path, route: str, state: str, *, detail_code: str = "") -> dict:
    if route not in {"mac_chatdb", "linq_webhook"} or state not in STATES:
        raise ValueError("unknown receiver route/status")
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
    connections[route] = {"state": state, "checked_at": datetime.now(timezone.utc).isoformat(),
                          "detail_code": detail_code[:80]}
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    return data
