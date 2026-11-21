"""起こし係：Webhook で予定ばんを起こす。HMAC署名と依頼IDで安全に渡す。
はっきりした失敗（接続できない・5xx）だけ、設定された間隔で置き直す。送ったあとに返事が分からない（時間切れ）は
「起こし結果不明」にして起こし直さない（定期の見回りが照合で拾う）。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def build_signed_body(payload: dict, key: str, *, envelope: bool = False) -> tuple[bytes, str]:
    """Return exact request bytes and HMAC header value; never logs either value."""
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(key.encode(), payload_bytes, hashlib.sha256).hexdigest()
    if envelope:
        body = json.dumps({"format": "yoteiban-envelope-v1", "payload": payload_bytes.decode("utf-8"),
                           "signature": signature}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
        return body, signature
    return payload_bytes, signature


def retry_after_seconds(value: str | None, *, now=None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            return max(0.0, (target - current).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def wake(url: str, key: str | None, payload: dict, retries: int | None = None, timeout_s: float = 8.0,
         backoff_s: float = 2.0, sleep=time.sleep, backoff: list[float] | None = None,
         authorization_header: str | None = None, max_retry_after_s: float = 300.0) -> dict:
    if not url or not key:
        return {"state": "起こし失敗", "why": "WebhookのURLか鍵が設定にない", "detail_code": "CONFIG_MISSING"}
    body, signature = build_signed_body(payload, key, envelope=os.environ.get("YOTEIBAN_WEBHOOK_FORMAT") == "envelope")
    if authorization_header is None:
        authorization_header = os.environ.get("YOTEIBAN_WEBHOOK_AUTH")
    auth_headers = {}
    if authorization_header:
        if authorization_header.startswith("Bearer "):
            auth_value = authorization_header[7:]
            auth_headers["Authorization"] = authorization_header
        else:
            auth_value = authorization_header
            auth_headers["Authorization"] = "Bearer " + authorization_header
        auth_headers["X-Automation-Key"] = auth_value
    delays = list(backoff) if backoff is not None else [backoff_s * (i + 1) for i in range(max((retries or 3) - 1, 0))]
    total = len(delays) + 1 if retries is None else min(retries, len(delays) + 1)
    last = ""
    for i in range(total):
        headers = {
            "Content-Type": "application/json", "X-Yoteiban-Signature": signature,
            "Idempotency-Key": payload.get("request_id", "")}
        headers.update(auth_headers)
        try:
            req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        except Exception:
            # Authorization の不正な値を例外文や戻り値へ漏らさない。
            return {"state": "起こし失敗", "attempts": i + 1, "why": "Webhook要求を作れない"}
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                reply = r.read()
                if r.status < 200 or r.status >= 300:
                    last = f"http {r.status}"
                else:
                    try:
                        data = json.loads(reply.decode("utf-8")) if reply else {}
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        data = {}
                    got = data.get("request_id") if isinstance(data, dict) else None
                    if got is not None and got != payload.get("request_id"):
                        return {"state": "起こし失敗", "http": r.status, "attempts": i + 1,
                                "why": "request_id が一致しない", "detail_code": "REQUEST_ID_MISMATCH"}
                    registration = "registered" if isinstance(data, dict) and data.get("ok") is True and data.get("input_id") else "unverified"
                    return {"state": "起こし済み", "http": r.status, "attempts": i + 1,
                            "delivery_state": "success", "registration_state": registration}
        except urllib.error.HTTPError as e:
            if 500 <= e.code < 600:
                last = f"http {e.code}"
            elif e.code == 429:
                delay = retry_after_seconds(e.headers.get("Retry-After")) if e.headers else None
                if delay is not None and delay <= max_retry_after_s and i + 1 < total:
                    sleep(delay)
                    continue
                return {"state": "起こし失敗", "http": 429, "attempts": i + 1,
                        "why": "回数制限。安全なRetry-Afterが無いため置き直さない",
                        "detail_code": "RATE_LIMIT_RETRY_UNAVAILABLE"}
            else:
                return {"state": "起こし失敗", "http": e.code, "attempts": i + 1,
                        "why": "4xx は置き直さない", "detail_code": "CLIENT_ERROR_NO_RETRY"}
        except (socket.timeout, TimeoutError):
            return {"state": "起こし結果不明", "attempts": i + 1, "why": "送ったあと返事が来ない。起こし直さない",
                    "detail_code": "DELIVERY_RESULT_UNKNOWN"}
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                return {"state": "起こし結果不明", "attempts": i + 1, "why": "送ったあと返事が来ない。起こし直さない",
                        "detail_code": "DELIVERY_RESULT_UNKNOWN"}
            last = f"接続できない: {type(e.reason).__name__}"
        except (ConnectionError, OSError) as e:
            last = f"接続できない: {type(e).__name__}"
        except Exception:
            # HTTPライブラリの例外文に認証ヘッダー値が含まれる場合も外へ出さない。
            return {"state": "起こし失敗", "attempts": i + 1, "why": "Webhook要求に失敗"}
        if i + 1 < total:
            sleep(delays[i])
    return {"state": "起こし失敗", "attempts": total, "why": last, "detail_code": "DELIVERY_RETRY_EXHAUSTED"}
