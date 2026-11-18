"""旧casebookとNotion書き出しからの重複しない移し替え。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from casebook import (
    CASE_APPROVED, CASE_BEFORE_PROPOSAL, CASE_EXPIRED, CASE_EXEC_DONE,
    CASE_EXECUTING, CASE_HOLD, CASE_OUTCOME_UNKNOWN, CASE_PROPOSING,
    CASE_REJECTED, CASE_WAIT_APPROVAL, JST_RE,
)


STATUS_MAP = {
    CASE_BEFORE_PROPOSAL: "提案前", CASE_PROPOSING: "確認中",
    CASE_WAIT_APPROVAL: "承認待ち", CASE_APPROVED: "承認済み",
    CASE_EXECUTING: "実行中", CASE_EXEC_DONE: "済んだ",
    CASE_OUTCOME_UNKNOWN: "実行結果不明（要照合）", CASE_REJECTED: "見送り",
    CASE_EXPIRED: "見送り", CASE_HOLD: "保留",
}
_JST = timezone(timedelta(hours=9))


def _iso(value):
    if not value:
        raise ValueError("日時がありません")
    match = JST_RE.match(str(value).strip())
    if match:
        year, month, day, hour, minute, second = match.groups()
        dt = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0), tzinfo=_JST)
        return dt.isoformat()
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()


def _digest_input(source, native_ref):
    return hashlib.sha256(f"{source}|{native_ref}".encode("utf-8")).hexdigest()[:24]


def _result(imported, skipped, unmapped):
    return {"imported": imported, "skipped": skipped, "unmapped": unmapped}


def from_casebook(ledger, casebook_json_path, now, dry_run=False):
    data = json.loads(Path(casebook_json_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("requests", {}), dict) or not isinstance(data.get("cases", {}), dict):
        raise ValueError("casebook JSON の形が不正です")
    at = _iso(now)
    imported = skipped = 0
    unmapped = []
    requests = data.get("requests", {})
    cases = data.get("cases", {})
    request_inputs = {}
    for key, rec in requests.items():
        if not isinstance(rec, dict):
            unmapped.append({"type": "request", "id": key, "reason": "依頼の形が不正です"}); continue
        request_id = rec.get("request_id", key)
        source = rec.get("source")
        try:
            received_at = _iso(rec.get("received_at"))
        except (TypeError, ValueError):
            unmapped.append({"type": "request", "id": request_id, "reason": "受信日時を読めません"}); continue
        if not isinstance(source, str) or not source or not isinstance(request_id, str) or not request_id:
            unmapped.append({"type": "request", "id": request_id, "reason": "source または request_id がありません"}); continue
        input_id = _digest_input(source, request_id)
        request_inputs[request_id] = input_id
        exists = ledger.conn.execute("SELECT 1 FROM raw_inputs WHERE input_id=?", (input_id,)).fetchone() is not None
        if exists:
            skipped += 1
        elif dry_run:
            imported += 1
        else:
            ledger.ingest({"source": source, "native_ref": request_id, "received_at": received_at,
                           "body_text": "", "body_masked": "", "summary_masked": "", "sender_role": "旧台帳"}, now=at)
            imported += 1
    for case_id, rec in cases.items():
        if not isinstance(rec, dict):
            unmapped.append({"type": "case", "id": case_id, "reason": "案件の形が不正です"}); continue
        source_status = rec.get("case_status")
        status = STATUS_MAP.get(source_status)
        if status is None:
            unmapped.append({"type": "case", "id": case_id, "status": source_status, "reason": "対応する状態語がありません"}); continue
        if ledger.get_case(case_id) is not None:
            skipped += 1; continue
        request_id = rec.get("request_id")
        input_id = request_inputs.get(request_id)
        if input_id is None and request_id:
            # A request can already have been imported before this migration run.
            req = ledger.conn.execute("SELECT input_id FROM raw_inputs WHERE native_ref=? ORDER BY received_at DESC LIMIT 1", (request_id,)).fetchone()
            input_id = req[0] if req else None
        title = str(rec.get("summary") or rec.get("case_key") or case_id)
        patch = {"case_id": case_id, "kind": "旧台帳", "title": title,
                 "summary": str(rec.get("summary") or ""), "cause_input_id": input_id,
                 "status": status, "certainty": "推測", "created_at": at, "updated_at": at}
        if dry_run:
            imported += 1
        else:
            ledger.upsert_case(patch, actor="移行", reason="casebookから移行", basis=str(request_id or ""), now=at)
            imported += 1
    return _result(imported, skipped, unmapped)


def from_notion_export(ledger, rows: list[dict], mapping: dict, now, dry_run=False):
    if not isinstance(mapping, dict) or not isinstance(rows, list):
        raise ValueError("rows と mapping の形が不正です")
    at = _iso(now)
    imported = skipped = 0
    unmapped = []
    seen_refs = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            unmapped.append({"type": "notion", "row": index, "reason": "行が辞書ではありません"}); continue
        mapped = {target: row.get(column) for column, target in mapping.items()}
        native_ref = mapped.get("native_ref")
        if not isinstance(native_ref, str) or not native_ref:
            unmapped.append({"type": "notion", "row": index, "reason": "native_ref の列がありません"}); continue
        if native_ref in seen_refs:
            skipped += 1; continue
        input_id = _digest_input("notion", native_ref)
        existing_input = ledger.conn.execute("SELECT 1 FROM raw_inputs WHERE input_id=?", (input_id,)).fetchone()
        case_id = "notion-" + hashlib.sha256(native_ref.encode("utf-8")).hexdigest()[:24]
        if existing_input or ledger.get_case(case_id):
            skipped += 1; continue
        source_status = mapped.get("status") or "未確定"
        status = source_status if source_status in {"提案前", "未確定", "保留", "確認中", "返事なし", "承認待ち", "承認済み", "実行中", "決まった", "準備中", "済んだ", "見送り", "取消", "実行結果不明（要照合）"} else STATUS_MAP.get(source_status)
        if status is None:
            unmapped.append({"type": "notion", "row": index, "native_ref": native_ref, "status": source_status, "reason": "対応する状態語がありません"}); continue
        title = mapped.get("title")
        if not isinstance(title, str) or not title:
            unmapped.append({"type": "notion", "row": index, "native_ref": native_ref, "reason": "title がありません"}); continue
        try:
            received_at = _iso(mapped.get("received_at") or at)
        except (TypeError, ValueError):
            unmapped.append({"type": "notion", "row": index, "native_ref": native_ref, "reason": "受信日時を読めません"}); continue
        patch = {"case_id": case_id, "kind": str(mapped.get("kind") or "Notion"), "title": title,
                 "summary": str(mapped.get("summary") or ""), "status": status,
                 "certainty": str(mapped.get("certainty") or "推測"), "urgency": str(mapped.get("urgency") or "朝でよい"),
                 "due_at": mapped.get("due_at"), "event_start": mapped.get("event_start"), "event_end": mapped.get("event_end"),
                 "next_review_at": mapped.get("next_review_at"), "owner_role": mapped.get("owner_role"),
                 "created_at": at, "updated_at": at}
        seen_refs.add(native_ref)
        if dry_run:
            imported += 1
        else:
            with ledger.tx():
                iid, created = ledger.ingest({"source": "notion", "native_ref": native_ref, "received_at": received_at,
                    "body_text": "", "body_masked": "", "summary_masked": "", "sender_role": "本人"}, now=at)
                if not created:
                    skipped += 1; continue
                patch["cause_input_id"] = iid
                ledger.upsert_case(patch, actor="移行", reason="Notion書き出しから移行", basis=native_ref, now=at)
                imported += 1
    return _result(imported, skipped, unmapped)
