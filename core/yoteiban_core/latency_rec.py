"""処理段階の待ち時間記録と、旧latency.jsonlからの移し替え。"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


def _iso(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or not value:
        raise ValueError("時刻はISO 8601で指定してください")
    return value


def _dt(value) -> datetime:
    raw = _iso(value).strip()
    if raw.endswith(" JST"):
        raw = raw[:-4]
        parsed = datetime.fromisoformat(raw.replace(" ", "T"))
        return parsed.replace(tzinfo=JST)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed


def start(ledger, req_id, stage, now):
    if not req_id or not stage:
        raise ValueError("req_id と stage が必要です")
    at = _iso(now)
    ledger.record_latency(str(req_id), str(stage), at, None)
    return {"req_id": str(req_id), "stage": str(stage), "started_at": at}


def end(ledger, req_id, stage, now, ai_kind=None, cost_yen=0):
    row = ledger.conn.execute("SELECT started_at FROM latency WHERE req_id=? AND stage=?", (req_id, stage)).fetchone()
    if row is None:
        raise KeyError((req_id, stage))
    if float(cost_yen) < 0:
        raise ValueError("cost_yen は0以上で指定してください")
    at = _iso(now)
    ledger.record_latency(req_id, stage, row["started_at"], at, ai_kind=ai_kind, cost_yen=cost_yen)
    return {"req_id": req_id, "stage": stage, "ended_at": at, "ai_kind": ai_kind,
            "cost_yen": float(cost_yen)}


def report(ledger, since):
    since_dt = _dt(since)
    rows = ledger.conn.execute("SELECT stage,started_at,ended_at FROM latency WHERE ended_at IS NOT NULL").fetchall()
    by_stage = defaultdict(list)
    for row in rows:
        try:
            started, ended = _dt(row["started_at"]), _dt(row["ended_at"])
            seconds = (ended - started).total_seconds()
            if ended >= since_dt and seconds >= 0:
                by_stage[row["stage"]].append(seconds)
        except (TypeError, ValueError):
            continue
    result = {}
    for stage, values in sorted(by_stage.items()):
        ordered = sorted(values)
        p90 = ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)]
        result[stage] = {"median_seconds": round(statistics.median(ordered), 3),
                         "p90_seconds": round(p90, 3), "count": len(ordered)}
    return result


def import_jsonl(ledger, path):
    """旧ログの asked_at/logged_at/scene/adjustments をlatency表へ移す。"""
    count = 0
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
                asked, logged, scene = item["asked_at"], item["logged_at"], item["scene"]
                if scene not in {"simple", "dialog", "multi", "express"}:
                    continue
                started, ended = _dt(asked).isoformat(), _dt(logged).isoformat()
                if _dt(ended) < _dt(started):
                    continue
                canonical = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                req_id = "legacy-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
                ledger.record_latency(req_id, "legacy:" + scene, started, ended,
                                      version="redesign-latency-jsonl", ai_kind=None, cost_yen=0)
                count += 1
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
    return count
