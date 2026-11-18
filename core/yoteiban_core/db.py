"""SQLite 接続、スキーマ移行、初期測定設定。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    conn.commit()
    schema_dir = Path(__file__).resolve().parents[1] / "schema"
    for path in sorted(schema_dir.glob("[0-9]*.sql")):
        version = int(path.name.split("_", 1)[0])
        if conn.execute("SELECT 1 FROM schema_version WHERE version=?", (version,)).fetchone():
            continue
        script = path.read_text(encoding="utf-8")
        # The version ledger is created before the first migration.
        script = script.replace("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);", "")
        conn.executescript("BEGIN IMMEDIATE;\n" + script + f"\nINSERT INTO schema_version(version,applied_at) VALUES({version},'{datetime.now().astimezone().isoformat()}');\nCOMMIT;")


_SEED = {
    "ai_monthly_cap_yen": 1000, "interval_check_hours": 6,
    "latency_target_simple_s": 30, "latency_target_dialog_s": 180,
    "duration_factors": [1.0, 1.3, 1.8], "travel_margin_min": 15,
    "overload_minutes": 480, "rain_prob": 60, "prep_min": 30,
    "read_accuracy_target": 0.95, "shadow_pass": {"n": 30, "max_edits": 1},
    "heartbeat_grace_min": {"watch": 5, "clock": 5, "nightly": 1560},
    "resend_backoff_s": [30, 120, 600, 1800],
    "backup_keep": {"daily": 30, "monthly": 12}, "morning_max_items": 3,
}


def seed_settings(conn: sqlite3.Connection) -> None:
    conn.executemany("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", [(k, json.dumps(v, ensure_ascii=False, separators=(",", ":"))) for k, v in _SEED.items()])
    conn.commit()
