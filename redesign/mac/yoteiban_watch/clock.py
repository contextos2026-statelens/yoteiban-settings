"""予定の期限だけを取得し、時刻になった案件を一度起こす。予定本文は扱わない。"""
from __future__ import annotations

import json
import hashlib
import urllib.request
from datetime import datetime


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def fetch_due(fetch=None, url: str | None = None) -> list[dict]:
    """due_url から case_id と next_review_at のみを読む。fetch は差し替え可能。"""
    if not url:
        return []
    fetch = fetch or urllib.request.urlopen
    try:
        response = fetch(url)
        try:
            raw = response.read() if hasattr(response, "read") else response
        finally:
            if hasattr(response, "close"):
                response.close()
        data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw) if isinstance(raw, (bytes, str)) else raw
        rows = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
        return [{"case_id": str(x["case_id"]), "next_review_at": str(x["next_review_at"])}
                for x in rows if isinstance(x, dict) and x.get("case_id") and x.get("next_review_at")]
    except Exception:
        return []


def _already(sent_store, case_id: str, due_at: str) -> bool:
    key = (case_id, due_at)
    if hasattr(sent_store, "has_clock_sent"):
        return sent_store.has_clock_sent(*key)
    if hasattr(sent_store, "has"):
        return sent_store.has(key)
    return key in sent_store


def _mark(sent_store, case_id: str, due_at: str) -> None:
    key = (case_id, due_at)
    if hasattr(sent_store, "mark_clock_sent"):
        sent_store.mark_clock_sent(*key)
    elif hasattr(sent_store, "add"):
        sent_store.add(key)
    else:
        sent_store[key] = True


def tick(now, due, sent_store, wake_fn):
    """期限到来分を起こし、case_id + due_at ごとに一度だけ送る。"""
    now = _dt(now)
    sent = []
    for item in due:
        case_id, due_at = str(item["case_id"]), str(item["next_review_at"])
        try:
            is_due = _dt(due_at) <= now
        except (TypeError, ValueError):
            continue
        if not is_due or _already(sent_store, case_id, due_at):
            continue
        rid = hashlib.sha256(f"{case_id}|{due_at}".encode("utf-8")).hexdigest()[:24]
        payload = {"kind": "yoteiban-review", "request_id": "review:" + rid,
                   "case_id": case_id, "due_at": due_at}
        # 通信結果不明時にも二重に起こさないため、呼び出す前に一度だけ記録する。
        _mark(sent_store, case_id, due_at)
        try:
            result = wake_fn(payload)
        except Exception:
            continue
        if result is False:
            continue
        sent.append(payload)
    return sent


def catch_up(now, due, last_run_at, sent_store, wake_fn):
    """停止中に期限を過ぎた分をまとめて処理する（本文は取らない）。"""
    now_dt = _dt(now)
    last_dt = _dt(last_run_at) if last_run_at else None
    missed = []
    for item in due:
        try:
            when = _dt(item["next_review_at"])
        except (TypeError, ValueError):
            continue
        if when <= now_dt and (last_dt is None or when > last_dt):
            missed.append(item)
    return tick(now_dt, missed, sent_store, wake_fn)
