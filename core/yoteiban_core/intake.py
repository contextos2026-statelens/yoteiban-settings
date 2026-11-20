"""箱側Webhookの検証済み受け口。入力本文は文字列としてのみ台帳へ渡す。"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import stat
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .ledger import Ledger
from .refs import normalize_native_ref
from .reminder_kind import classify as classify_reminder

DEFAULT_MAX_BODY_BYTES = 65536
DEFAULT_MAX_BODY_CHARS = 8000


def _result(ok=False, request_id=None, input_id=None, created=False, reason="", signed_by=None):
    return {"ok": bool(ok), "request_id": request_id, "input_id": input_id,
            "created": bool(created), "reason": reason, "signed_by": signed_by}


def _header(headers, name):
    if not isinstance(headers, dict):
        return None
    wanted = name.casefold()
    return next((str(value) for key, value in headers.items() if str(key).casefold() == wanted), None)


def _parse_iso(value):
    if not isinstance(value, str):
        raise ValueError("時刻がありません")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Tokyo"))
    return parsed


def _now_text(now):
    if isinstance(now, datetime):
        return now.isoformat()
    if isinstance(now, str) and now:
        return now
    raise ValueError("now は時刻で指定してください")


def _review(ledger, payload, now_text):
    case_id = payload.get("case_id")
    case = ledger.get_case(case_id) if isinstance(case_id, str) and case_id else None
    if case is None:
        return _result(False, payload.get("request_id"), reason="案件がありません")
    due_at = case.get("next_review_at")
    if not due_at:
        return _result(False, payload.get("request_id"), reason="確認予定がありません")
    try:
        due = _parse_iso(due_at)
        now_dt = _parse_iso(now_text)
        requested = payload.get("due_at")
        if requested and _parse_iso(requested) != due:
            return _result(False, payload.get("request_id"), reason="確認予定が変更されています")
    except (TypeError, ValueError):
        return _result(False, payload.get("request_id"), reason="時刻を確認できません")
    if now_dt < due:
        return _result(False, payload.get("request_id"), reason="確認時刻前です")
    return _result(True, payload.get("request_id"), reason="期限到来")


def handle(ledger, cfg, headers, body_bytes, now, secret):
    """署名済みwake入力を取り込むか、期限到来のreview通知を読み取り判定する。"""
    request_id = None
    if not isinstance(body_bytes, bytes):
        return _result(reason="本文はbytesで指定してください")
    max_bytes = int(cfg.get("webhook_max_body_bytes", DEFAULT_MAX_BODY_BYTES))
    if max_bytes <= 0 or len(body_bytes) > max_bytes:
        return _result(reason="本文が大きすぎます")
    signature = _header(headers, "X-Yoteiban-Signature")
    signed_by = None
    if not isinstance(secret, str) or not secret:
        return _result(reason="署名または鍵がありません")
    if signature:
        expected = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.strip().lower(), expected):
            return _result(reason="署名が正しくありません")
        signed_by = "header"
        try:
            outer = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            outer = None
        if isinstance(outer, dict) and outer.get("format") == "yoteiban-envelope-v1":
            inner_payload = outer.get("payload")
            inner_signature = outer.get("signature")
            if not isinstance(inner_payload, str) or not isinstance(inner_signature, str) or not inner_signature:
                return _result(reason="署名または鍵がありません", signed_by=signed_by)
            payload_bytes = inner_payload.encode("utf-8")
            inner_expected = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(inner_signature.strip().lower(), inner_expected):
                return _result(reason="署名が正しくありません", signed_by=signed_by)
            body_bytes = payload_bytes
    else:
        try:
            outer = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            outer = None
        if not isinstance(outer, dict) or outer.get("format") != "yoteiban-envelope-v1":
            return _result(reason="署名または鍵がありません")
        inner_payload = outer.get("payload")
        inner_signature = outer.get("signature")
        if not isinstance(inner_payload, str) or not isinstance(inner_signature, str) or not inner_signature:
            return _result(reason="署名または鍵がありません")
        payload_bytes = inner_payload.encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(inner_signature.strip().lower(), expected):
            return _result(reason="署名が正しくありません")
        body_bytes = payload_bytes
        signed_by = "envelope"
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _result(reason="JSONを読めません", signed_by=signed_by)
    if not isinstance(payload, dict):
        return _result(reason="JSONはオブジェクトで指定してください", signed_by=signed_by)
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        return _result(reason="request_id が不正です", signed_by=signed_by)
    try:
        at = _now_text(now)
    except ValueError as exc:
        return _result(request_id=request_id, reason=str(exc), signed_by=signed_by)
    kind = payload.get("kind")
    if kind == "yoteiban-review":
        result = _review(ledger, payload, at)
        result["signed_by"] = signed_by
        return result
    if kind != "yoteiban-wake":
        return _result(request_id=request_id, reason="kind が不正です", signed_by=signed_by)

    source = payload.get("source")
    native_ref = payload.get("native_ref", payload.get("ref"))
    if isinstance(source, str) and isinstance(native_ref, str):
        native_ref = normalize_native_ref(source, native_ref)
    received_at = payload.get("received_at")
    body_text = payload.get("body_text", "")
    reminder = None
    if source == "reminder_notification":
        # Ignore all free-form body/location fields. Only exact safe reminder
        # titles may identify a signal; other titles collapse to one generic line.
        reminder = classify_reminder(payload.get("title", ""))
        body_text = f"{reminder['summary']}（{received_at}）"
    sender_role = "本人" if reminder is not None else payload.get("sender_role", payload.get("role", "不明"))
    max_chars = int(cfg.get("max_body_chars", DEFAULT_MAX_BODY_CHARS))
    if (not isinstance(source, str) or not source or not isinstance(native_ref, str) or not native_ref
            or not isinstance(received_at, str) or not received_at):
        return _result(request_id=request_id, reason="source、native_ref、received_at が必要です", signed_by=signed_by)
    if not isinstance(body_text, str) or len(body_text) > max_chars:
        return _result(request_id=request_id, reason="本文が大きすぎるか文字列ではありません", signed_by=signed_by)
    if not isinstance(sender_role, str):
        return _result(request_id=request_id, reason="sender_role が不正です", signed_by=signed_by)

    # 読み取り係に渡す本文だけを伏せ字化する。伏せ字機能が無い場合や
    # 消し残しが検出された場合は空にし、後段でボット自身が読む。
    body_masked = ""
    try:
        from .mask import MaskLeak, guard
    except ImportError:
        pass
    else:
        try:
            body_masked = guard(body_text, cfg.get("mask_known_names", []))
        except MaskLeak:
            body_masked = ""

    # request IDとingestを同じSQLite transactionに記録し、再送・競合時も二重作成を防ぐ。
    with ledger.tx():
        ledger.conn.execute("CREATE TABLE IF NOT EXISTS intake_requests (request_id TEXT PRIMARY KEY, input_id TEXT NOT NULL)")
        old = ledger.conn.execute("SELECT input_id FROM intake_requests WHERE request_id=?", (request_id,)).fetchone()
        if old:
            input_id = old[0]
            return _result(True, request_id, input_id, False, "同じrequest_idを受け取り済みです", signed_by)
        input_id, created = ledger.ingest({"source": source, "native_ref": native_ref,
                                           "received_at": received_at, "body_text": body_text,
                                           "body_masked": body_masked,
                                           "sender_role": sender_role}, now=at)
        ledger.conn.execute("INSERT INTO intake_requests(request_id,input_id) VALUES(?,?)", (request_id, input_id))
    return _result(True, request_id, input_id, created, "受け取りました" if created else "入力済みです", signed_by)


def _cli(argv=None):
    parser = argparse.ArgumentParser(description="署名付きの予定ばんWebhookを検証します")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    envelope = json.load(sys.stdin)
    body = envelope.get("body", b"")
    if isinstance(body, str):
        body_bytes = body.encode("utf-8")
    elif isinstance(body, dict) or isinstance(body, list):
        body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    else:
        body_bytes = b""
    secret_name = cfg.get("webhook_secret_env", "")
    secret = os.environ.get(secret_name, "") if isinstance(secret_name, str) and secret_name else ""
    secret_file = cfg.get("webhook_secret_file")
    if not secret and isinstance(secret_file, str) and secret_file:
        path = Path(secret_file).expanduser()
        try:
            info = path.stat()
            if stat.S_IMODE(info.st_mode) & 0o077:
                result = _result(reason="鍵ファイルの権限が緩い")
            else:
                secret = path.read_text(encoding="utf-8").rstrip("\r\n")
                result = None
        except OSError:
            secret = ""
            result = None
    else:
        result = None
    if result is None:
        ledger = Ledger(cfg["db_path"])
        try:
            result = handle(ledger, cfg, envelope.get("headers", {}), body_bytes,
                            datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(), secret)
        finally:
            ledger.close()
    if "signed_by" not in result:
        result["signed_by"] = None
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
