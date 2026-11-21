"""Reference logic shipped for relay-bot updates; stores IDs and status only."""
from __future__ import annotations

import sqlite3
import os
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def classify_event(text: str, *, is_group: bool, call_markers: list[str],
                   reply_markers: list[str], sender_is_bot: bool = False) -> str:
    body = unicodedata.normalize("NFKC", str(text or "")).lstrip()
    if sender_is_bot:
        return "relay_bot_message"
    if any(marker and body.startswith(marker) for marker in reply_markers):
        return "relay_bot_message"
    if is_group:
        if any(marker and body.startswith(marker) for marker in call_markers):
            return "called_group"
        return "group_without_call"
    return "direct_message"


def decision_context(now: datetime, timezone_name: str) -> dict:
    """Provide an injected instant in the owner's configured IANA timezone."""
    if now.tzinfo is None:
        raise ValueError("now must include timezone")
    local = now.astimezone(ZoneInfo(timezone_name))
    return {"now_local": local.isoformat(), "timezone": timezone_name}


class ReplyLedger:
    """One shared database per bot; message content and sender identity aren't stored."""
    def __init__(self, path: str | Path):
        self.path = str(path)
        # Create new ledger files owner-only; never truncate or replace an existing file.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS reply_ledger (
            message_id TEXT PRIMARY KEY, state TEXT NOT NULL,
            route TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    def reserve(self, message_id: str, route: str, at: str) -> bool:
        if not message_id or not route:
            raise ValueError("message_id and route are required")
        cur = self.db.execute("INSERT OR IGNORE INTO reply_ledger VALUES (?, 'reserved', ?, ?)",
                              (message_id, route, at))
        return cur.rowcount == 1

    def mark_replied(self, message_id: str, at: str) -> bool:
        cur = self.db.execute("UPDATE reply_ledger SET state='replied', updated_at=? "
                              "WHERE message_id=? AND state='reserved'", (at, message_id))
        return cur.rowcount == 1

    def release_after_definite_failure(self, message_id: str) -> bool:
        cur = self.db.execute("DELETE FROM reply_ledger WHERE message_id=? AND state='reserved'",
                              (message_id,))
        return cur.rowcount == 1

    def state(self, message_id: str) -> str | None:
        row = self.db.execute("SELECT state FROM reply_ledger WHERE message_id=?", (message_id,)).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.db.close()


def route_event(text: str, *, is_group: bool, call_markers: list[str],
                reply_markers: list[str], sender_is_bot: bool = False) -> dict:
    kind = classify_event(text, is_group=is_group, call_markers=call_markers,
                          reply_markers=reply_markers, sender_is_bot=sender_is_bot)
    eligible = kind in {"called_group", "direct_message"}
    return {"kind": kind, "should_call_ai": eligible, "should_dispatch": eligible}
