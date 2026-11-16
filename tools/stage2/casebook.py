#!/usr/bin/env python3
"""予定ばん 第2段階試作：依頼と案件のストア（casebook）。

入力1件（依頼）と、そこから生まれる案件（1対多）を、3系統の状態を分けて持つ。
既存の queue.py / worklog.py とは独立した新規ファイルであり、それらの振る舞いを変えない。
スキル／ルーチンへの配線、本番ストアの利用、外部送信、カレンダー書き込みは行わない。

使い方:
  python3 casebook.py --file /tmp/casebook.json record --request-id req-001 --source mail --received-at "2026-11-17 01:00 JST"
  対象ストアは --file 必須（既定の本番パスは持たない）。
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# 3系統の語彙（混同しない）
# ---------------------------------------------------------------------------

# (1) 処理の成否（機械。入力1件）
PROC_RECEIVED = "受信済み"
PROC_ANALYZING = "分析中"
PROC_SAVED = "保存済み"
PROC_DONE = "完了"
PROC_RETRY = "失敗・要再試行"
PROCESSING_STATUSES = (
    PROC_RECEIVED,
    PROC_ANALYZING,
    PROC_SAVED,
    PROC_DONE,
    PROC_RETRY,
)

# (2) 案件の状態（人の判断。案件1件）。「承認待ち」はここだけ。
CASE_BEFORE_PROPOSAL = "提案前"
CASE_PROPOSING = "提案中"
CASE_WAIT_APPROVAL = "承認待ち"
CASE_APPROVED = "承認済み"
CASE_EXECUTING = "実行中"
CASE_EXEC_DONE = "実行完了"
CASE_OUTCOME_UNKNOWN = "実行結果不明（要照合）"
CASE_REJECTED = "却下"
CASE_EXPIRED = "期限切れ"
CASE_HOLD = "保留"
CASE_STATUSES = (
    CASE_BEFORE_PROPOSAL,
    CASE_PROPOSING,
    CASE_WAIT_APPROVAL,
    CASE_APPROVED,
    CASE_EXECUTING,
    CASE_EXEC_DONE,
    CASE_OUTCOME_UNKNOWN,
    CASE_REJECTED,
    CASE_EXPIRED,
    CASE_HOLD,
)

# (3) 外部への引き渡し・送信状態
HAND_BEFORE = "渡し前（未送信）"
HAND_UNCONFIRMED = "渡し済み（未確認）"
HAND_SENT = "送信完了"
HAND_REPLIED = "返事あり"
HANDOFF_STATUSES = (HAND_BEFORE, HAND_UNCONFIRMED, HAND_SENT, HAND_REPLIED)

# 返事待ちの追跡行は、送信完了のあとだけ作る
REPLY_WAITING = "返事待ち"

# 処理系統に置いてはいけない語（案件側・送信側）
FORBIDDEN_IN_PROCESSING = CASE_STATUSES + HANDOFF_STATUSES + (REPLY_WAITING,)

# 案件系統に置いてはいけない語（処理側・送信側の専用語）
FORBIDDEN_IN_CASE = PROCESSING_STATUSES + HANDOFF_STATUSES + (REPLY_WAITING,)

# 送信系統に置いてはいけない語
FORBIDDEN_IN_HANDOFF = PROCESSING_STATUSES + CASE_STATUSES + (REPLY_WAITING,)

# 処理の許可遷移（同じ状態への再指定は冪等で許可）
PROCESSING_TRANSITIONS: dict[str, frozenset[str]] = {
    PROC_RECEIVED: frozenset({PROC_ANALYZING, PROC_SAVED, PROC_DONE, PROC_RETRY}),
    PROC_ANALYZING: frozenset({PROC_SAVED, PROC_DONE, PROC_RETRY}),
    PROC_SAVED: frozenset({PROC_DONE, PROC_RETRY}),
    PROC_RETRY: frozenset({PROC_ANALYZING, PROC_SAVED, PROC_DONE, PROC_RETRY}),
    PROC_DONE: frozenset(),
}

# 案件の許可遷移（同じ状態への再指定は冪等で許可）
CASE_TRANSITIONS: dict[str, frozenset[str]] = {
    CASE_BEFORE_PROPOSAL: frozenset(
        {CASE_PROPOSING, CASE_HOLD, CASE_EXPIRED, CASE_REJECTED}
    ),
    CASE_PROPOSING: frozenset(
        {CASE_WAIT_APPROVAL, CASE_HOLD, CASE_EXPIRED, CASE_REJECTED}
    ),
    CASE_WAIT_APPROVAL: frozenset(
        {CASE_APPROVED, CASE_REJECTED, CASE_EXPIRED, CASE_HOLD}
    ),
    CASE_APPROVED: frozenset(
        {CASE_EXECUTING, CASE_REJECTED, CASE_HOLD, CASE_EXPIRED}
    ),
    CASE_EXECUTING: frozenset(
        {CASE_EXEC_DONE, CASE_OUTCOME_UNKNOWN, CASE_HOLD}
    ),
    CASE_EXEC_DONE: frozenset(),
    # 実行結果不明からは、照合の結果として実行完了にだけ進める（再実行は不可）
    CASE_OUTCOME_UNKNOWN: frozenset({CASE_EXEC_DONE}),
    CASE_REJECTED: frozenset(),
    CASE_EXPIRED: frozenset(),
    CASE_HOLD: frozenset(
        {
            CASE_BEFORE_PROPOSAL,
            CASE_PROPOSING,
            CASE_WAIT_APPROVAL,
            CASE_APPROVED,
            CASE_EXPIRED,
            CASE_REJECTED,
        }
    ),
}

# 送信状態の許可遷移（confirm-sent / mark-reply 以外では進めない）
HANDOFF_TRANSITIONS: dict[str, frozenset[str]] = {
    HAND_BEFORE: frozenset({HAND_UNCONFIRMED}),
    HAND_UNCONFIRMED: frozenset({HAND_SENT}),
    HAND_SENT: frozenset({HAND_REPLIED}),
    HAND_REPLIED: frozenset(),
}

REQUEST_FIELDS = (
    "request_id",
    "source",
    "received_at",
    "processing_status",
    "saved",
    "no_save_reason",
    "failure_reason",
    "case_ids",
    "created_at",
    "updated_at",
)

CASE_FIELDS = (
    "case_id",
    "request_id",
    "case_key",
    "summary",
    "case_status",
    "handoff_status",
    "handed_off_at",
    "confirmation_received_at",
    "confirmation_ref",
    "reply_track",
    "outcome_unknown_reason",
    "retry_execution",
    "created_at",
    "updated_at",
)

JST_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?:\:(\d{2}))?\s*JST$"
)
CASE_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class CasebookError(Exception):
    """ユーザー向けの明確なエラー。"""


def now_jst() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S JST")


def require_jst(label: str, value: str) -> str:
    s = value.strip()
    if not JST_RE.match(s):
        raise CasebookError(
            f"{label} は JST 形式で指定してください（例: 2026-11-17 01:00 JST）"
        )
    return s


def ordered(fields: tuple[str, ...], rec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in fields:
        if key in rec:
            out[key] = rec[key]
    for key, val in rec.items():
        if key not in out:
            out[key] = val
    return out


def empty_store() -> dict[str, Any]:
    return {"version": 1, "requests": {}, "cases": {}}


def empty_request(request_id: str, source: str, received_at: str) -> dict[str, Any]:
    ts = now_jst()
    return {
        "request_id": request_id,
        "source": source,
        "received_at": received_at,
        "processing_status": PROC_RECEIVED,
        "saved": False,
        "no_save_reason": "",
        "failure_reason": "",
        "case_ids": [],
        "created_at": ts,
        "updated_at": ts,
    }


def empty_case(
    request_id: str,
    case_key: str,
    summary: str,
) -> dict[str, Any]:
    ts = now_jst()
    return {
        "case_id": make_case_id(request_id, case_key),
        "request_id": request_id,
        "case_key": case_key,
        "summary": summary,
        "case_status": CASE_BEFORE_PROPOSAL,
        "handoff_status": HAND_BEFORE,
        "handed_off_at": "",
        "confirmation_received_at": "",
        "confirmation_ref": "",
        "reply_track": None,
        "outcome_unknown_reason": "",
        "retry_execution": False,
        "created_at": ts,
        "updated_at": ts,
    }


def make_case_id(request_id: str, case_key: str) -> str:
    return f"{request_id}#{case_key}"


def content_key(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return digest[:16]


def validate_case_key(case_key: str) -> str:
    key = case_key.strip()
    if not CASE_KEY_RE.match(key):
        raise CasebookError(
            "案件キーは英数字と . _ : - のみ、1〜128文字で指定してください"
            "（依頼IDとの結合に使うため、# や空白は使えません）"
        )
    return key


def load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_store()
    if path.is_dir():
        raise CasebookError(f"ストアパスがディレクトリです: {path}")
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return empty_store()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CasebookError(f"{path}: JSON として読めません: {e}") from e
    if not isinstance(data, dict):
        raise CasebookError(f"{path}: オブジェクト（辞書）ではありません")
    requests = data.get("requests")
    cases = data.get("cases")
    if requests is None:
        requests = {}
    if cases is None:
        cases = {}
    if not isinstance(requests, dict) or not isinstance(cases, dict):
        raise CasebookError(f"{path}: requests / cases は辞書である必要があります")
    data["requests"] = requests
    data["cases"] = cases
    data.setdefault("version", 1)
    return data


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """ストア全体を一時ファイルへ書いてから rename する。半端なファイルを残さない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".casebook-", suffix=".json", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def lock_path_for(path: Path) -> Path:
    return path.parent / (path.name + ".lock")


def with_store(
    path: Path,
    fn: Callable[[dict[str, Any]], tuple[dict[str, Any] | None, Any]],
) -> Any:
    """排他ロックのうえストアを読み、必要なら原子的に書き戻す。

    fn(store) -> (new_store_or_None, result)
    new_store が None なら読むだけ。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lp = lock_path_for(path)
    with open(lp, "a+", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            store = load_store(path)
            new_store, result = fn(store)
            if new_store is not None:
                atomic_write_json(path, new_store)
            return result
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def get_request(store: dict[str, Any], request_id: str) -> dict[str, Any] | None:
    rec = store["requests"].get(request_id)
    return rec if isinstance(rec, dict) else None


def get_case(store: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    rec = store["cases"].get(case_id)
    return rec if isinstance(rec, dict) else None


def require_request(store: dict[str, Any], request_id: str) -> dict[str, Any]:
    rec = get_request(store, request_id)
    if rec is None:
        raise CasebookError(
            f"依頼ID {request_id} は登録されていません。先に record してください。"
        )
    return rec


def require_case(store: dict[str, Any], case_id: str) -> dict[str, Any]:
    rec = get_case(store, case_id)
    if rec is None:
        raise CasebookError(f"案件ID {case_id} は登録されていません。")
    return rec


def can_complete_processing(rec: dict[str, Any], no_save_reason: str | None) -> bool:
    reason = (
        no_save_reason if no_save_reason is not None else rec.get("no_save_reason") or ""
    )
    if rec.get("saved") is True or rec.get("processing_status") == PROC_SAVED:
        return True
    if isinstance(reason, str) and reason.strip():
        return True
    return False


def apply_processing(
    rec: dict[str, Any],
    new_status: str,
    no_save_reason: str | None,
    failure_reason: str | None,
) -> dict[str, Any]:
    current = rec.get("processing_status")
    if new_status in FORBIDDEN_IN_PROCESSING:
        raise CasebookError(
            f"「{new_status}」は処理の成否ではありません。"
            "承認待ちなどの案件状態、または引き渡し・送信状態は、それぞれ別フィールドで扱います。"
        )
    if new_status not in PROCESSING_STATUSES:
        allowed = " / ".join(PROCESSING_STATUSES)
        raise CasebookError(f"未知の処理状態です: {new_status}（使える値: {allowed}）")

    if current == new_status:
        if no_save_reason is not None:
            rec["no_save_reason"] = no_save_reason.strip()
        if new_status == PROC_RETRY and failure_reason is not None:
            rec["failure_reason"] = failure_reason.strip()
        rec["updated_at"] = now_jst()
        return rec

    if current == PROC_DONE and new_status != PROC_DONE:
        raise CasebookError(
            f"依頼ID {rec.get('request_id')} は既に完了しているため、"
            f"「{new_status}」へは戻せません。"
        )

    if new_status == PROC_RECEIVED and current != PROC_RECEIVED:
        raise CasebookError(
            "「受信済み」への戻しは record の再登録（冪等・既存を返す）だけです。"
            "set-processing では使えません。"
        )

    allowed = PROCESSING_TRANSITIONS.get(str(current), frozenset())
    if new_status not in allowed:
        raise CasebookError(
            f"処理状態を「{current}」から「{new_status}」へは遷移できません。"
        )

    if new_status == PROC_DONE and not can_complete_processing(rec, no_save_reason):
        raise CasebookError(
            f"依頼ID {rec.get('request_id')} を完了にできません。"
            "完了にするには、案件の保存済み（saved=true または 保存済み）"
            "か、保存不要の理由が必要です。"
        )

    if new_status == PROC_SAVED:
        rec["saved"] = True
        rec["failure_reason"] = ""

    if no_save_reason is not None:
        rec["no_save_reason"] = no_save_reason.strip()

    if new_status == PROC_RETRY:
        if failure_reason is not None:
            rec["failure_reason"] = failure_reason.strip()
    elif failure_reason is not None:
        rec["failure_reason"] = ""
    elif new_status != PROC_RETRY:
        rec["failure_reason"] = ""

    rec["processing_status"] = new_status
    rec["updated_at"] = now_jst()
    return rec


def apply_case_status(
    rec: dict[str, Any],
    new_status: str,
    *,
    reconciled: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    current = rec.get("case_status")
    if new_status in FORBIDDEN_IN_CASE:
        raise CasebookError(
            f"「{new_status}」は案件の状態ではありません。"
            "処理の成否や引き渡し・送信状態は、それぞれ別フィールドで扱います。"
        )
    if new_status not in CASE_STATUSES:
        allowed = " / ".join(CASE_STATUSES)
        raise CasebookError(f"未知の案件状態です: {new_status}（使える値: {allowed}）")

    if current == new_status:
        if reason is not None and new_status == CASE_OUTCOME_UNKNOWN:
            rec["outcome_unknown_reason"] = reason.strip()
        rec["updated_at"] = now_jst()
        return rec

    allowed = CASE_TRANSITIONS.get(str(current), frozenset())
    if new_status not in allowed:
        raise CasebookError(
            f"案件状態を「{current}」から「{new_status}」へは遷移できません。"
        )

    # 実行結果不明からの再実行（実行中へ戻す等）は上の遷移表で既に拒否される。
    # 実行完了へ進めるのは、照合済みの明示があるときに限る。
    if current == CASE_OUTCOME_UNKNOWN and new_status == CASE_EXEC_DONE:
        if not reconciled:
            raise CasebookError(
                "実行結果不明（要照合）の案件を実行完了にするには、"
                "照合済み（--reconciled）の指定が必要です。"
                "外部送信・カレンダー変更の再実行はしません。"
            )

    if new_status == CASE_OUTCOME_UNKNOWN:
        rec["retry_execution"] = False
        if reason is not None:
            rec["outcome_unknown_reason"] = reason.strip()
        elif not rec.get("outcome_unknown_reason"):
            rec["outcome_unknown_reason"] = "実行結果を確認できない"

    if new_status == CASE_EXEC_DONE:
        rec["retry_execution"] = False

    rec["case_status"] = new_status
    rec["updated_at"] = now_jst()
    return rec


def retry_decision(rec: dict[str, Any]) -> dict[str, Any]:
    """外部送信・カレンダー変更を再実行してよいか（安全側）。"""
    status = rec.get("case_status")
    case_id = rec.get("case_id")
    base = {
        "case_id": case_id,
        "case_status": status,
        "handoff_status": rec.get("handoff_status"),
        "retry_ok": False,
        "prompt_retry": False,
        "retry_execution": False,
    }
    if status == CASE_OUTCOME_UNKNOWN:
        base.update(
            {
                "message": (
                    "実行結果不明（要照合）です。"
                    "外部送信・カレンダー変更は再実行しません。"
                    "先に結果を照合してください。"
                ),
                "allowed": [
                    "結果を照合する",
                    "照合できたら --reconciled で実行完了にする",
                ],
                "forbidden": [
                    "外部送信の再実行",
                    "カレンダー変更の再実行",
                    "実行中への戻し",
                    "引き渡しのやり直し",
                ],
            }
        )
        return base
    if status in (CASE_EXEC_DONE, CASE_REJECTED, CASE_EXPIRED):
        base["message"] = (
            f"案件状態が「{status}」のため、外部送信・カレンダー変更は再実行しません。"
        )
        return base
    if status == CASE_EXECUTING:
        base["message"] = (
            "実行中です。結果が確認できるまで待ち、確認できなければ"
            "実行結果不明（要照合）にします。無条件では再実行しません。"
        )
        return base
    if rec.get("handoff_status") == HAND_UNCONFIRMED:
        base["message"] = (
            "渡し済み（未確認）です。確認が来るまで送信完了にせず、渡し直しもしません。"
        )
        return base
    # 再実行を「促す」ことは、この試作ではどの状態でもしない（安全側）。
    # 実行そのものは承認済みから実行中への遷移で表現する。
    if status == CASE_APPROVED:
        base.update(
            {
                "retry_ok": False,
                "prompt_retry": False,
                "message": (
                    "承認済みです。初回の実行は set-case で実行中へ進めます。"
                    "このコマンドは再実行の可否確認専用で、再実行は促しません。"
                ),
                "allowed": ["実行中へ進める（初回実行の開始）"],
                "forbidden": ["結果未確認のままの再実行"],
            }
        )
        return base
    base["message"] = (
        f"案件状態が「{status}」です。このコマンドは再実行を促しません。"
    )
    return base


def add_case_to_store(
    store: dict[str, Any],
    request_id: str,
    case_key: str,
    summary: str,
    *,
    mark_saved: bool,
) -> tuple[dict[str, Any], bool]:
    req = require_request(store, request_id)
    key = validate_case_key(case_key)
    case_id = make_case_id(request_id, key)
    existing = get_case(store, case_id)
    if existing is not None:
        return existing, False
    rec = empty_case(request_id, key, summary)
    store["cases"][case_id] = rec
    ids = list(req.get("case_ids") or [])
    if case_id not in ids:
        ids.append(case_id)
        req["case_ids"] = ids
    if mark_saved:
        req["saved"] = True
        if req.get("processing_status") in (PROC_RECEIVED, PROC_ANALYZING):
            req["processing_status"] = PROC_SAVED
            req["failure_reason"] = ""
    req["updated_at"] = now_jst()
    return rec, True


def findings_for_store(store: dict[str, Any]) -> list[dict[str, str]]:
    """整合性チェック。3系統の混同や不正を検出する（読むだけ）。"""
    issues: list[dict[str, str]] = []
    requests: dict[str, Any] = store.get("requests") or {}
    cases: dict[str, Any] = store.get("cases") or {}

    def add(target: str, code: str, message: str) -> None:
        issues.append({"対象": target, "種別": code, "内容": message})

    for rid, rec in requests.items():
        if not isinstance(rec, dict):
            add(str(rid), "形", "依頼レコードが辞書ではありません")
            continue
        status = rec.get("processing_status")
        if status in FORBIDDEN_IN_PROCESSING:
            add(
                rid,
                "系統の混同",
                f"処理の成否に案件または送信の語が入っています: {status}",
            )
        elif status not in PROCESSING_STATUSES:
            add(rid, "未知", f"未知の処理状態です: {status}")
        saved = rec.get("saved") is True or status == PROC_SAVED
        reason = (rec.get("no_save_reason") or "").strip()
        if status == PROC_DONE and not saved and not reason:
            add(
                rid,
                "完了条件",
                "完了なのに、保存済みでも保存不要の理由でもありません",
            )
        case_ids = rec.get("case_ids") or []
        if not isinstance(case_ids, list):
            add(rid, "形", "case_ids が配列ではありません")
            case_ids = []
        for cid in case_ids:
            child = cases.get(cid)
            if child is None:
                add(rid, "対応", f"案件ID {cid} が案件ストアにありません")
            elif isinstance(child, dict) and child.get("request_id") != rid:
                add(
                    rid,
                    "対応",
                    f"案件ID {cid} の依頼IDが一致しません（{child.get('request_id')}）",
                )
        if case_ids and not saved and status not in (PROC_RETRY,):
            add(
                rid,
                "保存",
                "案件があるのに、処理側が保存済みになっていません",
            )

    for cid, rec in cases.items():
        if not isinstance(rec, dict):
            add(str(cid), "形", "案件レコードが辞書ではありません")
            continue
        cstatus = rec.get("case_status")
        hstatus = rec.get("handoff_status")
        if cstatus in FORBIDDEN_IN_CASE:
            add(
                cid,
                "系統の混同",
                f"案件の状態に処理または送信の語が入っています: {cstatus}",
            )
        elif cstatus not in CASE_STATUSES:
            add(cid, "未知", f"未知の案件状態です: {cstatus}")
        if hstatus in FORBIDDEN_IN_HANDOFF:
            add(
                cid,
                "系統の混同",
                f"引き渡し・送信状態に処理または案件の語が入っています: {hstatus}",
            )
        elif hstatus not in HANDOFF_STATUSES:
            add(cid, "未知", f"未知の引き渡し・送信状態です: {hstatus}")

        conf_ref = (rec.get("confirmation_ref") or "").strip()
        conf_at = (rec.get("confirmation_received_at") or "").strip()
        if hstatus in (HAND_SENT, HAND_REPLIED):
            if not conf_ref or not conf_at:
                add(
                    cid,
                    "確認欠落",
                    "送信完了（または返事あり）なのに、Bot間の完了確認が記録されていません",
                )
        if hstatus == HAND_SENT and rec.get("reply_track"):
            # 送信完了後に追跡行を作るのは正常。返事ありへはまだ進めていなくてもよい。
            pass
        reply = rec.get("reply_track")
        if reply:
            if hstatus not in (HAND_SENT, HAND_REPLIED):
                add(
                    cid,
                    "返事待ちの時期",
                    "返事待ちの追跡行がありますが、送信完了の前です",
                )
        if hstatus == HAND_REPLIED and not reply:
            add(cid, "返事待ちの欠落", "返事ありなのに、追跡行がありません")

        if cstatus == CASE_OUTCOME_UNKNOWN and rec.get("retry_execution") is True:
            add(
                cid,
                "再実行フラグ",
                "実行結果不明（要照合）なのに、再実行フラグが立っています",
            )

        rid = rec.get("request_id")
        if not rid or rid not in requests:
            add(cid, "対応", f"親の依頼ID {rid} が依頼ストアにありません")
        else:
            parent = requests[rid]
            ids = parent.get("case_ids") or []
            if isinstance(parent, dict) and cid not in ids:
                add(cid, "対応", "親依頼の case_ids にこの案件が載っていません")

    return issues


def print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# コマンド
# ---------------------------------------------------------------------------

def cmd_record(args: argparse.Namespace) -> int:
    request_id = args.request_id.strip()
    source = args.source.strip()
    received_at = require_jst("received-at", args.received_at)
    if not request_id or not source:
        raise CasebookError("request-id / source / received-at はすべて必須です")
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        existing = get_request(store, request_id)
        if existing is not None:
            warnings: list[str] = []
            if existing.get("source") != source:
                warnings.append(
                    f"既存の source と異なります（既存: {existing.get('source')} / 指定: {source}）。既存をそのまま返します。"
                )
            if existing.get("received_at") != received_at:
                warnings.append(
                    f"既存の received_at と異なります。既存をそのまま返します。"
                )
            return None, {
                "ok": True,
                "created": False,
                "request_id": request_id,
                "processing_status": existing.get("processing_status"),
                "file": str(path),
                "note": "同じ依頼IDは既に登録済みのため追加しません（冪等）",
                "record": ordered(REQUEST_FIELDS, existing),
                "warnings": warnings,
            }
        rec = empty_request(request_id, source, received_at)
        store["requests"][request_id] = rec
        return store, {
            "ok": True,
            "created": True,
            "request_id": request_id,
            "processing_status": rec["processing_status"],
            "file": str(path),
            "record": ordered(REQUEST_FIELDS, rec),
        }

    result = with_store(path, mut)
    for w in result.get("warnings") or []:
        print(f"警告: {w}", file=sys.stderr)
    print_json({k: v for k, v in result.items() if k != "warnings" or v})
    return 0


def cmd_add_case(args: argparse.Namespace) -> int:
    request_id = args.request_id.strip()
    summary = (args.summary or "").strip()
    case_key = args.case_key
    if args.content and not case_key:
        case_key = content_key(args.content)
    if not case_key:
        raise CasebookError("case-key か content（内容ハッシュ用）のいずれかを指定してください")
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec, created = add_case_to_store(
            store,
            request_id,
            case_key,
            summary,
            mark_saved=bool(args.mark_saved),
        )
        # 既存に対してはストアを書かない（状態を上書きしない）
        return (store if created else None), {
            "ok": True,
            "created": created,
            "request_id": request_id,
            "case_id": rec["case_id"],
            "case_key": rec["case_key"],
            "file": str(path),
            "note": (
                None
                if created
                else "同じ案件IDは既に登録済みのため追加しません（冪等）"
            ),
            "record": ordered(CASE_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json({k: v for k, v in result.items() if v is not None})
    return 0


def cmd_add_cases(args: argparse.Namespace) -> int:
    request_id = args.request_id.strip()
    path = Path(args.file)
    raw = sys.stdin.read()
    if not raw.strip():
        raise CasebookError(
            "標準入力に案件の配列（JSON）を渡してください。"
            '例: [{"case_key":"a","summary":"候補1"}]'
        )
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CasebookError(f"標準入力の JSON が読めません: {e}") from e
    if not isinstance(items, list) or not items:
        raise CasebookError("標準入力は、空でない配列である必要があります")

    def mut(store: dict[str, Any]):
        require_request(store, request_id)
        created_ids: list[str] = []
        existing_ids: list[str] = []
        records: list[dict[str, Any]] = []
        # 1回の書き込みでまとめて追加（途中失敗時は全体を書かない）
        for i, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise CasebookError(f"{i}件目がオブジェクトではありません")
            key = item.get("case_key")
            content = item.get("content")
            if not key and content:
                key = content_key(str(content))
            if not key:
                raise CasebookError(f"{i}件目に case_key も content もありません")
            rec, created = add_case_to_store(
                store,
                request_id,
                str(key),
                str(item.get("summary") or ""),
                mark_saved=bool(args.mark_saved),
            )
            records.append(ordered(CASE_FIELDS, rec))
            if created:
                created_ids.append(rec["case_id"])
            else:
                existing_ids.append(rec["case_id"])
        changed = bool(created_ids)
        return (store if changed else None), {
            "ok": True,
            "request_id": request_id,
            "created_count": len(created_ids),
            "existing_count": len(existing_ids),
            "created_ids": created_ids,
            "existing_ids": existing_ids,
            "file": str(path),
            "records": records,
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_set_processing(args: argparse.Namespace) -> int:
    request_id = args.request_id.strip()
    new_status = args.status.strip()
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_request(store, request_id)
        apply_processing(rec, new_status, args.no_save_reason, args.failure_reason)
        return store, {
            "ok": True,
            "request_id": request_id,
            "processing_status": rec["processing_status"],
            "saved": rec.get("saved", False),
            "file": str(path),
            "record": ordered(REQUEST_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_set_case(args: argparse.Namespace) -> int:
    case_id = args.case_id.strip()
    new_status = args.status.strip()
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_case(store, case_id)
        apply_case_status(
            rec,
            new_status,
            reconciled=bool(args.reconciled),
            reason=args.reason,
        )
        return store, {
            "ok": True,
            "case_id": case_id,
            "case_status": rec["case_status"],
            "handoff_status": rec["handoff_status"],
            "file": str(path),
            "record": ordered(CASE_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    case_id = args.case_id.strip()
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_case(store, case_id)
        current = rec.get("handoff_status")
        if rec.get("case_status") == CASE_OUTCOME_UNKNOWN:
            raise CasebookError(
                "実行結果不明（要照合）のため、引き渡しのやり直しはしません。"
            )
        if current == HAND_UNCONFIRMED:
            return None, {
                "ok": True,
                "changed": False,
                "case_id": case_id,
                "handoff_status": current,
                "note": "既に渡し済み（未確認）です。確認が来るまで待ち、渡し直しはしません。",
                "record": ordered(CASE_FIELDS, rec),
            }
        if current in (HAND_SENT, HAND_REPLIED):
            raise CasebookError(
                f"引き渡し・送信状態が「{current}」のため、渡し直しはできません。"
            )
        if current != HAND_BEFORE:
            raise CasebookError(
                f"引き渡し・送信状態を「{current}」から渡し済み（未確認）へは遷移できません。"
            )
        rec["handoff_status"] = HAND_UNCONFIRMED
        rec["handed_off_at"] = now_jst()
        rec["updated_at"] = now_jst()
        return store, {
            "ok": True,
            "changed": True,
            "case_id": case_id,
            "handoff_status": rec["handoff_status"],
            "note": "渡し済み（未確認）にしました。相手ボットの送信確認が来るまで送信完了にはしません。",
            "record": ordered(CASE_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_confirm_sent(args: argparse.Namespace) -> int:
    case_id = args.case_id.strip()
    confirmation_ref = (args.confirmation_ref or "").strip()
    if not confirmation_ref:
        raise CasebookError(
            "送信完了にするには、相手ボットからの確認参照（--confirmation-ref）が必要です。"
            "確認が無いまま送信完了にはできません。"
        )
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_case(store, case_id)
        current = rec.get("handoff_status")
        if current == HAND_SENT or current == HAND_REPLIED:
            return None, {
                "ok": True,
                "changed": False,
                "case_id": case_id,
                "handoff_status": current,
                "note": "既に送信完了（または返事あり）です。追加の確認では状態を変えません。",
                "record": ordered(CASE_FIELDS, rec),
            }
        if current != HAND_UNCONFIRMED:
            raise CasebookError(
                f"引き渡し・送信状態が「{current}」のため、送信完了にできません。"
                "先に handoff で渡し済み（未確認）にし、相手ボットの確認を受け取ってからにしてください。"
                "確認無しで送信完了にはできません。"
            )
        rec["handoff_status"] = HAND_SENT
        rec["confirmation_ref"] = confirmation_ref
        rec["confirmation_received_at"] = now_jst()
        rec["updated_at"] = now_jst()
        return store, {
            "ok": True,
            "changed": True,
            "case_id": case_id,
            "handoff_status": rec["handoff_status"],
            "confirmation_ref": confirmation_ref,
            "record": ordered(CASE_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_mark_reply(args: argparse.Namespace) -> int:
    case_id = args.case_id.strip()
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_case(store, case_id)
        current = rec.get("handoff_status")
        if current == HAND_REPLIED:
            if not rec.get("reply_track"):
                rec["reply_track"] = {
                    "status": REPLY_WAITING,
                    "created_at": now_jst(),
                    "note": "送信完了後の追跡行",
                }
                rec["updated_at"] = now_jst()
                return store, {
                    "ok": True,
                    "changed": True,
                    "case_id": case_id,
                    "handoff_status": current,
                    "record": ordered(CASE_FIELDS, rec),
                }
            return None, {
                "ok": True,
                "changed": False,
                "case_id": case_id,
                "handoff_status": current,
                "note": "既に返事ありです。",
                "record": ordered(CASE_FIELDS, rec),
            }
        if current != HAND_SENT:
            raise CasebookError(
                f"引き渡し・送信状態が「{current}」のため、返事あり／返事待ちの追跡行は作れません。"
                "追跡行は送信完了のあとだけ作ります。"
            )
        rec["handoff_status"] = HAND_REPLIED
        if not rec.get("reply_track"):
            rec["reply_track"] = {
                "status": REPLY_WAITING,
                "created_at": now_jst(),
                "note": "送信完了後の追跡行",
            }
        rec["updated_at"] = now_jst()
        return store, {
            "ok": True,
            "changed": True,
            "case_id": case_id,
            "handoff_status": rec["handoff_status"],
            "record": ordered(CASE_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_mark_outcome_unknown(args: argparse.Namespace) -> int:
    case_id = args.case_id.strip()
    reason = (args.reason or "").strip() or "実行結果を確認できない"
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_case(store, case_id)
        apply_case_status(rec, CASE_OUTCOME_UNKNOWN, reason=reason)
        rec["retry_execution"] = False
        return store, {
            "ok": True,
            "case_id": case_id,
            "case_status": rec["case_status"],
            "retry_execution": False,
            "prompt_retry": False,
            "note": "実行結果不明（要照合）にしました。外部送信・カレンダー変更は再実行しません。",
            "record": ordered(CASE_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_can_retry_execution(args: argparse.Namespace) -> int:
    case_id = args.case_id.strip()
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_case(store, case_id)
        decision = retry_decision(rec)
        return None, decision

    result = with_store(path, mut)
    print_json(result)
    # 再実行不可でも点検自体は成功（終了コード 0）。促さないことが目的。
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        issues = findings_for_store(store)
        return None, {
            "依頼件数": len(store.get("requests") or {}),
            "案件件数": len(store.get("cases") or {}),
            "問題件数": len(issues),
            "問題": issues,
        }

    result = with_store(path, mut)
    if args.json:
        print_json(result)
    else:
        print(f"依頼: {result['依頼件数']}件")
        print(f"案件: {result['案件件数']}件")
        print(f"問題: {result['問題件数']}件")
        if result["問題"]:
            for issue in result["問題"]:
                print(f"  - [{issue['種別']}] {issue['対象']}: {issue['内容']}")
        else:
            print("  矛盾はありません。")
    return 1 if result["問題件数"] else 0


def cmd_show(args: argparse.Namespace) -> int:
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        if args.request_id:
            rec = require_request(store, args.request_id.strip())
            kids = [
                ordered(CASE_FIELDS, store["cases"][cid])
                for cid in rec.get("case_ids") or []
                if cid in store["cases"]
            ]
            return None, {
                "request": ordered(REQUEST_FIELDS, rec),
                "cases": kids,
            }
        rec = require_case(store, args.case_id.strip())
        parent = get_request(store, rec.get("request_id", ""))
        return None, {
            "case": ordered(CASE_FIELDS, rec),
            "request": ordered(REQUEST_FIELDS, parent) if parent else None,
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="casebook.py",
        description=(
            "予定ばん 第2段階試作。依頼（処理の成否）と案件（人の判断）と"
            "引き渡し・送信状態を別フィールドで持つ。--file 必須。"
        ),
    )
    p.add_argument(
        "--file",
        required=True,
        help="対象ストア（JSON辞書）。検証は一時ファイルを指定すること。既定の本番パスは持たない",
    )
    sub = p.add_subparsers(dest="command", required=True)

    rp = sub.add_parser("record", help="依頼1件を受信済みで登録（依頼IDで冪等）")
    rp.add_argument("--request-id", required=True, help="依頼ID（入力1件の識別子）")
    rp.add_argument("--source", required=True, help="入口の種類（汎用名）")
    rp.add_argument("--received-at", required=True, help="受信時刻（例: 2026-11-17 01:00 JST）")
    rp.set_defaults(func=cmd_record)

    ap = sub.add_parser("add-case", help="依頼から案件を1件作成（安定IDで冪等）")
    ap.add_argument("--request-id", required=True)
    ap.add_argument("--case-key", default=None, help="依頼内で安定した案件キー（省略時は --content のハッシュ）")
    ap.add_argument("--content", default=None, help="内容の正規化文字列（キー省略時にハッシュ化して使う）")
    ap.add_argument("--summary", default="", help="案件の要約（固有値を書かない）")
    ap.add_argument("--mark-saved", action="store_true", help="依頼を保存済みにする")
    ap.set_defaults(func=cmd_add_case)

    mp = sub.add_parser("add-cases", help="依頼から複数案件を一括作成（標準入力JSON配列。原子的・冪等）")
    mp.add_argument("--request-id", required=True)
    mp.add_argument("--mark-saved", action="store_true")
    mp.set_defaults(func=cmd_add_cases)

    sp = sub.add_parser("set-processing", help="処理の成否を遷移する")
    sp.add_argument("--request-id", required=True)
    sp.add_argument("--status", required=True, help="分析中／保存済み／完了／失敗・要再試行")
    sp.add_argument("--no-save-reason", default=None)
    sp.add_argument("--failure-reason", default=None)
    sp.set_defaults(func=cmd_set_processing)

    cp = sub.add_parser("set-case", help="案件の状態を遷移する")
    cp.add_argument("--case-id", required=True)
    cp.add_argument("--status", required=True)
    cp.add_argument("--reason", default=None, help="実行結果不明などにするときの理由")
    cp.add_argument(
        "--reconciled",
        action="store_true",
        help="照合済み。実行結果不明から実行完了へ進めるときだけ使う（再実行ではない）",
    )
    cp.set_defaults(func=cmd_set_case)

    hp = sub.add_parser("handoff", help="外部ボットへ引き渡す（渡し済み・未確認）。渡し直しはしない")
    hp.add_argument("--case-id", required=True)
    hp.set_defaults(func=cmd_handoff)

    kp = sub.add_parser(
        "confirm-sent",
        help="相手ボットの送信確認を受けて送信完了にする（確認参照が必須）",
    )
    kp.add_argument("--case-id", required=True)
    kp.add_argument("--confirmation-ref", required=True, help="相手ボットからの確認参照")
    kp.set_defaults(func=cmd_confirm_sent)

    yp = sub.add_parser("mark-reply", help="送信完了後に返事ありとし、返事待ちの追跡行を作る")
    yp.add_argument("--case-id", required=True)
    yp.set_defaults(func=cmd_mark_reply)

    up = sub.add_parser(
        "mark-outcome-unknown",
        help="実行結果不明（要照合）にする。再実行は促さない",
    )
    up.add_argument("--case-id", required=True)
    up.add_argument("--reason", default=None)
    up.set_defaults(func=cmd_mark_outcome_unknown)

    tp = sub.add_parser(
        "can-retry-execution",
        help="外部送信・カレンダー変更を再実行してよいか確認する（読むだけ。不明時は促さない）",
    )
    tp.add_argument("--case-id", required=True)
    tp.set_defaults(func=cmd_can_retry_execution)

    ch = sub.add_parser("check", help="整合性チェック（読むだけ）")
    ch.add_argument("--json", action="store_true")
    ch.set_defaults(func=cmd_check)

    sh = sub.add_parser("show", help="依頼または案件を表示")
    g = sh.add_mutually_exclusive_group(required=True)
    g.add_argument("--request-id", default=None)
    g.add_argument("--case-id", default=None)
    sh.set_defaults(func=cmd_show)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CasebookError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
