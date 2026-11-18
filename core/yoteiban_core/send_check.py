"""最後の送信直前に、承認と一度限りの操作を確認する。"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo


def _digest(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def verify(
    request: dict[str, Any],
    approvals_view: Callable[[str], dict[str, Any] | None],
    used_store: str | os.PathLike[str],
    now: str | datetime,
) -> tuple[bool, str]:
    """検証に成功した場合だけ、op_id と approval_id を原子的に消費する。"""
    if not isinstance(request, dict):
        return False, "依頼の形が正しくありません"
    required = ("approval_id", "payload", "payload_hash", "expires_at", "op_id")
    if any(key not in request for key in required):
        return False, "依頼に必要な項目がありません"
    approval_id, op_id = request["approval_id"], request["op_id"]
    if not all(isinstance(value, str) and value for value in (approval_id, op_id)):
        return False, "承認番号または操作番号が正しくありません"
    try:
        calculated = _digest(request["payload"])
    except (TypeError, ValueError, OverflowError):
        return False, "送信内容を指紋にできません"
    if not isinstance(request["payload_hash"], str) or calculated != request["payload_hash"]:
        return False, "送信内容の指紋が一致しません"

    try:
        approval = approvals_view(approval_id)
    except Exception:
        return False, "承認を確認できません"
    if not isinstance(approval, dict):
        return False, "承認が見つかりません"
    if approval.get("payload_hash") != calculated:
        return False, "承認した内容と一致しません"
    if approval.get("state") != "有効":
        return False, "承認は有効ではありません"

    now_dt = _time(now.isoformat() if isinstance(now, datetime) else now)
    request_expiry = _time(request["expires_at"])
    approval_expiry = _time(approval.get("expires_at"))
    if now_dt is None or request_expiry is None or approval_expiry is None:
        return False, "時刻の形式またはタイムゾーンが正しくありません"
    if request_expiry != approval_expiry:
        return False, "承認の期限と依頼の期限が一致しません"
    if now_dt >= request_expiry:
        return False, "承認の期限が切れています"

    path = os.fspath(used_store)
    try:
        # 同じファイルをロックしたまま読み取りと更新を行い、並行実行の二重使用も防ぐ。
        with open(path, "a+", encoding="utf-8") as store:
            fcntl.flock(store.fileno(), fcntl.LOCK_EX)
            store.seek(0)
            raw = store.read()
            try:
                used = json.loads(raw) if raw else {"op_ids": [], "approval_ids": []}
            except json.JSONDecodeError:
                return False, "使用済み記録を読み取れません"
            if not isinstance(used, dict) or not isinstance(used.get("op_ids"), list) or not isinstance(
                used.get("approval_ids"), list
            ):
                return False, "使用済み記録の形が正しくありません"
            if op_id in used["op_ids"]:
                return False, "この操作はすでに使用されています"
            if approval_id in used["approval_ids"]:
                return False, "この承認はすでに使用されています"
            used["op_ids"].append(op_id)
            used["approval_ids"].append(approval_id)
            store.seek(0)
            store.truncate()
            json.dump(used, store, ensure_ascii=False, sort_keys=True)
            store.flush()
            os.fsync(store.fileno())
            return True, "送信できます"
    except OSError:
        return False, "使用済み記録を更新できません"


def _main() -> int:
    parser = argparse.ArgumentParser(description="送信直前の承認確認")
    parser.add_argument("--used", required=True, help="使用済み記録のJSONファイル")
    args = parser.parse_args()
    try:
        data = json.load(sys.stdin)
        request = data["request"]
        approvals = data["approvals"]
        now = data.get("now") or datetime.now(ZoneInfo("Asia/Tokyo"))
        if not isinstance(approvals, dict):
            raise ValueError("approvals はオブジェクトで指定してください")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        json.dump({"ok": False, "reason": f"入力を読めません: {exc}"}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 2
    ok, reason = verify(request, lambda approval_id: approvals.get(approval_id), args.used, now)
    json.dump({"ok": ok, "reason": reason}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
