"""台帳内の情報源一覧と最終確認時刻を管理する。"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


def _iso(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or not value:
        raise ValueError("時刻はISO 8601で指定してください")
    return value


def _dt(value) -> datetime:
    parsed = datetime.fromisoformat(_iso(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed


def _reader_name(reader) -> str:
    if isinstance(reader, str) and reader.strip():
        return reader.strip()
    if callable(reader):
        module = getattr(reader, "__module__", "")
        name = getattr(reader, "__qualname__", getattr(reader, "__name__", "reader"))
        return f"{module}.{name}" if module else name
    raise ValueError("reader は名前または関数で指定してください")


def register(ledger, source_id, kind, reader, read_only):
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id が必要です")
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("kind が必要です")
    if not isinstance(read_only, bool):
        raise ValueError("read_only はboolで指定してください")
    name = _reader_name(reader)
    with ledger.tx():
        old = ledger.conn.execute("SELECT status,last_seen_at,note FROM sources WHERE source_id=?", (source_id,)).fetchone()
        if old:
            ledger.conn.execute("UPDATE sources SET kind=?,reader=?,read_only=? WHERE source_id=?",
                                (kind, name, int(read_only), source_id))
        else:
            ledger.conn.execute("INSERT INTO sources(source_id,kind,reader,read_only,status) VALUES(?,?,?,?,?)",
                                (source_id, kind, name, int(read_only), "未接続"))
    return source_id


def seen(ledger, source_id, ok, now):
    if not isinstance(ok, bool):
        raise ValueError("ok はboolで指定してください")
    ledger.source_seen(source_id, ok, _iso(now))
    return "動いている" if ok else "止まっている"


def stale(ledger, now, limits):
    now_dt = _dt(now)
    rows = ledger.conn.execute("SELECT source_id,kind,last_seen_at,status FROM sources ORDER BY source_id").fetchall()
    stopped = []
    for row in rows:
        limit = limits.get(row["kind"])
        if limit is None or row["last_seen_at"] is None:
            continue
        limit = float(limit)
        if limit < 0:
            raise ValueError("limits は0以上の分で指定してください")
        is_stale = now_dt - _dt(row["last_seen_at"]) > timedelta(minutes=limit)
        if is_stale and row["status"] != "止まっている":
            with ledger.tx():
                ledger.conn.execute("UPDATE sources SET status='止まっている' WHERE source_id=?", (row["source_id"],))
        if is_stale:
            stopped.append({"source_id": row["source_id"], "kind": row["kind"],
                            "last_seen_at": row["last_seen_at"], "status": "止まっている"})
    return stopped


def summary_text(sources):
    if isinstance(sources, (str, dict)):
        sources = [sources]
    lines = []
    for item in sources or []:
        if isinstance(item, dict):
            label = item.get("label") or item.get("source_id") or item.get("kind") or "情報源"
        else:
            label = str(item)
        if label:
            lines.append(f"{label}の新着を確かめられていません")
    return "\n".join(lines)
