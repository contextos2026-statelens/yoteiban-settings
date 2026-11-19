"""予定ばんの設定読み込み。秘密値自体は設定ファイルに置かない。"""
from __future__ import annotations

import json
import os
from pathlib import Path


REQUIRED = {
    "db_path", "cal_personal_id", "cal_readonly_ids",
    "reminder_list_id", "reminder_readonly_list_ids", "notion_db_ids",
    "webhook_secret_env", "heartbeat_urls", "backup_public_key_path",
    "backup_target", "ai", "quiet_hours", "morning_max_items", "retention_days",
}


def load(path: str | Path) -> dict:
    try:
        with Path(path).open(encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"設定ファイルを読み込めません: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("設定はJSONオブジェクトである必要があります")
    missing = sorted(REQUIRED - value.keys())
    if missing:
        raise ValueError("設定に不足しているキー: " + ", ".join(missing))
    nested = {"ai": {"enabled", "provider", "fast_model", "strong_model", "key_env", "yen_per_1k_in", "yen_per_1k_out"}, "quiet_hours": {"start", "end"}, "backup_target": {"kind", "ref"}, "heartbeat_urls": {"watch", "clock", "nightly", "interval"}}
    bad = [f"{key}.{sub}" for key, subs in nested.items() if not isinstance(value.get(key), dict) or (subs - value[key].keys())]
    if bad:
        raise ValueError("設定に不足しているキー: " + ", ".join(bad))
    return value


def env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise ValueError(f"環境変数が設定されていません: {name}")
    return value
