"""旧casebookとNotion書き出しからの重複しない移し替え。"""
from __future__ import annotations

import hashlib
import json
import re
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

LEDGER_STATUSES = {
    "提案前", "未確定", "保留", "確認中", "返事なし", "承認待ち", "承認済み",
    "実行中", "決まった", "準備中", "済んだ", "見送り", "取消", "実行結果不明（要照合）",
}

# Notion候補箱の区分。予定なしは案件を作らず入力だけ残す。
NOTION_STATUS_MAP = {
    "予定あり": "未確定",
    "予定なし": None,
    "迷い": "未確定",
    "確認待ち": "確認中",
    "返事待ち": "確認中",
    "承認待ち": "承認待ち",
    "保留": "保留",
    "見送り": "見送り",
    "登録済み": "決まった",
}

CERTAINTY_MAP = {"高": "確定", "中": "推測", "低": "未定"}
CERTAINTY_PASS = {"確定", "未定", "推測"}
MUTE_MAP = {
    "なし": "なし",
    "自動（印のみ）": "自動（印のみ）",
    "自動印のみ": "自動（印のみ）",
    "本人が明示": "本人が明示",
    "本人が明示（通知しない）": "本人が明示",
}
INPUT_ONLY = object()
_JST = timezone(timedelta(hours=9))
_DURATION_HM = re.compile(r"^(?:(\d+(?:\.\d+)?)時間(?:半)?)?(?:(\d+(?:\.\d+)?)分)?$")
_DURATION_HALF = re.compile(r"^(\d+)時間半$")
_DURATION_HOUR = re.compile(r"^(\d+(?:\.\d+)?)時間$")
_DURATION_MIN = re.compile(r"^(\d+(?:\.\d+)?)分$")
_DURATION_NUM = re.compile(r"^(\d+)$")
_YES = {1, True, "1", "__YES__", "はい", "true", "True"}
_NO = {0, False, "0", "__NO__", "いいえ", "false", "False"}


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


def map_status(source_status):
    """台帳の状態語、または入力だけ（予定なし）。分からなければ ValueError。"""
    if source_status in (None, ""):
        return "未確定"
    if source_status in LEDGER_STATUSES:
        return source_status
    if source_status in NOTION_STATUS_MAP:
        mapped = NOTION_STATUS_MAP[source_status]
        return INPUT_ONLY if mapped is None else mapped
    mapped = STATUS_MAP.get(source_status)
    if mapped is None:
        raise ValueError("対応する状態語がありません")
    return mapped


def map_certainty(value):
    """高・中・低を確定・推測・未定へ。空は推測。分からなければ ValueError。"""
    if value in (None, ""):
        return "推測"
    text = str(value).strip()
    if text in CERTAINTY_PASS:
        return text
    if text in CERTAINTY_MAP:
        return CERTAINTY_MAP[text]
    raise ValueError("対応する確実度がありません")


def parse_duration_min(value):
    """所要を分の整数へ。空は None。読めなければ ValueError。"""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("所要を読めません")
    if isinstance(value, (int, float)):
        if value < 0 or int(value) != value:
            raise ValueError("所要を読めません")
        return int(value)
    text = str(value).strip().replace(" ", "").replace("　", "")
    if not text:
        return None
    half = _DURATION_HALF.match(text)
    if half:
        return int(half.group(1)) * 60 + 30
    hour = _DURATION_HOUR.match(text)
    if hour:
        return int(float(hour.group(1)) * 60)
    minute = _DURATION_MIN.match(text)
    if minute:
        return int(float(minute.group(1)))
    both = _DURATION_HM.match(text)
    if both and (both.group(1) or both.group(2)):
        hours = float(both.group(1) or 0)
        mins = float(both.group(2) or 0)
        total = int(hours * 60 + mins)
        if total < 0:
            raise ValueError("所要を読めません")
        return total
    if _DURATION_NUM.match(text):
        return int(text)
    raise ValueError("所要を読めません")


def map_mute_state(value):
    if value in (None, ""):
        return None
    mapped = MUTE_MAP.get(str(value).strip())
    if mapped is None:
        raise ValueError("対応するミュートの状態がありません")
    return mapped


def map_tentative(value):
    if value in (None, ""):
        return 0
    if value in _YES:
        return 1
    if value in _NO:
        return 0
    raise ValueError("［仮］の値を読めません")


def normalize_inferred_fields(value):
    if value in (None, ""):
        return "[]"
    if isinstance(value, (list, tuple)):
        names = [str(item) for item in value]
        return json.dumps(names, ensure_ascii=False)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError("推測で埋めた項目の形が不正です")
            return json.dumps([str(item) for item in parsed], ensure_ascii=False)
        names = [part.strip() for part in re.split(r"[,、]", text) if part.strip()]
        return json.dumps(names, ensure_ascii=False)
    raise ValueError("推測で埋めた項目の形が不正です")


def _choice(value, allowed, label):
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text not in allowed:
        raise ValueError(f"対応する{label}がありません")
    return text


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


def _map_row_fields(mapped, native_ref, index):
    """区分・確実度・新しい列を変換する。失敗時は unmapped 用の辞書を返す。"""
    try:
        status = map_status(mapped.get("status"))
    except ValueError:
        return None, {"type": "notion", "row": index, "native_ref": native_ref, "status": mapped.get("status"), "reason": "対応する状態語がありません"}
    schedule_related = None
    try:
        schedule_related = _choice(mapped.get("schedule_related"), {"はい", "迷い", "いいえ"}, "予定に関係あるか")
    except ValueError:
        return None, {"type": "notion", "row": index, "native_ref": native_ref, "reason": "対応する予定に関係あるかがありません"}
    input_only = status is INPUT_ONLY or schedule_related == "いいえ"
    title = mapped.get("title")
    if not input_only and (not isinstance(title, str) or not title):
        return None, {"type": "notion", "row": index, "native_ref": native_ref, "reason": "title がありません"}
    try:
        received_at = _iso(mapped.get("received_at") or None)
    except (TypeError, ValueError):
        # 受信日時が無いときは呼び出し側の now を後で使う。ここは値が有るのに読めない場合。
        if mapped.get("received_at"):
            return None, {"type": "notion", "row": index, "native_ref": native_ref, "reason": "受信日時を読めません"}
        received_at = None
    try:
        certainty = map_certainty(mapped.get("certainty"))
    except ValueError:
        return None, {"type": "notion", "row": index, "native_ref": native_ref, "reason": "対応する確実度がありません"}
    duration_min = None
    if mapped.get("duration_min") not in (None, ""):
        try:
            duration_min = parse_duration_min(mapped.get("duration_min"))
        except (TypeError, ValueError):
            duration_min = None
    reply_by = mapped.get("reply_by")
    if reply_by not in (None, ""):
        try:
            reply_by = _iso(reply_by)
        except (TypeError, ValueError):
            return None, {"type": "notion", "row": index, "native_ref": native_ref, "reason": "返事の期限を読めません"}
    else:
        reply_by = None
    try:
        mute_state = map_mute_state(mapped.get("mute_state"))
        tentative = map_tentative(mapped.get("tentative"))
        inferred_fields = normalize_inferred_fields(mapped.get("inferred_fields"))
        is_direct = _choice(mapped.get("is_direct"), {"はい", "いいえ", "不明"}, "1対1か")
        named = _choice(mapped.get("named"), {"はい", "いいえ"}, "名前入りか")
    except ValueError as exc:
        return None, {"type": "notion", "row": index, "native_ref": native_ref, "reason": str(exc)}
    return {
        "input_only": input_only,
        "status": None if input_only else status,
        "title": title if isinstance(title, str) else "",
        "received_at": received_at,
        "certainty": certainty,
        "duration_min": duration_min,
        "reply_by": reply_by,
        "mute_state": mute_state,
        "tentative": tentative,
        "inferred_fields": inferred_fields,
        "is_direct": is_direct,
        "named": named,
        "schedule_related": schedule_related,
        "location": mapped.get("location") if mapped.get("location") not in (None, "") else None,
        "counterpart": mapped.get("counterpart") if mapped.get("counterpart") not in (None, "") else None,
        "addressed_to": mapped.get("addressed_to") if mapped.get("addressed_to") not in (None, "") else None,
        "kind": str(mapped.get("kind") or "Notion"),
        "summary": str(mapped.get("summary") or ""),
        "urgency": str(mapped.get("urgency") or "朝でよい"),
        "due_at": mapped.get("due_at"),
        "event_start": mapped.get("event_start"),
        "event_end": mapped.get("event_end"),
        "next_review_at": mapped.get("next_review_at"),
        "owner_role": mapped.get("owner_role"),
    }, None


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
        fields, error = _map_row_fields(mapped, native_ref, index)
        if error:
            unmapped.append(error); continue
        if existing_input or (not fields["input_only"] and ledger.get_case(case_id)):
            skipped += 1; continue
        received_at = fields["received_at"] or at
        ingest_raw = {
            "source": "notion", "native_ref": native_ref, "received_at": received_at,
            "body_text": "", "body_masked": "", "summary_masked": "", "sender_role": "本人",
        }
        for key in ("addressed_to", "is_direct", "named", "schedule_related", "mute_state"):
            if fields.get(key) is not None:
                ingest_raw[key] = fields[key]
        seen_refs.add(native_ref)
        if dry_run:
            imported += 1
            continue
        with ledger.tx():
            iid, created = ledger.ingest(ingest_raw, now=at)
            if not created:
                skipped += 1; continue
            if not fields["input_only"]:
                patch = {
                    "case_id": case_id, "kind": fields["kind"], "title": fields["title"],
                    "summary": fields["summary"], "status": fields["status"],
                    "certainty": fields["certainty"], "urgency": fields["urgency"],
                    "due_at": fields["due_at"], "event_start": fields["event_start"],
                    "event_end": fields["event_end"], "next_review_at": fields["next_review_at"],
                    "owner_role": fields["owner_role"], "location": fields["location"],
                    "duration_min": fields["duration_min"], "counterpart": fields["counterpart"],
                    "inferred_fields": fields["inferred_fields"], "tentative": fields["tentative"],
                    "reply_by": fields["reply_by"], "cause_input_id": iid,
                    "created_at": at, "updated_at": at,
                }
                ledger.upsert_case(patch, actor="移行", reason="Notion書き出しから移行", basis=native_ref, now=at)
            imported += 1
    return _result(imported, skipped, unmapped)
