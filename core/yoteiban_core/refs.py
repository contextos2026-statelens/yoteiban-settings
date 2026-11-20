"""入口ごとの native_ref の正規形。受け口と ingest は必ずこれを通す。

既存の台帳の行は書き換えない。照合のときは保存済みの値もこの関数で読んで比べる。
決めた形は core/docs/native_ref.md に書く。
"""
from __future__ import annotations

import re

_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def canonicalize_source(source: str) -> str:
    """同じ入口の別名を、照合用の短い名前にまとめる。"""
    if not isinstance(source, str):
        return ""
    text = source.strip()
    key = text.split(":", 1)[0].casefold()
    if key in {"gmail", "email", "mail"} or key.startswith("gmail-"):
        return "gmail"
    if key == "imessage" or key.startswith("imessage-"):
        return "imessage"
    if key == "calendar" or key.startswith("calendar-"):
        return "calendar"
    if key == "reminder_notification" or key.startswith("reminder_notification-"):
        return "reminder_notification"
    return text


def normalize_native_ref(source, raw) -> str:
    """入口ごとの正規形を返す。空や型が違うときは空文字。"""
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    kind = canonicalize_source(source if isinstance(source, str) else "")
    if kind == "gmail":
        return _gmail(text)
    if kind == "imessage":
        return _imessage(text)
    if kind == "calendar":
        return _calendar(text)
    if kind == "reminder_notification":
        value = text[6:].strip() if text.casefold().startswith("notif:") else text
        return "notif:" + value.casefold()
    return text


def _gmail(text: str) -> str:
    current = text
    while True:
        stripped = current.strip()
        if len(stripped) >= 2 and stripped[0] == "<" and stripped[-1] == ">":
            current = stripped[1:-1]
            continue
        return stripped.casefold()


def _imessage(text: str) -> str:
    stripped = text.strip()
    lower = stripped.casefold()
    if lower.startswith("guid:"):
        return "guid:" + lower[5:].strip().strip("{}")
    if lower.startswith("rowid:"):
        digits = lower[6:].strip()
        if digits.isdigit():
            return "rowid:" + str(int(digits))
        return "rowid:" + digits
    bare = stripped.strip("{}")
    if _UUID.fullmatch(bare):
        return "guid:" + bare.casefold()
    if stripped.isdigit():
        return "rowid:" + str(int(stripped))
    return stripped


def _calendar(text: str) -> str:
    stripped = text.strip()
    if stripped.casefold().startswith("event:"):
        stripped = stripped[6:].strip()
    if not stripped:
        return ""
    if "@" in stripped:
        event_id, mark = stripped.rsplit("@", 1)
        event_id, mark = event_id.strip(), mark.strip()
        if event_id and mark:
            return f"event:{event_id}@{mark}"
    return f"event:{stripped}"
