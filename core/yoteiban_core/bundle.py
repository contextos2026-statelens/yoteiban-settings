"""通知候補を即時・朝・見送りに振り分ける純粋関数。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from yoteiban_rd import calc

_URGENCY_RANK = {"すぐ": 0, "今日中": 1, "朝でよい": 2}


def _now(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("now はタイムゾーン付き日時で指定してください")
    return value.astimezone(calc.JST)


def _clock(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(calc.JST).timetz().replace(tzinfo=None)
        return value.time()
    if isinstance(value, str):
        try:
            if "T" in value or " " in value:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed.astimezone(calc.JST).time().replace(tzinfo=None) if parsed.tzinfo else parsed.time()
            return time.fromisoformat(value)
        except ValueError:
            return None
    return None


def _moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(calc.JST) if value.tzinfo else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(calc.JST) if parsed.tzinfo else None


def _in_clock_range(current: time, start: time, end: time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _quiet(now: datetime, quiet_hours) -> bool:
    for span in quiet_hours or []:
        if isinstance(span, dict):
            start, end = _clock(span.get("start")), _clock(span.get("end"))
        elif isinstance(span, (list, tuple)) and len(span) == 2:
            start, end = _clock(span[0]), _clock(span[1])
        else:
            continue
        if start and end and _in_clock_range(now.timetz().replace(tzinfo=None), start, end):
            return True
    return False


def _in_focus(now: datetime, blocks) -> bool:
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        start, end = _moment(block.get("start")), _moment(block.get("end"))
        if start and end and start <= now < end:
            return True
    return False


def _date_key(item: dict, today: date) -> tuple[int, str]:
    raw = item.get("date")
    try:
        day = date.fromisoformat(str(raw)[:10])
        return max(0, (day - today).days), day.isoformat()
    except (TypeError, ValueError):
        return 9999, "9999-12-31"


def _stable_key(item: dict) -> str:
    if item.get("key"):
        return str(item["key"])
    refs = sorted(str(ref) for ref in item.get("refs", []))
    return "|".join((str(item.get("type", "")), str(item.get("date", "")), ",".join(refs)))


def _sort(items: list[dict], today: date) -> list[dict]:
    return sorted(items, key=lambda item: (_URGENCY_RANK.get(item.get("urgency"), 2),
                                           *_date_key(item, today), _stable_key(item)))


def bundle(findings, now, prefs, sent_keys: set) -> dict:
    """Findingを静かな時間・集中時間・重複送信・上限に沿って振り分ける。"""
    local_now = _now(now)
    prefs = prefs or {}
    sent_keys = sent_keys or set()
    quiet = _quiet(local_now, prefs.get("quiet_hours", []))
    focus = _in_focus(local_now, prefs.get("focus_blocks", []))
    muted = set(prefs.get("muted_types", []) or [])
    snoozes = prefs.get("snooze", {}) or {}
    immediate, morning, dropped = [], [], []

    for item in findings or []:
        key = _stable_key(item)
        if key in sent_keys or item.get("type") in muted:
            dropped.append(item)
            continue
        snoozed_until = _moment(snoozes.get(key))
        if snoozed_until and snoozed_until > local_now:
            dropped.append(item)
            continue
        if item.get("urgency") == "すぐ":
            immediate.append(item)
        else:
            # 静かな時間と集中時間は即時通知を抑え、朝の一覧へ回す。
            morning.append(item)

    immediate = _sort(immediate, local_now.date())
    morning = _sort(morning, local_now.date())
    limit = prefs.get("morning_max_items", 3)
    try:
        limit = max(0, int(limit))
    except (TypeError, ValueError):
        limit = 3
    kept_morning = morning[:limit]
    dropped.extend(morning[limit:])
    return {"now": immediate, "morning": kept_morning, "dropped": dropped}


def to_text(items) -> str:
    """通知項目を1件1行の短い日本語にする。"""
    lines = []
    for item in items or []:
        text = str(item.get("text", "")).strip()
        if not text:
            date_text = str(item.get("date", "")).strip()
            kind = str(item.get("type", "お知らせ")).strip()
            text = f"{date_text} {kind}".strip()
        if text:
            lines.append(text.replace("\n", " "))
    return "\n".join(lines)
