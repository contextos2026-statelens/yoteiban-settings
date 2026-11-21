"""見張りの入口。どれも読むだけ。
- iMessage：chat.db を読み取り専用（mode=ro）で開き、ROWID の読み位置から新着を読む。
- Gmail・カレンダー：Google を読む道具（gog）を、読む機能だけに絞って呼ぶ。
  道具の引数は設定で差し替えられる（道具の版で変わるため。実機での確認が要る）。"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

_APP_ROOT = Path(__file__).resolve().parents[1]
_CORE = _APP_ROOT if (_APP_ROOT / "yoteiban_core").is_dir() else Path(__file__).resolve().parents[3] / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))
from yoteiban_core.refs import normalize_native_ref

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


@dataclass
class Incoming:
    source: str
    native_id: str
    sender: str  # 送り主の識別子（番号・アドレス）。外部AIには渡さない
    text: str
    at: str
    chat: str = ""  # 会話の識別子。外部AIには渡さない
    is_group: bool = False
    from_me: bool = False
    extra: dict = field(default_factory=dict)


class SourceError(Exception):
    """入口が読めない（権限・ファイル無し・道具の失敗）。朝のまとめの「読めていない入口」に出す。"""


def _decode_attributed(blob: bytes | None) -> str:
    """新しい macOS で text が空のときの attributedBody から本文を取り出す（ベストエフォート）。"""
    if not blob:
        return ""
    try:
        i = blob.index(b"NSString")
        chunk = blob[i + 8:]
        j = chunk.index(b"+") + 1
        n = chunk[j]
        start = j + 1
        if n == 0x81:
            n = int.from_bytes(chunk[j + 1:j + 3], "little")
            start = j + 3
        return chunk[start:start + n].decode("utf-8", "replace")
    except (ValueError, IndexError):
        return ""


def read_imessage(db_path: str, after_rowid: int, limit: int = 200) -> list[Incoming]:
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as e:
        raise SourceError(f"iMessage を開けません: {e}") from e
    try:
        rows = con.execute(
            """select m.ROWID, m.text, m.attributedBody, m.date, m.is_from_me, coalesce(h.id,''),
                      coalesce(c.chat_identifier,''), coalesce(c.style,0), coalesce(m.guid,'')
               from message m left join handle h on h.ROWID=m.handle_id
               left join chat_message_join cmj on cmj.message_id=m.ROWID
               left join chat c on c.ROWID=cmj.chat_id
               where m.ROWID>? order by m.ROWID limit ?""", (after_rowid, limit)).fetchall()
    except sqlite3.Error as e:
        raise SourceError(f"iMessage を読めません: {e}") from e
    finally:
        con.close()
    out = []
    for rowid, text, ab, d, me, handle, chat, style, guid in rows:
        body = text or _decode_attributed(ab)
        ts = APPLE_EPOCH + timedelta(seconds=(d or 0) / 1e9 if (d or 0) > 1e12 else (d or 0))
        native = normalize_native_ref("imessage", f"guid:{guid}" if guid else str(rowid))
        out.append(Incoming("imessage", native, handle, body, ts.isoformat(), chat, style == 43, bool(me),
                            extra={"rowid": int(rowid)}))
    return out


def normalize_linq_webhook(event: dict) -> Incoming:
    """Linq message.receivedを共通Incomingへ変換。イベント受信・認証は呼出側が担当。"""
    if not isinstance(event, dict):
        raise SourceError("Linqイベントがオブジェクトではありません")
    data = event.get("data") if isinstance(event.get("data"), dict) else event
    msg = data.get("message") if isinstance(data.get("message"), dict) else data
    chat = data.get("chat") if isinstance(data.get("chat"), dict) else {}
    event_id = event.get("event_id") or event.get("id")
    native = msg.get("id") or msg.get("message_id") or event_id
    if not native:
        raise SourceError("Linqイベントに再送照合用IDがありません")
    sender = msg.get("sender_handle") or msg.get("sender") or ""
    body = msg.get("text") or msg.get("body") or ""
    sent_at = msg.get("sent_at") or msg.get("created_at") or data.get("sent_at") or ""
    return Incoming("linq", str(native), str(sender), str(body), str(sent_at),
                    str(chat.get("id") or data.get("chat_id") or ""),
                    bool(chat.get("is_group", data.get("is_group", False))),
                    str(msg.get("direction", data.get("direction", "inbound"))).casefold() == "outbound",
                    extra={"event_id": str(event_id or ""), "owner_handle": chat.get("owner_handle")})


def run_tool(argv: list, service: str, timeout: int = 60) -> object:
    """読む道具を呼ぶ。道具の機能は --enable-commands で service だけに絞ってあること。
    送信や変更の語が引数に入っていたら呼ばない。"""
    bad = {"send", "delete", "create", "update", "modify", "trash", "insert", "patch", "reply", "forward", "move"}
    if not argv or any(str(a).lower() in bad for a in argv):
        raise SourceError("読む以外の操作は呼びません")
    if "--enable-commands" not in argv or argv[argv.index("--enable-commands") + 1:][:1] != [service]:
        raise SourceError("道具の機能を " + service + " だけに絞っていません")
    try:
        p = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SourceError(f"道具が動きません: {e}") from e
    if p.returncode != 0:
        raise SourceError("道具が失敗しました: " + re.sub(r"\s+", " ", p.stderr)[:200])
    try:
        return json.loads(p.stdout or "null")
    except json.JSONDecodeError as e:
        raise SourceError("道具の出力を読めません") from e



def _gmail_message_id(m: dict) -> str:
    """RFC の Message-ID を優先し、無ければ API の id を使う。どちらも正規形にする。"""
    for key in ("rfc822msgid", "rfc822MessageId", "rfc822_message_id", "message_id"):
        value = m.get(key)
        if value:
            return normalize_native_ref("gmail", value)
    payload = m.get("payload")
    headers = m.get("headers")
    if headers is None and isinstance(payload, dict):
        headers = payload.get("headers")
    if isinstance(headers, list):
        for header in headers:
            if str(header.get("name", "")).casefold() == "message-id" and header.get("value"):
                return normalize_native_ref("gmail", header.get("value"))
    mid = m.get("messageId") or m.get("id") or ""
    return normalize_native_ref("gmail", mid) if mid else ""


def read_gmail(cmd: list[str], after_iso: str | None) -> list[Incoming]:
    """cmd 例（設定）：["gog","--enable-commands","gmail","-j","--results-only","gmail","search","newer_than:1d"]"""
    data = run_tool(cmd, "gmail")
    items = data if isinstance(data, list) else (data or {}).get("messages", []) if isinstance(data, dict) else []
    out = []
    for m in items:
        mid = _gmail_message_id(m)
        if not mid:
            continue
        at = str(m.get("date") or m.get("internalDate") or "")
        if after_iso and at and at <= after_iso:
            continue
        out.append(Incoming("gmail", mid, str(m.get("from", "")), str(m.get("snippet") or m.get("subject") or ""), at,
                            extra={"subject": str(m.get("subject", ""))}))
    return out


def read_calendar_changes(cmd: list[str], known: dict) -> tuple[list[Incoming], dict]:
    """予定の一覧を読み、前回の一覧（id→更新時刻）と比べて、増えた・変わった・消えたを出す。"""
    data = run_tool(cmd, "calendar")
    evs = data if isinstance(data, list) else (data or {}).get("events", []) if isinstance(data, dict) else []
    now_map, out = {}, []
    for e in evs:
        eid = str(e.get("id", ""))
        upd = str(e.get("updated", ""))
        if not eid:
            continue
        now_map[eid] = upd
        if eid not in known:
            out.append(Incoming("calendar", normalize_native_ref("calendar", f"{eid}@{upd}"), "", str(e.get("summary", "")), upd, extra={"change": "増えた"}))
        elif known[eid] != upd:
            out.append(Incoming("calendar", normalize_native_ref("calendar", f"{eid}@{upd}"), "", str(e.get("summary", "")), upd, extra={"change": "変わった"}))
    for eid in known:
        if eid not in now_map:
            out.append(Incoming("calendar", normalize_native_ref("calendar", f"{eid}@gone"), "", "", "", extra={"change": "消えた"}))
    return out, now_map
