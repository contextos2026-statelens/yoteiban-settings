"""旧casebookとNotion書き出しからの重複しない移し替え。"""
from __future__ import annotations

from .refs import normalize_native_ref
import hashlib
import json
import re
import secrets
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

# Notion候補箱の区分。予定なしは原則として案件を作らず入力だけ残す。
# 予定関連が「はい」「迷い」のときは案件（未確定）にする。
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
_DURATION_HALF = re.compile(r"^(\d+)時間半")
_DURATION_HOUR_MIN = re.compile(r"^(\d+(?:\.\d+)?)時間(\d+(?:\.\d+)?)分")
_DURATION_HOUR = re.compile(r"^(\d+(?:\.\d+)?)時間")
_DURATION_MIN = re.compile(r"^(\d+(?:\.\d+)?)分")
_DURATION_NUM = re.compile(r"^(\d+)$")
_YES = {1, True, "1", "__YES__", "はい", "true", "True"}
_NO = {0, False, "0", "__NO__", "いいえ", "false", "False"}

URGENT_NOW = ("至急", "緊急", "事故", "病院", "入院", "救急", "すぐ")
URGENT_TODAY = ("今日中",)
DATE_LIKE = re.compile(
    r"("
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"|\d{1,2}月\d{1,2}日"
    r"|\d{1,2}時(?:\d{1,2}分)?"
    r"|\d{1,2}:\d{2}"
    r")"
)
ISO_IN_TEXT = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)

# 振り分け先が決まっている列。残りは extra へ。
ROUTED_COLUMNS = {
    "確認する項目": "questions",
    "カレンダー登録": "operations",
    "14日後の消去対象か": "retention",
    "登録先のイベント参照": "operations_target",
}
SYSTEM_COLUMNS = {
    "url", "createdTime", "created_time", "created_time_source", "作成日",
}
_DATE_AUX = re.compile(r"^date:.+:(end|is_datetime)$")
UNLINKED_HISTORY = "history:unlinked:"


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
    ref = normalize_native_ref(source, native_ref) or native_ref
    return hashlib.sha256(f"{source}|{ref}".encode("utf-8")).hexdigest()[:24]


def _result(imported, skipped, unmapped, **extra):
    out = {"imported": imported, "skipped": skipped, "unmapped": unmapped}
    out.update(extra)
    return out


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
    """所要を分の整数へ。空は None。読めなければ ValueError。

    「数字＋分／時間」のあとに言葉が付いていても、先頭の所要を読む。
    """
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
    both = _DURATION_HOUR_MIN.match(text)
    if both:
        total = int(float(both.group(1)) * 60 + float(both.group(2)))
        if total < 0:
            raise ValueError("所要を読めません")
        return total
    hour = _DURATION_HOUR.match(text)
    if hour:
        return int(float(hour.group(1)) * 60)
    minute = _DURATION_MIN.match(text)
    if minute:
        return int(float(minute.group(1)))
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


def _stringify(value):
    if value in (None, ""):
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def infer_urgency(row: dict) -> str:
    """行の全体（振り分け前）から急ぎを決める。extra には書かない。"""
    if not isinstance(row, dict):
        return "朝でよい"
    text = "\n".join(_stringify(v) for v in row.values())
    if any(word in text for word in URGENT_NOW):
        return "すぐ"
    if any(word in text for word in URGENT_TODAY):
        return "今日中"
    return "朝でよい"


def extra_needs_review(extra: dict) -> list[str]:
    """その他の箱の値に日時や緊急の語があれば、確かめる列名を返す。"""
    if not isinstance(extra, dict):
        return []
    names = []
    for key, value in extra.items():
        text = _stringify(value)
        if not text.strip():
            continue
        urgent = any(word in text for word in (*URGENT_NOW, *URGENT_TODAY))
        dated = bool(DATE_LIKE.search(text))
        if urgent or dated:
            names.append(key)
    return names


def count_extra(ledger) -> dict:
    """その他の箱の件数（週の見直し用）。個人の値は返さない。"""
    def load(blob):
        if not blob or blob in ("{}", "null"):
            return {}
        try:
            data = json.loads(blob) if isinstance(blob, str) else blob
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) and data else {}

    cases = fields = 0
    for (blob,) in ledger.conn.execute("SELECT extra FROM cases"):
        data = load(blob)
        if data:
            cases += 1
            fields += len(data)
    inputs = 0
    for (blob,) in ledger.conn.execute("SELECT extra FROM raw_inputs"):
        data = load(blob)
        if data:
            inputs += 1
            fields += len(data)
    review_q = ledger.conn.execute(
        "SELECT COUNT(*) FROM questions WHERE text LIKE '%確かめる%'"
    ).fetchone()[0]
    review_findings = ledger.conn.execute(
        "SELECT COUNT(*) FROM outlook_findings WHERE type='extra_review'"
    ).fetchone()[0]
    return {
        "cases": cases,
        "inputs": inputs,
        "fields": fields,
        "review": review_q + review_findings,
    }


def _is_checked(value):
    return value in _YES


def _plus_days(iso_value, days, fallback):
    base = iso_value or fallback
    dt = datetime.fromisoformat(str(base).replace("Z", "+00:00"))
    return (dt + timedelta(days=days)).isoformat()


def parse_last_seen(value):
    """カーソル文から先頭の日時だけ読む。読めなければ None（行は落とさない）。"""
    if value in (None, ""):
        return None
    text = str(value).strip()
    found = ISO_IN_TEXT.search(text)
    if found:
        try:
            return _iso(found.group(0))
        except (TypeError, ValueError):
            return None
    try:
        return _iso(text)
    except (TypeError, ValueError):
        return None


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
    # 予定なしでも、予定関連が「はい」「迷い」なら案件（未確定）にする。
    if status is INPUT_ONLY and schedule_related in {"はい", "迷い"}:
        status = "未確定"
        input_only = False
    else:
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
        "due_at": mapped.get("due_at"),
        "event_start": mapped.get("event_start"),
        "event_end": mapped.get("event_end"),
        "next_review_at": mapped.get("next_review_at"),
        "owner_role": mapped.get("owner_role"),
    }, None


def _collect_row_extras(row, mapping):
    """使い道の決まった列を振り分け、残りは extra へ。知らない列では止まらない。"""
    mapped_cols = set(mapping) if isinstance(mapping, dict) else set()
    questions_text = None
    cal_state = None
    event_ref = None
    retain = None
    extra = {}
    if not isinstance(row, dict):
        return extra, questions_text, cal_state, event_ref, retain
    for column, value in row.items():
        if column in mapped_cols or column in SYSTEM_COLUMNS or _DATE_AUX.match(str(column)):
            continue
        route = ROUTED_COLUMNS.get(column)
        if route == "questions":
            questions_text = value
        elif route == "operations":
            cal_state = value
        elif route == "operations_target":
            event_ref = value
        elif route == "retention":
            retain = value
        elif value not in (None, ""):
            extra[column] = value
    return extra, questions_text, cal_state, event_ref, retain


def _split_questions(text):
    if text in (None, ""):
        return []
    if isinstance(text, (list, tuple)):
        return [str(item).strip() for item in text if str(item).strip()]
    parts = [part.strip() for part in re.split(r"[\n、]+", str(text)) if part.strip()]
    return parts


def _write_review(ledger, case_id, input_id, fields, urgency, now, dry_run):
    """その他の箱の緊急・日時を、確かめるへ回す。案件が無ければ見回り用の印。"""
    if not fields:
        return 0
    if dry_run:
        return 1
    if case_id:
        text = "その他の箱の「" + "、".join(fields) + "」を確かめる"
        exists = ledger.conn.execute(
            "SELECT 1 FROM questions WHERE case_id=? AND text=?", (case_id, text)
        ).fetchone()
        if not exists:
            ledger.add_question(case_id, text, "本人に聞く", now=now)
        return 1
    key = "extra-review:" + (input_id or secrets.token_hex(8))
    if ledger.conn.execute("SELECT 1 FROM outlook_findings WHERE key=?", (key,)).fetchone():
        return 1
    refs = json.dumps([input_id] if input_id else [], ensure_ascii=False)
    ledger.conn.execute(
        "INSERT INTO outlook_findings(key,type,date,refs,urgency,text,status,first_seen,last_seen) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (key, "extra_review", None, refs, urgency or "今日中", "その他の箱を確かめる", "未確定", now, now),
    )
    return 1


def _write_cal_done(ledger, case_id, target_id, now, dry_run):
    if not case_id:
        return 0
    if dry_run:
        return 1
    target = str(target_id).strip() if target_id not in (None, "") else ("notion-cal:" + case_id)
    payload_hash = hashlib.sha256(target.encode("utf-8")).hexdigest()
    op_id = hashlib.sha256(f"cal_personal|{target}|{case_id}|{payload_hash}".encode("utf-8")).hexdigest()[:24]
    if ledger.conn.execute("SELECT 1 FROM operations WHERE op_id=?", (op_id,)).fetchone():
        return 1
    ledger.conn.execute(
        "INSERT INTO operations(op_id,kind,target_id,case_id,payload_hash,state,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (op_id, "cal_personal", target, case_id, payload_hash, "成功（読み戻し済み）", now, now),
    )
    return 1


def from_notion_export(ledger, rows: list[dict], mapping: dict, now, dry_run=False):
    if not isinstance(mapping, dict) or not isinstance(rows, list):
        raise ValueError("rows と mapping の形が不正です")
    at = _iso(now)
    imported = skipped = 0
    unmapped = []
    seen_refs = set()
    stats = {
        "input_only": 0, "cases": 0, "questions": 0, "operations": 0,
        "extra_fields": 0, "review": 0,
    }
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            unmapped.append({"type": "notion", "row": index, "reason": "行が辞書ではありません"}); continue
        mapped = {target: row.get(column) for column, target in mapping.items()}
        native_ref = mapped.get("native_ref")
        if not isinstance(native_ref, str) or not native_ref:
            unmapped.append({"type": "notion", "row": index, "reason": "native_ref の列がありません（移さない）"}); continue
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
        # 急ぎは extra に分ける前に、行の全体から決める。
        urgency = infer_urgency(row)
        extra, questions_text, cal_state, event_ref, retain = _collect_row_extras(row, mapping)
        review_fields = extra_needs_review(extra)
        retention_until = None
        if _is_checked(retain):
            try:
                retention_until = _plus_days(received_at, 14, at)
            except (TypeError, ValueError):
                retention_until = None
        ingest_raw = {
            "source": "notion", "native_ref": native_ref, "received_at": received_at,
            "body_text": "", "body_masked": "", "summary_masked": "", "sender_role": "本人",
        }
        for key in ("addressed_to", "is_direct", "named", "schedule_related", "mute_state"):
            if fields.get(key) is not None:
                ingest_raw[key] = fields[key]
        if retention_until:
            ingest_raw["retention_until"] = retention_until
        if fields["input_only"] and extra:
            ingest_raw["extra"] = json.dumps(extra, ensure_ascii=False)
        seen_refs.add(native_ref)
        question_items = _split_questions(questions_text)
        cal_done = (str(cal_state).strip() == "済")
        if dry_run:
            imported += 1
            if fields["input_only"]:
                stats["input_only"] += 1
            else:
                stats["cases"] += 1
            stats["questions"] += len(question_items) if not fields["input_only"] else 0
            if not fields["input_only"] and extra and question_items is not None:
                pass
            if fields["input_only"] and question_items:
                # 案件が無いときは確認項目も extra へ残す
                extra = dict(extra)
                extra.setdefault("確認する項目", questions_text)
                review_fields = extra_needs_review(extra)
            if cal_done and not fields["input_only"]:
                stats["operations"] += 1
            elif cal_done and fields["input_only"]:
                extra = dict(extra)
                extra.setdefault("カレンダー登録", cal_state)
                if event_ref not in (None, ""):
                    extra.setdefault("登録先のイベント参照", event_ref)
            stats["extra_fields"] += len(extra)
            if review_fields:
                stats["review"] += 1
            continue
        with ledger.tx():
            iid, created = ledger.ingest(ingest_raw, now=at)
            if not created:
                skipped += 1; continue
            written_case = None
            if not fields["input_only"]:
                patch = {
                    "case_id": case_id, "kind": fields["kind"], "title": fields["title"],
                    "summary": fields["summary"], "status": fields["status"],
                    "certainty": fields["certainty"], "urgency": urgency,
                    "due_at": fields["due_at"], "event_start": fields["event_start"],
                    "event_end": fields["event_end"], "next_review_at": fields["next_review_at"],
                    "owner_role": fields["owner_role"], "location": fields["location"],
                    "duration_min": fields["duration_min"], "counterpart": fields["counterpart"],
                    "inferred_fields": fields["inferred_fields"], "tentative": fields["tentative"],
                    "reply_by": fields["reply_by"], "cause_input_id": iid,
                    "created_at": at, "updated_at": at,
                }
                if extra:
                    patch["extra"] = json.dumps(extra, ensure_ascii=False)
                ledger.upsert_case(patch, actor="移行", reason="Notion書き出しから移行", basis=native_ref, now=at)
                written_case = case_id
                stats["cases"] += 1
                for text in question_items:
                    ledger.add_question(case_id, text, "本人に聞く", now=at)
                    stats["questions"] += 1
                if cal_done:
                    stats["operations"] += _write_cal_done(ledger, case_id, event_ref, at, dry_run=False)
            else:
                stats["input_only"] += 1
                leftover = dict(extra)
                if question_items:
                    leftover.setdefault("確認する項目", questions_text)
                if cal_done:
                    leftover.setdefault("カレンダー登録", cal_state)
                    if event_ref not in (None, ""):
                        leftover.setdefault("登録先のイベント参照", event_ref)
                if leftover:
                    extra = leftover
                    review_fields = extra_needs_review(extra)
                    ledger.conn.execute(
                        "UPDATE raw_inputs SET extra=? WHERE input_id=?",
                        (json.dumps(extra, ensure_ascii=False), iid),
                    )
            stats["extra_fields"] += len(extra)
            stats["review"] += _write_review(
                ledger, written_case, iid, review_fields, urgency, at, dry_run=False
            )
            imported += 1
    return _result(imported, skipped, unmapped, **stats)


def from_notion_history(ledger, rows: list[dict], now, dry_run=False, mapping: dict | None = None):
    """記録表を history へ。結べなくても捨てない。種類が空でも止まらない。"""
    if not isinstance(rows, list):
        raise ValueError("rows の形が不正です")
    mapping = mapping or {
        "内容": "title", "種類": "kind", "結果": "result",
        "url": "native_ref", "date:時間:start": "at", "時間": "at",
    }
    at = _iso(now)
    imported = skipped = 0
    unmapped = []
    empty_kind = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            unmapped.append({"type": "history", "row": index, "reason": "行が辞書ではありません"}); continue
        mapped = {target: row.get(column) for column, target in mapping.items()}
        title = mapped.get("title") or ""
        kind = mapped.get("kind") or ""
        result_text = mapped.get("result") or ""
        native_ref = mapped.get("native_ref") or ""
        when = mapped.get("at") or at
        try:
            when = _iso(when)
        except (TypeError, ValueError):
            when = at
        if not str(title).strip() and not str(result_text).strip() and not str(native_ref).strip():
            unmapped.append({"type": "history", "row": index, "reason": "内容がありません"}); continue
        if not str(kind).strip():
            empty_kind += 1
            kind = "その他"
        basis = str(native_ref or ("row-" + str(index)))
        case_id = UNLINKED_HISTORY + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]
        exists = ledger.conn.execute(
            "SELECT 1 FROM history WHERE actor=? AND basis=? AND reason=?",
            ("移行", basis, "Notion記録表から移行"),
        ).fetchone()
        if exists:
            skipped += 1
            continue
        change = {"kind": kind, "title": str(title), "result": str(result_text)}
        if dry_run:
            imported += 1
            continue
        ledger._record_history(case_id, change, "Notion記録表から移行", when, actor="移行", basis=basis)
        imported += 1
    return _result(imported, skipped, unmapped, empty_kind=empty_kind)


def from_notion_exceptions(ledger, rows: list[dict], now, dry_run=False, mapping: dict | None = None):
    """例外表を twin_prefs へ。特徴も扱いも残す。"""
    if not isinstance(rows, list):
        raise ValueError("rows の形が不正です")
    mapping = mapping or {"入力の特徴": "feature", "正しい扱い": "action"}
    at = _iso(now)
    imported = skipped = 0
    unmapped = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            unmapped.append({"type": "exception", "row": index, "reason": "行が辞書ではありません"}); continue
        mapped = {target: row.get(column) for column, target in mapping.items()}
        feature = mapped.get("feature") or ""
        action = mapped.get("action") or ""
        if not str(feature).strip() and not str(action).strip():
            unmapped.append({"type": "exception", "row": index, "reason": "内容がありません"}); continue
        digest = hashlib.sha256(f"{feature}|{action}".encode("utf-8")).hexdigest()[:16]
        topic = "exception:" + digest
        existing = ledger.conn.execute(
            "SELECT 1 FROM twin_prefs WHERE topic=?", (topic,)
        ).fetchone()
        if existing:
            skipped += 1
            continue
        value = json.dumps({"feature": str(feature), "action": str(action)}, ensure_ascii=False)
        if dry_run:
            imported += 1
            continue
        ledger.set_pref(topic, value, "確定", now=at)
        imported += 1
    return _result(imported, skipped, unmapped)


def from_notion_sources(ledger, rows: list[dict], source_map: dict, now, dry_run=False, mapping: dict | None = None):
    """出どころ設定表を sources へ。対応（ID・読む道具・読むだけ）は引数の source_map で渡す。"""
    if not isinstance(rows, list) or not isinstance(source_map, dict):
        raise ValueError("rows と source_map の形が不正です")
    mapping = mapping or {
        "出どころ名": "name", "種類": "kind", "状態": "status",
        "カーソル": "cursor", "信頼度": "trust",
    }
    at = _iso(now)
    imported = skipped = 0
    unmapped = []
    from yoteiban_core.sources import register as register_source, seen as mark_seen

    status_map = {"有効": "動いている", "停止": "止まっている"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            unmapped.append({"type": "source", "row": index, "reason": "行が辞書ではありません"}); continue
        mapped = {target: row.get(column) for column, target in mapping.items()}
        name = mapped.get("name")
        if not isinstance(name, str) or not name.strip():
            unmapped.append({"type": "source", "row": index, "reason": "出どころ名がありません"}); continue
        spec = source_map.get(name)
        if not isinstance(spec, dict):
            unmapped.append({"type": "source", "row": index, "reason": "対応表にない出どころ"}); continue
        source_id = spec.get("source_id")
        kind = spec.get("kind")
        reader = spec.get("reader")
        read_only = spec.get("read_only")
        if not source_id or not kind or not reader or read_only not in (0, 1, True, False):
            unmapped.append({"type": "source", "row": index, "reason": "対応表の項目が足りません"}); continue
        exists = ledger.conn.execute("SELECT 1 FROM sources WHERE source_id=?", (source_id,)).fetchone()
        if exists:
            skipped += 1
            continue
        last_seen = parse_last_seen(mapped.get("cursor"))
        status = status_map.get(str(mapped.get("status") or "").strip(), "未接続")
        trust = mapped.get("trust") or ""
        note = json.dumps({"trust": trust}, ensure_ascii=False) if trust else ""
        if dry_run:
            imported += 1
            continue
        register_source(ledger, source_id, kind, reader, bool(read_only))
        if last_seen:
            mark_seen(ledger, source_id, True, last_seen)
        ledger.conn.execute(
            "UPDATE sources SET status=?, note=? WHERE source_id=?",
            (status, note, source_id),
        )
        imported += 1
    return _result(imported, skipped, unmapped)
