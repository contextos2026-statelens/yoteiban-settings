"""起こし係：Webhook で予定ばんを起こす。本文は送らず、依頼IDと入口の参照と仕分けの結果だけを送る。
はっきりした失敗（接続できない・5xx）だけ、数回まで置き直す。送ったあとに返事が分からない（時間切れ）は
「起こし結果不明」にして起こし直さない（定期の見回りが照合で拾う）。"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request


def wake(url: str, key: str | None, payload: dict, retries: int = 3, timeout_s: float = 8.0,
         backoff_s: float = 2.0, sleep=time.sleep) -> dict:
    if not url or not key:
        return {"state": "起こし失敗", "why": "WebhookのURLか鍵が設定にない"}
    body = json.dumps(payload, ensure_ascii=False).encode()
    last = ""
    for i in range(retries):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + key,
            "Idempotency-Key": payload.get("request_id", "")})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                return {"state": "起こし済み", "http": r.status, "attempts": i + 1}
        except urllib.error.HTTPError as e:
            if 500 <= e.code < 600:
                last = f"http {e.code}"
            else:
                return {"state": "起こし失敗", "http": e.code, "attempts": i + 1, "why": "4xx は置き直さない"}
        except (socket.timeout, TimeoutError):
            return {"state": "起こし結果不明", "attempts": i + 1, "why": "送ったあと返事が来ない。起こし直さない"}
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                return {"state": "起こし結果不明", "attempts": i + 1, "why": "送ったあと返事が来ない。起こし直さない"}
            last = f"接続できない: {type(e.reason).__name__}"
        except (ConnectionError, OSError) as e:
            last = f"接続できない: {type(e).__name__}"
        if i + 1 < retries:
            sleep(backoff_s * (i + 1))
    return {"state": "起こし失敗", "attempts": retries, "why": last}
