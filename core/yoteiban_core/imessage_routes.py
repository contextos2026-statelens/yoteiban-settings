"""iMessage受信経路の決定。未知の参加状況は保留し、誤配送を避ける。"""
from __future__ import annotations

import hashlib
import re
import unicodedata


def normalize_handle(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if "@" in text:
        return text
    digits = re.sub(r"\D", "", text)
    return ("+" + digits) if digits else text


def _active(handles: list[dict] | None) -> set[str] | None:
    if handles is None:
        return None
    result = set()
    for handle in handles:
        if not isinstance(handle, dict):
            return None
        if handle.get("status", "active") in {"left", "removed", "inactive"} or handle.get("left_at"):
            continue
        identity = normalize_handle(handle.get("handle") or handle.get("id") or handle.get("address"))
        if identity:
            result.add(identity)
    return result


def choose_route(*, source: str, is_group: bool, owner_handles: list[str],
                 participants: list[dict] | None = None, from_me: bool = False,
                 linq_handles: list[str] | None = None, owner_handle: str = "") -> str:
    """Return mac, linq, ignore, or hold. Linq group roster must be current/known."""
    if from_me:
        return "ignore"
    if source == "imessage":
        return "mac"
    if source != "linq":
        return "hold"
    if not is_group:
        configured = {normalize_handle(x) for x in (linq_handles or []) if normalize_handle(x)}
        return "linq" if configured and normalize_handle(owner_handle) in configured else "hold"
    active = _active(participants)
    if active is None:
        return "hold"
    owners = {normalize_handle(x) for x in owner_handles if normalize_handle(x)}
    if not owners:
        return "hold"
    if active & owners:
        return "ignore"  # Mac chat.dbを正とする重複経路
    return "linq"


def duplicate_fingerprint(*, conversation_key: str, sender: str, body: str,
                          sent_at: str) -> str:
    """経路横断の照合用。同一内容・時刻の候補を保留/照合する鍵で、無条件破棄に使わない。"""
    fields = (conversation_key, normalize_handle(sender), unicodedata.normalize("NFKC", body).strip(), sent_at)
    return hashlib.sha256("\0".join(fields).encode()).hexdigest()
