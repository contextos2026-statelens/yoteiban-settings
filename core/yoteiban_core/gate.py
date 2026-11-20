"""外部操作を実行する前に、設定・権限・重複を確認して台帳へ記録する。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import load as load_config
from .deleg import in_scope
from .ledger import InvalidTransition, Ledger, SchemaError


KINDS = {"cal_personal", "reminder", "notion", "send"}
# 下書きカレンダーは作らない（2026-11-19 本人が決定）。台帳の過去の行のため種類名だけ残し、関所では拒否する。
RETIRED_KINDS = {"cal_draft"}
PENDING = "実行結果不明（要照合）"
SUCCESS = "成功（読み戻し済み）"
RUNNING = "実行中"
# 承認なしで個人用カレンダーへ「追加」だけを通す、2つの明示的な例外。
FORCED_ADD = "forced_add"
TENTATIVE = "tentative"
DEPARTURE_NOTICE = "departure_notice"
MOVE_WATCH = "move_watch"
DONE_CHECK = "done_check"
GROUNDS = {FORCED_ADD, TENTATIVE}
# 予定ばんが自分で置いた［仮］だけを、聞かずに直す例外（2026-11-19 本人が決定）。
SELF_RESOLVE = "self_resolve"
TENTATIVE_MOVE = "tentative_move"
DUPLICATE_DELETE = "duplicate_delete"
SELF_RESOLVE_KINDS = {TENTATIVE_MOVE, DUPLICATE_DELETE}
FORCED_PREFIX = "［自動］"
TENTATIVE_PREFIX = "［仮］"
STYLE_ALL_CONFIRM = "全部確認"
STYLE_QUIET = "静かに"
TENTATIVE_OFF = "使わない"
TENTATIVE_MAX_DEFAULT = 5
ACTIVE_STATES = {"予約", RUNNING, SUCCESS, PENDING}
# 招待や送信を伴う内容は例外で通さない（何も送らない）。
SENDING_KEYS = ("attendees", "invitees", "recipient", "recipient_id", "send_updates", "notify_guests")


def payload_hash(payload: Any) -> str:
    """共通仕様のJSON正規化による内容指紋。"""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def marker(op_id: str) -> str:
    return f"yoteiban-op:{op_id}"


def _now(value: str | datetime | None) -> datetime | None:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Tokyo"))
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt


def _now_text(value: str | datetime | None) -> str:
    dt = _now(value)
    if dt is None:
        raise ValueError("now はタイムゾーン付きISO 8601日時で指定してください")
    return dt.isoformat()


def _deny(reason: str, op_id: str | None = None, existing_state: str | None = None) -> dict[str, Any]:
    return {"allow": False, "op_id": op_id, "reason": reason, "existing_state": existing_state}


def _delegation_for(ledger: Ledger, value: Any) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(value, dict):
        provided = value.get("delegation") if isinstance(value.get("delegation"), dict) else value
        del_id = provided.get("del_id") or provided.get("delegation_id")
    elif isinstance(value, str):
        del_id = value
    else:
        del_id = None
    # 権限判断は呼び出し元の辞書ではなく、台帳に保存された委任を使う。
    if not isinstance(del_id, str) or not del_id:
        return None, None
    try:
        row = ledger.conn.execute("SELECT * FROM delegations WHERE del_id=?", (del_id,)).fetchone()
    except Exception:
        return None, None
    if row is None:
        return None, None
    try:
        scope = json.loads(row["scope"])
    except (TypeError, json.JSONDecodeError):
        return None, None
    return {"del_id": row["del_id"], "scope": scope, "state": row["state"],
            "approval_id": row["approved_by_approval_id"]}, row["del_id"]


def _delegation_usage_count(ledger: Ledger, del_id: str) -> int:
    return sum(
        1 for old in ledger.list_ops()
        if old.get("result") and old["result"].get("delegation_id") == del_id
        and old.get("state") != "取消"
    )


def _delegated_check_op(ledger: Ledger, op: dict[str, Any], delegation: dict[str, Any]) -> dict[str, Any]:
    check_op = dict(op)
    payload = op.get("payload")
    if isinstance(payload, dict):
        for key in ("body", "text", "approved_body", "template_body", "template", "recipient", "recipient_id"):
            if key in payload and key not in check_op:
                check_op[key] = payload[key]
    if not any(key in check_op for key in ("approved_body", "template_body", "template")):
        template_id = (delegation.get("scope") or {}).get("template_id")
        if template_id:
            try:
                row = ledger.conn.execute("SELECT body,state FROM templates WHERE template_id=?", (template_id,)).fetchone()
            except Exception:
                row = None
            if row is not None and row["state"] == "有効":
                check_op["approved_body"] = row["body"]
    return check_op


def _delegate_authorized(ledger: Ledger, cfg: dict[str, Any], op: dict[str, Any], now: str) -> tuple[bool, str | None, dict[str, Any] | None]:
    delegation, del_id = _delegation_for(ledger, op.get("delegation"))
    if delegation is None:
        return False, None, None
    # DB上の委任を参照する場合は、承認の取消・期限切れも反映する。
    approval_id = delegation.get("approval_id", delegation.get("approved_by_approval_id"))
    if approval_id:
        approval = ledger.get_approval(approval_id)
        if approval is None or approval.get("state") not in {"有効", "使用済み"}:
            return False, del_id, delegation
        # 承認が使用済みなら委任の有効期間に従う。未使用の承認は有効期限内であること。
        if approval.get("state") == "有効" and (
            _now(approval.get("expires_at")) is None or _now(approval.get("expires_at")) <= _now(now)
        ):
            return False, del_id, delegation
    used_count = _delegation_usage_count(ledger, del_id)
    check_op = _delegated_check_op(ledger, op, delegation)
    ok, _reason = in_scope(delegation, check_op, now, used_count)
    if not ok:
        return False, del_id, delegation
    return True, del_id, delegation


def _approval_authorized(ledger: Ledger, op: dict[str, Any], digest: str, now: str) -> bool:
    approval_id = op.get("approval_id")
    if not isinstance(approval_id, str) or not approval_id:
        return False
    approval = ledger.get_approval(approval_id)
    if approval is None or approval.get("state") != "有効":
        return False
    if approval.get("op_kind") != op.get("kind") or approval.get("payload_hash") != digest:
        return False
    expiry, at = _now(approval.get("expires_at")), _now(now)
    return expiry is not None and at is not None and at < expiry


def _target_allowed(cfg: dict[str, Any], op: dict[str, Any]) -> tuple[bool, str]:
    kind, target = op.get("kind"), op.get("target_id")
    if not isinstance(target, str) or not target:
        return False, "target_id がありません"
    cal_readonly = cfg.get("cal_readonly_ids", [])
    reminder_readonly = cfg.get("reminder_readonly_list_ids", [])
    if (not isinstance(cal_readonly, list) or not isinstance(reminder_readonly, list)
            or any(not isinstance(x, str) for x in cal_readonly + reminder_readonly)):
        return False, "読み取り専用IDの設定が正しくありません"
    read_only = set(cal_readonly) | set(reminder_readonly)
    if target in read_only:
        return False, "読み取り専用または共有先への書き込みは禁止です"
    if kind == "cal_personal":
        expected = cfg.get("cal_personal_id")
        return (target == expected and bool(expected), "本人カレンダーのIDが設定と一致しません")
    if kind == "reminder":
        if op.get("grounds") in {DEPARTURE_NOTICE, MOVE_WATCH, DONE_CHECK}:
            expected = cfg.get("move_reminder_list")
            if not expected:
                expected = cfg.get("reminder_list_id")
        else:
            expected = cfg.get("reminder_list_id")
        return (target == expected and bool(expected), "専用リマインダーリストのIDが設定と一致しません")
    if kind == "notion":
        allowed = cfg.get("notion_db_ids", [])
        return (isinstance(allowed, list) and all(isinstance(x, str) for x in allowed) and target in allowed,
                "NotionデータベースのIDが許可一覧にありません")
    if kind == "send":
        allowed = cfg.get("send_recipient_ids", cfg.get("send_target_ids"))
        if allowed is not None and (not isinstance(allowed, list) or any(not isinstance(x, str) for x in allowed) or target not in allowed):
            return False, "送信先が設定の許可一覧にありません"
        payload = op.get("payload")
        if isinstance(payload, dict):
            bound = next((payload[k] for k in ("recipient_id", "recipient", "target_id") if k in payload), None)
            if bound is not None and bound != target:
                return False, "送信先と承認内容の送信先が一致しません"
            pinned = isinstance(allowed, list) and allowed == [target]
            if bound is None and not pinned:
                return False, "送信先が内容指紋に含まれていません"
        elif not (isinstance(allowed, list) and allowed == [target]):
            return False, "送信先が内容指紋に含まれていません"
        return True, ""
    return False, "未知の操作種類です"


def _departure_notice_check(ledger: Ledger, cfg: dict[str, Any], op: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """承認なしで置ける出発リマインダーだけを厳密に確かめる。"""
    if op.get("kind") != "reminder" or op.get("grounds") != DEPARTURE_NOTICE:
        return False, "出発の知らせの関所ではありません", {}
    if cfg.get("departure_notice", "reminder") != "reminder":
        return False, "出発の知らせがリマインダー設定ではありません", {}
    if op.get("approval_id") or op.get("delegation"):
        return False, "出発の知らせの例外に承認・委任は混ぜられません", {}
    basis, payload = op.get("basis"), op.get("payload")
    if not isinstance(basis, dict) or not isinstance(payload, dict):
        return False, "出発の知らせの根拠または内容がありません", {}
    action = op.get("action", payload.get("operation"))
    if action not in {"add", "update", "delete"} or payload.get("operation") != action:
        return False, "出発の知らせの操作種類が内容と一致しません", {}
    case_id = op.get("case_id")
    case = ledger.get_case(case_id) if isinstance(case_id, str) else None
    if case is None:
        return False, "出発の知らせの案件がありません", {}
    try:
        extra = json.loads(case.get("extra") or "{}") if isinstance(case.get("extra"), str) else case.get("extra", {})
    except (TypeError, ValueError):
        extra = {}
    if not isinstance(extra, dict) or extra.get("movement") is not True:
        return False, "移動の予定として台帳に記録されていません", {}
    short_number = extra.get("short_number")
    if not isinstance(short_number, str) or not re.fullmatch(r"[0-9]{1,6}", short_number):
        return False, "台帳の案件番号がありません", {}
    expected_title = f"出発の時間：#{short_number}"
    linked_id = op.get("linked_op_id")
    linked = ledger.get_op(linked_id) if isinstance(linked_id, str) else None
    if (linked is None or linked.get("kind") != "cal_personal" or linked.get("state") != SUCCESS
            or linked.get("case_id") != case_id):
        return False, "個人用カレンダーへの書き込みが読み戻し済みではありません", {}

    origin_id = basis.get("origin_reminder_op_id")
    origin = ledger.get_op(origin_id) if isinstance(origin_id, str) else None
    active = []
    for old in ledger.list_ops():
        result_data = old.get("result") if isinstance(old.get("result"), dict) else {}
        if (old.get("kind") == "reminder" and old.get("case_id") == case_id
                and result_data.get("grounds") == DEPARTURE_NOTICE
                and old.get("state") in ACTIVE_STATES
                and not result_data.get("deleted_at") and not result_data.get("replaced_by_op_id")):
            active.append(old)
    if action == "add":
        if active:
            return False, "この予定には出発の知らせがすでにあります", {}
    else:
        if (origin is None or origin not in active or origin.get("target_id") != op.get("target_id")
                or origin.get("case_id") != case_id):
            return False, "直す・消す元の出発リマインダーが台帳にありません", {}
        if any(old.get("op_id") != origin_id for old in active):
            return False, "この予定に複数の出発リマインダーがあり照合が必要です", {}
        old_result = origin.get("result") if isinstance(origin.get("result"), dict) else {}
        if not old_result.get("reminder_id"):
            return False, "出発リマインダーの外部IDが未確認です", {}
        if payload.get("reminder_id") != old_result.get("reminder_id"):
            return False, "対象の出発リマインダーIDが一致しません", {}

    if action == "delete":
        if case.get("status") != "取消":
            return False, "出発リマインダーを消すのは予定が取り消された時だけです", {}
        if payload.get("title") != expected_title:
            return False, "出発リマインダーの件名が案件番号と一致しません", {}
        return True, "", {"grounds": DEPARTURE_NOTICE, "action": action,
                            "origin_reminder_op_id": origin_id, "notify_level": "なし"}

    if case.get("status") != "決まった":
        return False, "決まった予定だけに出発の知らせを置けます", {}
    if basis.get("travel_confirmed") is not True:
        return False, "移動の所要時間が未確認です", {}
    travel, prep = basis.get("travel_minutes"), basis.get("prep_minutes")
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (travel, prep)):
        return False, "移動と準備の分数が正しくありません", {}
    event_start = _now(case.get("event_start"))
    due = _now(payload.get("due"))
    if event_start is None or due is None:
        return False, "予定の開始または出発時刻が未確認です", {}
    if due != event_start - timedelta(minutes=travel + prep):
        return False, "出発時刻が開始−移動−準備の計算と一致しません", {}
    if payload.get("title") != expected_title:
        return False, "出発リマインダーの件名に台帳番号以外があるか、番号が一致しません", {}
    notes = payload.get("notes")
    if (not isinstance(notes, str) or not isinstance(case.get("title"), str)
            or case["title"] not in notes or "https://www.google.com/maps/dir/?api=1" not in notes):
        return False, "メモに予定名とGoogle マップの経路リンクがありません", {}
    if any(payload.get(key) for key in SENDING_KEYS):
        return False, "出発の知らせは招待・送信を伴いません", {}
    return True, "", {"grounds": DEPARTURE_NOTICE, "action": action,
                        "origin_reminder_op_id": origin_id, "event_name": case.get("title"),
                        "reminder_title": expected_title, "departure_time": payload.get("due"),
                        "travel_minutes": travel, "prep_minutes": prep, "notify_level": "なし"}


def _move_watch_check(ledger: Ledger, cfg: dict[str, Any], op: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Allow only the two own location reminders and their update/delete/complete actions."""
    if op.get("kind") != "reminder" or op.get("grounds") != MOVE_WATCH:
        return False, "移動の見守りの関所ではありません", {}
    if cfg.get("movement_monitoring") not in (True, "使う"):
        return False, "移動の見守り設定が使うではありません", {}
    if op.get("approval_id") or op.get("delegation"):
        return False, "移動の見守り例外に承認・委任は混ぜられません", {}
    payload, runtime = op.get("payload"), op.get("runtime_input")
    basis = op.get("basis") if isinstance(op.get("basis"), dict) else {}
    if not isinstance(payload, dict) or set(payload) - {"operation", "signal_type", "title", "reminder_id", "point_number"}:
        return False, "保存するpayloadに場所情報など余分な項目があります", {}
    action = op.get("action", payload.get("operation"))
    if action not in {"add", "update", "delete", "complete"} or payload.get("operation") != action:
        return False, "移動リマインダーの操作種類が内容と一致しません", {}
    case_id = op.get("case_id")
    case = ledger.get_case(case_id) if isinstance(case_id, str) else None
    if case is None:
        return False, "移動予定がありません", {}
    try:
        extra = json.loads(case.get("extra") or "{}") if isinstance(case.get("extra"), str) else case.get("extra", {})
    except (TypeError, ValueError):
        extra = {}
    if not isinstance(extra, dict) or extra.get("movement") is not True:
        return False, "台帳で移動の予定と確認できません", {}
    number = extra.get("short_number")
    if not isinstance(number, str) or not re.fullmatch(r"[0-9]{1,6}", number):
        return False, "台帳の案件番号がありません", {}
    signal_type = payload.get("signal_type")
    if signal_type not in {"出発", "到着"}:
        return False, "合図の種類が正しくありません", {}
    expected_title = ("出発：#" if signal_type == "出発" else "到着：#") + number
    point_number = payload.get("point_number")
    if point_number is not None:
        if isinstance(point_number, bool) or not isinstance(point_number, int) or point_number < 1:
            return False, "地点番号が正しくありません", {}
        expected_title += f"-{point_number}"
    if payload.get("title") != expected_title:
        return False, "件名は案件番号だけの出発／到着形式にしてください", {}
    linked_id = op.get("linked_op_id")
    linked = ledger.get_op(linked_id) if isinstance(linked_id, str) else None
    if (linked is None or linked.get("kind") != "cal_personal" or linked.get("state") != SUCCESS
            or linked.get("case_id") != case_id):
        return False, "個人用カレンダーへの書き込みが読み戻し済みではありません", {}

    origin_id = basis.get("origin_reminder_op_id")
    origin = ledger.get_op(origin_id) if isinstance(origin_id, str) else None
    origin_result = origin.get("result") if origin and isinstance(origin.get("result"), dict) else {}
    entries = [old for old in ledger.list_ops()
               if old.get("kind") == "reminder" and old.get("case_id") == case_id
               and isinstance(old.get("result"), dict)
               and old["result"].get("grounds") == MOVE_WATCH
               and old["result"].get("signal_type") == signal_type
               and old["result"].get("point_number") == point_number]
    if action == "add":
        if case.get("status") != "決まった":
            return False, "決まった予定だけに場所リマインダーを置けます", {}
        if entries:
            return False, "この予定の合図は既に作成済みです。同じものを追加しません", {}
        current_location = isinstance(runtime, dict) and runtime.get("current_location") is True
        if not isinstance(runtime, dict) or (not runtime.get("location") and not current_location):
            return False, "端末に渡す場所がありません", {}
        expected_trigger = "leave" if signal_type == "出発" else "arrive"
        notes = runtime.get("notes")
        if runtime.get("trigger") != expected_trigger:
            return False, "場所の入る／出る指定が合図の種類と一致しません", {}
        if current_location:
            if (signal_type != "出発" or cfg.get("departure_notice", "reminder") != "reminder"
                    or not isinstance(extra.get("departure_notice_received_at"), str)):
                return False, "現在地の出発合図は出発の知らせを受けた後だけ置けます", {}
        elif point_number is not None:
            move = extra.get("move_status") if isinstance(extra.get("move_status"), dict) else {}
            points = move.get("points", []) if isinstance(move.get("points", []), list) else []
            if not any(isinstance(p, dict) and p.get("group_number") == point_number
                       and p.get("kind") == "errand" and p.get("has_signal") is True for p in points):
                return False, "地点番号が台帳の用事地点にありません", {}
        elif signal_type == "到着":
            move = extra.get("move_status") if isinstance(extra.get("move_status"), dict) else {}
            points = move.get("points", []) if isinstance(move.get("points", []), list) else []
            groups = {p.get("group_number") for p in points if isinstance(p, dict)
                      and p.get("kind") == "errand" and isinstance(p.get("group_number"), int)}
            if len(groups) > 1:
                return False, "複数の用事地点は地点番号つきの到着合図が必要です", {}
        if not isinstance(notes, str) or case.get("title") not in notes \
                or "https://www.google.com/maps/dir/?api=1" not in notes:
            return False, "メモに予定名とGoogle マップ経路リンクがありません", {}
    else:
        if (origin is None or origin not in entries or origin.get("target_id") != op.get("target_id")
                or origin.get("state") != SUCCESS or origin_result.get("completed_at")
                or origin_result.get("deleted_at") or origin_result.get("replaced_by_op_id")):
            return False, "予定ばんが作った有効な合図リマインダーがありません", {}
        reminder_id = origin_result.get("reminder_id")
        if not isinstance(reminder_id, str) or not reminder_id or payload.get("reminder_id") != reminder_id:
            return False, "対象リマインダーIDが作成記録と一致しません", {}
        if action == "delete" and case.get("status") != "取消":
            return False, "削除できるのは予定が取り消された時だけです", {}
        if action in {"update", "complete"} and case.get("status") != "決まった":
            return False, "更新・完了は決まった予定だけです", {}
        if action == "complete":
            move = extra.get("move_status") if isinstance(extra.get("move_status"), dict) else {}
            signals = move.get("signals", []) if isinstance(move.get("signals", []), list) else []
            if not any(isinstance(signal, dict) and signal.get("type") == signal_type for signal in signals):
                return False, "対応する出発・到着合図を受け取る前は完了にできません", {}
        if action == "update":
            if not isinstance(runtime, dict) or not runtime.get("location"):
                return False, "更新する場所がありません", {}
            expected_trigger = "leave" if signal_type == "出発" else "arrive"
            notes = runtime.get("notes")
            if runtime.get("trigger") != expected_trigger or not isinstance(notes, str) \
                    or case.get("title") not in notes or "https://www.google.com/maps/dir/?api=1" not in notes:
                return False, "更新内容の場所指定またはメモが正しくありません", {}
        if action == "complete" and (runtime is not None or "location" in payload):
            return False, "完了操作に場所データを含めません", {}
        if action == "delete" and runtime is not None:
            return False, "削除操作に場所データを含めません", {}

    if any(payload.get(k) for k in SENDING_KEYS):
        return False, "移動リマインダーは送信・招待を伴いません", {}
    return True, "", {"grounds": MOVE_WATCH, "action": action,
                       "signal_type": signal_type, "reminder_title": expected_title,
                       "point_number": point_number, "origin_reminder_op_id": origin_id, "notify_level": "なし"}


def _done_check_check(ledger: Ledger, cfg: dict[str, Any], op: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    if op.get("kind") != "reminder" or op.get("grounds") != DONE_CHECK:
        return False, "終了確認の関所ではありません", {}
    if cfg.get("done_check", "all") not in {"all", "important", True}:
        return False, "できたかの確認設定が無効です", {}
    payload = op.get("payload")
    if not isinstance(payload, dict) or set(payload) - {"operation", "title", "due", "reminder_id"}:
        return False, "終了確認の保存内容に余分な情報があります", {}
    action = op.get("action", payload.get("operation"))
    if action not in {"add", "update", "delete", "complete"} or payload.get("operation") != action:
        return False, "終了確認の操作種類が一致しません", {}
    case_id = op.get("case_id")
    case = ledger.get_case(case_id) if isinstance(case_id, str) else None
    if not case:
        return False, "予定がありません", {}
    try:
        extra = json.loads(case.get("extra") or "{}") if isinstance(case.get("extra"), str) else case.get("extra", {})
    except (TypeError, ValueError):
        extra = {}
    title_text = case.get("title") if isinstance(case.get("title"), str) else ""
    if (not isinstance(extra, dict) or extra.get("all_day") is True
            or extra.get("reply_or_check") is True
            or re.search(r"(?:に返信する|を確認する)$", title_text)):
        return False, "この予定は終了確認の対象外です", {}
    if extra.get("owner") not in (None, "self"):
        return False, "本人以外の予定は対象外です", {}
    importance = extra.get("importance", case.get("urgency"))
    if cfg.get("done_check") == "important" and importance not in {"中", "高", "緊急"}:
        return False, "重要度が対象の範囲外です", {}
    if case.get("status") in {"取消", "削除", "削除済み"}:
        return False, "取り消し済みの予定は対象外です", {}
    number = extra.get("short_number")
    if not isinstance(number, str) or not re.fullmatch(r"[0-9]{1,6}", number):
        return False, "案件の短い番号がありません", {}
    if payload.get("title") != f"終わった？：#{number}":
        return False, "件名は終わった？と案件番号だけにしてください", {}
    linked_id = op.get("linked_op_id")
    linked = ledger.get_op(linked_id) if isinstance(linked_id, str) else None
    if action == "add":
        if case.get("status") not in {"決まった", "仮", "［仮］", "自動", "［自動］"}:
            return False, "読み戻し済みの決まった予定だけに確認リマインダーを置けます", {}
        linked_ok = (linked is not None and linked.get("kind") == "cal_personal"
                     and linked.get("state") == SUCCESS and linked.get("case_id") == case_id)
        household_ok = (extra.get("household_read_only") is True and extra.get("household_owner") == "self"
                        and linked_id is None)
        if not linked_ok and not household_ok:
            return False, "個人用カレンダーへの読み戻し、または本人と確認済みの家用予定ではありません", {}
        due = payload.get("due")
        if (not isinstance(case.get("event_end"), str) or "T" not in case["event_end"]
                or not isinstance(due, str) or due != case.get("event_end")):
            return False, "終了時刻と確認リマインダーの時刻が一致しません", {}
        if any(old.get("kind") == "reminder" and old.get("case_id") == case_id
               and isinstance(old.get("result"), dict) and old["result"].get("grounds") == DONE_CHECK
               for old in ledger.list_ops()):
            return False, "この予定の終了確認リマインダーは既にあります", {}
    else:
        basis = op.get("basis") if isinstance(op.get("basis"), dict) else {}
        origin_id = basis.get("origin_reminder_op_id")
        origin = ledger.get_op(origin_id) if isinstance(origin_id, str) else None
        origin_result = origin.get("result") if origin and isinstance(origin.get("result"), dict) else {}
        if (origin is None or origin.get("kind") != "reminder" or origin.get("case_id") != case_id
                or origin.get("state") != SUCCESS or origin_result.get("grounds") != DONE_CHECK
                or origin_result.get("reminder_id") != payload.get("reminder_id")
                or origin_result.get("completed_at") or origin_result.get("deleted_at")):
            return False, "予定ばんが作った有効な終了確認リマインダーではありません", {}
        if action == "complete" and not isinstance(extra.get("done_check"), dict):
            return False, "結果を記録する前は終了確認リマインダーを完了にできません", {}
        if action == "delete" and case.get("status") not in {"取消", "削除", "削除済み"}:
            return False, "削除できるのは取り消された予定だけです", {}
        if action == "update":
            if case.get("status") not in {"決まった", "仮", "［仮］", "自動", "［自動］"}:
                return False, "更新できるのは続いている予定だけです", {}
            if not isinstance(case.get("event_end"), str) or payload.get("due") != case.get("event_end"):
                return False, "更新する確認時刻が予定の終了時刻と一致しません", {}
    return True, "", {"grounds": DONE_CHECK, "action": action, "reminder_title": payload["title"],
                       "origin_reminder_op_id": (op.get("basis") or {}).get("origin_reminder_op_id"),
                       "notify_level": "なし"}


def _setting(cfg: dict[str, Any], basis: dict[str, Any], key: str) -> Any:
    """本人の設定は設定ファイルの値を優先し、無ければ根拠に書かれた値を使う。"""
    value = cfg.get(key)
    return value if value is not None else basis.get(key)


def _is_false(basis: dict[str, Any], key: str) -> bool:
    return basis.get(key) is False


def _active_tentative(ledger: Ledger) -> int:
    count = 0
    for old in ledger.list_ops():
        res = old.get("result") if isinstance(old.get("result"), dict) else {}
        if (old.get("kind") == "cal_personal" and res.get("grounds") == TENTATIVE
                and old.get("state") in ACTIVE_STATES and not res.get("tentative_closed")):
            count += 1
    return count


def _tentative_max(cfg: dict[str, Any]) -> int:
    value = cfg.get("tentative_max", TENTATIVE_MAX_DEFAULT)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else TENTATIVE_MAX_DEFAULT



HOUSEHOLD_SOURCE_DEFAULT = ("calendar-household", "calendar-family")


def _household_source_ids(cfg: dict[str, Any]) -> list[str]:
    value = cfg.get("household_source_ids")
    if isinstance(value, list) and value:
        return [x for x in value if isinstance(x, str) and x]
    return list(HOUSEHOLD_SOURCE_DEFAULT)


def _normalize_email(value: Any) -> str | None:
    """前後空白を除き小文字にしたメールだけを返す。表示名や電話は対象外。"""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if " " in text or "@" not in text:
        return None
    domain = text.rsplit("@", 1)[-1]
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        return None
    return text


def _trusted_emails(cfg: dict[str, Any]) -> set[str]:
    """箱の信頼する送り手ファイルから、メールだけを集める。中身は返さず、照合用の集合だけ持つ。"""
    path = cfg.get("trusted_senders_file")
    if not isinstance(path, str) or not path:
        return set()
    try:
        with Path(path).open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return set()
    senders = data.get("senders") if isinstance(data, dict) else data
    if not isinstance(senders, list):
        return set()
    found: set[str] = set()
    for row in senders:
        if not isinstance(row, dict):
            continue
        ids: list[Any] = []
        for key in ("ids", "emails", "email"):
            value = row.get(key)
            if isinstance(value, list):
                ids.extend(value)
            elif isinstance(value, str):
                ids.append(value)
        for item in ids:
            email = _normalize_email(item)
            if email:
                found.add(email)
    return found


def _source_trusted(cfg: dict[str, Any], basis: dict[str, Any]) -> tuple[bool, str]:
    trusted = cfg.get("trusted_source_ids")
    source = basis.get("source_id")
    if (isinstance(trusted, list) and isinstance(source, str) and source
            and source in [x for x in trusted if isinstance(x, str)]):
        return True, ""
    if _normalize_email(basis.get("sender_email")) in _trusted_emails(cfg):
        # _normalize_email が None のときは集合に入らない
        return True, ""
    return False, "出どころが信頼する出どころに登録されていません"


def _exception_check(ledger: Ledger, cfg: dict[str, Any], op: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """強制追加・［仮］の根拠を確かめる。1つでも欠けたら通さない。"""
    grounds = op.get("grounds")
    basis = op.get("basis")
    if grounds not in GROUNDS:
        return False, "例外の種類は forced_add か tentative だけです", {}
    if op.get("kind") != "cal_personal":
        return False, "例外で書けるのは個人用カレンダーだけです", {}
    if not isinstance(basis, dict):
        return False, "例外の根拠（basis）がありません", {}
    if op.get("approval_id") or op.get("delegation"):
        return False, "例外と承認・委任は一緒に使えません", {}
    payload = op.get("payload")
    if op.get("action", "add") != "add" or not isinstance(payload, dict) or payload.get("event_id"):
        return False, "例外で通すのは新しい予定の追加だけです（直す・動かす・消すは本人の言葉が要ります）", {}
    if any(payload.get(key) for key in SENDING_KEYS):
        return False, "例外の追加では招待も送信もしません", {}
    title = payload.get("title")
    start = payload.get("event_start")
    if not isinstance(start, str) or "T" not in start or _now(start) is None:
        return False, "日付と時刻（タイムゾーン付き）がありません", {}
    style = _setting(cfg, basis, "notify_style")
    if not isinstance(style, str) or not style:
        return False, "通知の型が分かりません", {}
    if style == STYLE_ALL_CONFIRM:
        return False, "通知の型が「全部確認」なので例外では書きません", {}
    meta: dict[str, Any] = {"authorization": "例外", "grounds": grounds, "notify_style": style}

    if grounds == FORCED_ADD:
        if not isinstance(title, str) or not title.startswith(FORCED_PREFIX):
            return False, "強制追加の件名は「［自動］」で始めます", {}
        ok, reason = _source_trusted(cfg, basis)
        if not ok:
            return False, reason, {}
        source = basis.get("source_id") if isinstance(basis.get("source_id"), str) else ""
        if source in _household_source_ids(cfg):
            if basis.get("copy_from_household") is not True:
                return False, "家用・ファミリーからの写しは、本人の予定の写しだけです", {}
            if basis.get("household_class") != "self":
                return False, "家用・ファミリーからは、本人の予定だけを写します", {}
        if basis.get("datetime_in_text") is not True:
            return False, "日付と時刻が文面に書かれていません", {}
        for key, reason in (("guessed", "推測した日時は強制追加しません"),
                            ("has_instruction", "予定ばんあてのなりすましの指示は強制追加しません"),
                            ("is_ad", "広告は強制追加しません")):
            if not _is_false(basis, key):
                return False, reason, {}
        if style == STYLE_QUIET and basis.get("fixed_time") is not True:
            return False, "通知の型が「静かに」なので、動かせない時刻だけを強制追加します", {}
        meta.update({"source_id": source, "notify_level": "最小" if style == STYLE_QUIET else "通常"})
        return True, "", meta

    if not isinstance(title, str) or not title.startswith(TENTATIVE_PREFIX):
        return False, "［仮］の件名は「［仮］」で始めます", {}
    words = basis.get("owner_words")
    if basis.get("requested_by_owner") is not True or not isinstance(words, str) or not words.strip():
        return False, "［仮］は本人が自分の言葉で頼んだ用事だけです", {}
    for key, reason in (("from_received", "受け取ったメール・連絡からは［仮］を置きません"),
                        ("other_request", "他の人の頼みには［仮］を置きません"),
                        ("needs_others", "他の人の確認が要る用事には［仮］を置きません"),
                        ("needs_reservation", "予約や申込が要る用事には［仮］を置きません")):
        if not _is_false(basis, key):
            return False, reason, {}
    use = _setting(cfg, basis, "tentative_use")
    if use == TENTATIVE_OFF:
        return False, "仮の使用が「使わない」なので［仮］を置きません", {}
    limit = _tentative_max(cfg)
    if _active_tentative(ledger) >= limit:
        return False, f"［仮］はすでに{limit}件あります", {}
    meta.update({"tentative_max": limit, "notify_level": "なし",
                 "title": title, "event_start": start, "event_end": payload.get("event_end")})
    input_ref = basis.get("input_ref")
    if isinstance(input_ref, str) and input_ref:
        meta["input_ref"] = input_ref
    return True, "", meta


def _placed_tentative(ledger: Ledger, op_id: Any) -> tuple[dict[str, Any] | None, str]:
    """予定ばんが例外の［仮］として置き、読み戻しまで済んだ、まだ閉じていない操作を返す。"""
    if not isinstance(op_id, str) or not op_id:
        return None, "元の［仮］の操作（origin_op_id）がありません"
    row = ledger.get_op(op_id)
    if row is None:
        return None, "元の［仮］の操作が台帳にありません"
    res = row.get("result") if isinstance(row.get("result"), dict) else {}
    if row.get("kind") != "cal_personal" or res.get("grounds") != TENTATIVE:
        return None, "予定ばんが置いた［仮］ではないので、聞かずには直しません"
    if row.get("approval_id") or res.get("delegation_id"):
        return None, "本人が承認した予定は、聞かずには直しません"
    if row.get("state") != SUCCESS:
        return None, "元の［仮］の書き込みが確かめ済みではありません"
    if res.get("tentative_closed"):
        return None, "元の［仮］はもう閉じています"
    title = res.get("title")
    if not isinstance(title, str) or not title.startswith(TENTATIVE_PREFIX) or title.startswith(FORCED_PREFIX):
        return None, "件名が「［仮］」で始まらないので、聞かずには直しません"
    if not isinstance(res.get("event_id"), str) or not res.get("event_id"):
        return None, "元の［仮］の予定IDが記録されていません"
    return row, ""


def _current(res: dict[str, Any]) -> dict[str, Any]:
    return {"event_id": res.get("event_id"), "title": res.get("title"),
            "event_start": res.get("current_start", res.get("event_start")),
            "event_end": res.get("current_end", res.get("event_end"))}


def _self_resolve_check(ledger: Ledger, cfg: dict[str, Any], op: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """予定ばんが自分で置いた［仮］の重なり・ダブりだけを、承認なしで動かす・消す。1つでも欠けたら通さない。"""
    basis = op.get("basis")
    if op.get("kind") != "cal_personal":
        return False, "聞かずに直せるのは個人用カレンダーだけです", {}
    if not isinstance(basis, dict):
        return False, "根拠（basis）がありません", {}
    if op.get("approval_id") or op.get("delegation"):
        return False, "例外と承認・委任は一緒に使えません", {}
    which = basis.get("kind")
    if which not in SELF_RESOLVE_KINDS:
        return False, "basis.kind は tentative_move か duplicate_delete だけです", {}
    payload = op.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("event_id"), str) or not payload.get("event_id"):
        return False, "直す予定のID（payload.event_id）がありません", {}
    if any(payload.get(key) for key in SENDING_KEYS):
        return False, "聞かずに直すときは招待も送信もしません", {}
    style = _setting(cfg, basis, "notify_style")
    if style == STYLE_ALL_CONFIRM:
        return False, "通知の型が「全部確認」なので聞かずには直しません", {}
    origin, why = _placed_tentative(ledger, basis.get("origin_op_id"))
    if origin is None:
        return False, why, {}
    ores = origin["result"]
    if ores.get("event_id") != payload["event_id"]:
        return False, "直す予定のIDが、予定ばんが置いた［仮］と一致しません", {}
    before = _current(ores)
    action = op.get("action")
    meta: dict[str, Any] = {"authorization": "例外", "grounds": SELF_RESOLVE, "self_resolve_kind": which,
                            "origin_op_id": origin["op_id"], "notify_level": "1行", "before": before}

    if which == TENTATIVE_MOVE:
        if action not in {"move", "update"}:
            return False, "tentative_move の action は move か update だけです", {}
        title = payload.get("title", before["title"])
        if not isinstance(title, str) or not title.startswith(TENTATIVE_PREFIX) or title.startswith(FORCED_PREFIX):
            return False, "動かした後も件名は「［仮］」で始めます", {}
        start, end = payload.get("event_start"), payload.get("event_end")
        if not isinstance(start, str) or "T" not in start or _now(start) is None:
            return False, "移す先の日付と時刻（タイムゾーン付き）がありません", {}
        if end is not None and (_now(end) is None or _now(end) <= _now(start)):
            return False, "移す先の終わりの時刻が正しくありません", {}
        meta["after"] = {"event_id": payload["event_id"], "title": title, "event_start": start, "event_end": end}
        return True, "", meta

    if action != "delete":
        return False, "duplicate_delete の action は delete だけです", {}
    keep, why = _placed_tentative(ledger, basis.get("keep_op_id"))
    if keep is None:
        return False, "残す方: " + why, {}
    kres = keep["result"]
    if keep["op_id"] == origin["op_id"] or kres.get("event_id") == ores.get("event_id"):
        return False, "残す予定と消す予定が同じです", {}
    ref = ores.get("input_ref")
    if not isinstance(ref, str) or not ref or kres.get("input_ref") != ref:
        return False, "同じ入力から予定ばんが置いた2件ではないので、聞かずには消しません", {}
    if _current(kres)["title"] != before["title"] or _current(kres)["event_start"] != before["event_start"] \
            or _current(kres)["event_end"] != before["event_end"]:
        return False, "件名と時刻がまったく同じダブりではないので、聞かずには消しません", {}
    kept_at, del_at = _now(keep.get("created_at")), _now(origin.get("created_at"))
    if kept_at is None or del_at is None or not kept_at < del_at:
        return False, "消してよいのは、後から置いた方だけです", {}
    meta.update({"keep_op_id": keep["op_id"], "input_ref": ref, "after": None})
    return True, "", meta


def _reserve_exception(ledger: Ledger, cfg: dict[str, Any], kind: str, target: str, case_id: str,
                       digest: str, linked_op_id: str | None, now: str, meta: dict[str, Any]) -> tuple[str, str | None]:
    """例外が通った追加を予約する。Ledger.reserve_op の本人承認必須制約に対応する。"""
    op_id = ledger._digest(f"{kind}|{target}|{case_id}|{digest}")[:24]
    with ledger.tx():
        old = ledger.conn.execute("SELECT state FROM operations WHERE op_id=?", (op_id,)).fetchone()
        if old:
            return op_id, old["state"]
        if meta.get("grounds") == TENTATIVE and _active_tentative(ledger) >= _tentative_max(cfg):
            raise InvalidTransition("［仮］の件数の上限を再確認できません")
        ledger.conn.execute(
            "INSERT INTO operations(op_id,kind,target_id,case_id,payload_hash,approval_id,linked_op_id,state,handoff_state,result,created_at,updated_at) "
            "VALUES(?,?,?,?,?,NULL,?,'予約',NULL,?,?,?)",
            (op_id, kind, target, case_id, digest, linked_op_id, json.dumps(meta, ensure_ascii=False), now, now),
        )
        ledger._record_history(case_id, {"operation": {"before": None, "after": {"op_id": op_id, "kind": kind, "state": "予約", "grounds": meta.get("grounds")}}}, "例外で操作を予約", now)
    return op_id, None


def _run_exception(ledger: Ledger, op_id: str, now: str) -> None:
    with ledger.tx():
        row = ledger.conn.execute("SELECT state,case_id FROM operations WHERE op_id=?", (op_id,)).fetchone()
        if row is None or row["state"] != "予約":
            raise InvalidTransition("例外の操作を実行中にできません")
        ledger.conn.execute("UPDATE operations SET state='実行中',updated_at=? WHERE op_id=? AND state='予約'", (now, op_id))
        ledger._record_history(row["case_id"], {"state": {"before": "予約", "after": "実行中"}}, "例外の操作を実行中に変更", now)


def close_tentative(ledger: Ledger, op_id: str, how: str, now: str | datetime | None = None) -> dict[str, Any]:
    """［仮］を本人の一言で確定・移動・削除したあと、件数から外す。カレンダー自体は既存の承認の道で直す。"""
    at = _now_text(now)
    operation = ledger.get_op(op_id)
    if operation is None:
        raise KeyError(op_id)
    saved = dict(operation.get("result") or {}) if isinstance(operation.get("result"), dict) else {}
    if saved.get("grounds") != TENTATIVE:
        return {"ok": False, "op_id": op_id, "reason": "［仮］の操作ではありません"}
    if how not in {"確定", "移動", "削除"}:
        return {"ok": False, "op_id": op_id, "reason": "how は 確定・移動・削除 のどれかです"}
    saved.update({"tentative_closed": True, "closed_how": how, "closed_at": at})
    ledger.set_op_state(op_id, operation["state"], result=saved, now=at)
    return {"ok": True, "op_id": op_id, "reason": f"［仮］を{how}として件数から外しました"}


def _case_status(ledger: Ledger, case_id: str) -> str | None:
    row = ledger.get_case(case_id)
    return row.get("status") if row else None


def _reserve_delegated(ledger: Ledger, kind: str, target: str, case_id: str,
                       digest: str, linked_op_id: str | None, now: str,
                       del_id: str, delegation: dict[str, Any], op: dict[str, Any]) -> tuple[str, str | None]:
    """委任が通った操作を予約する。Ledger.reserve_op の本人承認必須制約に対応する。"""
    op_id = ledger._digest(f"{kind}|{target}|{case_id}|{digest}")[:24]
    with ledger.tx():
        old = ledger.conn.execute("SELECT state FROM operations WHERE op_id=?", (op_id,)).fetchone()
        if old:
            return op_id, old["state"]
        check_op = _delegated_check_op(ledger, op, delegation)
        if not in_scope(delegation, check_op, now, _delegation_usage_count(ledger, del_id))[0]:
            raise InvalidTransition("委任の範囲または回数上限を再確認できません")
        ledger.conn.execute(
            "INSERT INTO operations(op_id,kind,target_id,case_id,payload_hash,approval_id,linked_op_id,state,handoff_state,result,created_at,updated_at) "
            "VALUES(?,?,?,?,?,NULL,?,'予約',?,?,?,?)",
            (op_id, kind, target, case_id, digest, linked_op_id,
             "渡し前（未送信）" if kind == "send" else None,
             json.dumps({"delegation_id": del_id, "authorization": "委任"}, ensure_ascii=False), now, now),
        )
        ledger._record_history(case_id, {"operation": {"before": None, "after": {"op_id": op_id, "kind": kind, "state": "予約", "delegation_id": del_id}}}, "委任で操作を予約", now)
    return op_id, None


def _run_delegated(ledger: Ledger, op_id: str, now: str, kind: str) -> None:
    with ledger.tx():
        row = ledger.conn.execute("SELECT state,case_id,result FROM operations WHERE op_id=?", (op_id,)).fetchone()
        if row is None or row["state"] != "予約":
            raise InvalidTransition("委任操作を実行中にできません")
        ledger.conn.execute("UPDATE operations SET state='実行中',updated_at=? WHERE op_id=? AND state='予約'", (now, op_id))
        ledger._record_history(row["case_id"], {"state": {"before": "予約", "after": "実行中"}}, "委任操作を実行中に変更", now)


def _cancel_reservation(ledger: Ledger, op_id: str, now: str) -> None:
    try:
        ledger.set_op_state(op_id, "取消", result={"reason": "同じ案件・操作種類に要照合の操作があります"}, now=now)
    except (KeyError, InvalidTransition, SchemaError):
        pass


def check(ledger: Ledger, cfg: dict[str, Any], op: dict[str, Any], now: str | datetime | None = None) -> dict[str, Any]:
    """判定し、許可する時だけ操作を予約して実行中にする。"""
    if not isinstance(cfg, dict) or not isinstance(op, dict):
        return _deny("設定または操作の形が正しくありません")
    try:
        at = _now_text(now)
    except ValueError as exc:
        return _deny(str(exc))
    kind = op.get("kind")
    if kind in RETIRED_KINDS:
        return _deny("下書きカレンダーは使いません（作らないと決めました）")
    if not isinstance(kind, str) or kind not in KINDS:
        return _deny("未知の操作種類です")
    target_ok, target_reason = _target_allowed(cfg, op)
    if not target_ok:
        return _deny(target_reason)
    case_id = op.get("case_id")
    if not isinstance(case_id, str) or not case_id or ledger.get_case(case_id) is None:
        return _deny("案件が見つかりません")
    payload = op.get("payload")
    try:
        digest = payload_hash(payload)
    except (TypeError, ValueError, OverflowError):
        return _deny("操作内容を指紋にできません")
    expected_op_id = ledger._digest(f"{kind}|{op['target_id']}|{case_id}|{digest}")[:24]
    prior = ledger.get_op(expected_op_id)
    if prior is not None:
        if prior.get("state") == SUCCESS:
            return _deny("成功済み", expected_op_id, SUCCESS)
        return _deny("同じ操作はすでに予約または処理されています", expected_op_id, prior.get("state"))
    try:
        delegation_ok, del_id, delegation = _delegate_authorized(ledger, cfg, op, at)
    except Exception:
        delegation_ok, del_id, delegation = False, None, None
    approval_ok = _approval_authorized(ledger, op, digest, at)
    exception_ok, exception_meta = False, {}
    if op.get("grounds") == SELF_RESOLVE:
        exception_ok, reason, exception_meta = _self_resolve_check(ledger, cfg, op)
        if not exception_ok:
            return _deny(reason)
    elif op.get("grounds") == DEPARTURE_NOTICE:
        exception_ok, reason, exception_meta = _departure_notice_check(ledger, cfg, op)
        if not exception_ok:
            return _deny(reason)
    elif op.get("grounds") == MOVE_WATCH:
        exception_ok, reason, exception_meta = _move_watch_check(ledger, cfg, op)
        if not exception_ok:
            return _deny(reason)
    elif op.get("grounds") == DONE_CHECK:
        exception_ok, reason, exception_meta = _done_check_check(ledger, cfg, op)
        if not exception_ok:
            return _deny(reason)
    elif op.get("grounds") is not None or op.get("basis") is not None:
        exception_ok, reason, exception_meta = _exception_check(ledger, cfg, op)
        if not exception_ok:
            return _deny(reason)

    if kind == "cal_personal":
        if not approval_ok and not delegation_ok and not exception_ok:
            return _deny("本人カレンダーには有効な承認か範囲内の有効な委任、または条件を満たす強制追加・［仮］・予定ばんの［仮］の直しが必要です")
    elif kind == "reminder":
        if not exception_ok:
            linked_id = op.get("linked_op_id")
            linked = ledger.get_op(linked_id) if isinstance(linked_id, str) else None
            if (linked is None or linked.get("kind") != "cal_personal" or linked.get("state") != SUCCESS
                    or linked.get("case_id") != case_id or _case_status(ledger, case_id) != "決まった"):
                return _deny("確定済みの本人カレンダー操作に結び付いたリマインダーだけ許可します")
    elif kind == "send":
        if not approval_ok and not delegation_ok:
            return _deny("送信には期限内で内容が一致する本人承認か範囲内の委任が必要です")

    if (kind in {"cal_personal", "send"}) and op.get("approval_id") and not approval_ok:
        return _deny("承認の種類・状態・期限・内容が一致しません")
    linked_op_id = op.get("linked_op_id")
    if linked_op_id is not None and not isinstance(linked_op_id, str):
        return _deny("linked_op_id の形式が正しくありません")

    try:
        if exception_ok:
            op_id, existing = _reserve_exception(ledger, cfg, kind, op["target_id"], case_id, digest, linked_op_id, at, exception_meta)
        elif delegation_ok and not approval_ok and kind in {"cal_personal", "send"}:
            op_id, existing = _reserve_delegated(ledger, kind, op["target_id"], case_id, digest, linked_op_id, at, del_id, delegation, op)
        else:
            op_id, existing = ledger.reserve_op(kind, op["target_id"], case_id, digest,
                                                 approval_id=op.get("approval_id"), linked_op_id=linked_op_id, now=at)
    except (InvalidTransition, SchemaError, KeyError, ValueError) as exc:
        return _deny(f"操作を予約できません: {exc}")
    if existing == SUCCESS:
        return _deny("成功済み", op_id, existing)
    if existing is not None:
        return _deny("同じ操作はすでに予約または処理されています", op_id, existing)

    # 同じ案件・種別に結果不明がある時は、新しい予約を取消して二重実行を止める。
    unresolved = next((old for old in ledger.list_ops()
                       if old.get("op_id") != op_id and old.get("case_id") == case_id
                       and old.get("kind") == kind and old.get("state") == PENDING), None)
    if unresolved:
        _cancel_reservation(ledger, op_id, at)
        return _deny("同じ案件・操作種類に実行結果不明の操作があり、照合が必要です", op_id, "取消")

    try:
        if exception_ok:
            _run_exception(ledger, op_id, at)
        elif kind in {"cal_personal", "send"} and approval_ok:
            ledger.use_approval(op.get("approval_id"), now=at)
            ledger.set_op_state(op_id, RUNNING, now=at)
        elif kind in {"cal_personal", "send"} and delegation_ok:
            _run_delegated(ledger, op_id, at, kind)
        else:
            ledger.set_op_state(op_id, RUNNING, now=at)
    except (InvalidTransition, SchemaError, KeyError, ValueError) as exc:
        _cancel_reservation(ledger, op_id, at)
        return _deny(f"操作を実行中にできません: {exc}", op_id, "取消")
    out = {"allow": True, "op_id": op_id, "reason": "操作を実行中にしました", "existing_state": None}
    if exception_ok:
        out.update({"grounds": exception_meta["grounds"], "notify_level": exception_meta["notify_level"]})
        if exception_meta["grounds"] == DEPARTURE_NOTICE:
            out.update({"departure_action": exception_meta["action"]})
        if exception_meta["grounds"] == MOVE_WATCH:
            out.update({"move_watch_action": exception_meta["action"],
                        "signal_type": exception_meta["signal_type"],
                        "point_number": exception_meta.get("point_number")})
        if exception_meta["grounds"] == SELF_RESOLVE:
            out.update({"self_resolve_kind": exception_meta["self_resolve_kind"],
                        "before": exception_meta["before"], "after": exception_meta.get("after")})
    return out


def result(ledger: Ledger, op_id: str, outcome: Any, readback_ok: bool | None,
           now: str | datetime | None = None, event_id: str | None = None) -> dict[str, Any]:
    """外部実行結果を記録する。timeout/Noneは必ず照合待ちにする。"""
    at = _now_text(now)
    operation = ledger.get_op(op_id)
    if operation is None:
        raise KeyError(op_id)
    if operation.get("state") != RUNNING:
        return {"ok": False, "op_id": op_id, "state": operation.get("state"), "reason": "操作は実行中ではありません"}
    prior_result = operation.get("result") if isinstance(operation.get("result"), dict) else {}
    departure_action = prior_result.get("action") if prior_result.get("grounds") == DEPARTURE_NOTICE else None
    move_action = prior_result.get("action") if prior_result.get("grounds") == MOVE_WATCH else None
    if (outcome == "ok" and readback_ok is True and operation.get("kind") == "reminder"
            and departure_action in {"add", "update"} and not (isinstance(event_id, str) and event_id)):
        state, reason = PENDING, "読み戻し済みリマインダーのIDがありません。照合してください"
    elif (outcome == "ok" and readback_ok is True and operation.get("kind") == "reminder"
          and move_action in {"add", "update"} and not (isinstance(event_id, str) and event_id)):
        state, reason = PENDING, "読み戻し済みリマインダーのIDがありません。照合してください"
    elif (outcome == "ok" and readback_ok is True and operation.get("kind") == "reminder"
          and prior_result.get("grounds") == DONE_CHECK and prior_result.get("action") in {"add", "update"}
          and not (isinstance(event_id, str) and event_id)):
        state, reason = PENDING, "読み戻し済みリマインダーのIDがありません。照合してください"
    elif outcome == "ok" and readback_ok is True:
        state, reason = SUCCESS, "実物との読み戻しが一致しました"
    elif outcome == "error":
        state, reason = "失敗", "外部操作が失敗しました"
    else:
        state = PENDING
        reason = "実行結果を照合してください" if outcome in ("timeout", None) else "読み戻しが一致しません。実行結果を照合してください"
    saved = dict(prior_result)
    saved.update({"outcome": outcome, "readback_ok": readback_ok, "reason": reason})
    if isinstance(event_id, str) and event_id and saved.get("grounds") == TENTATIVE:
        # 聞かずに直すときの照合用に、予定ばんが置いた［仮］の予定IDを残す。
        saved["event_id"] = event_id
    if isinstance(event_id, str) and event_id and saved.get("grounds") == DEPARTURE_NOTICE \
            and saved.get("action") in {"add", "update"}:
        saved["reminder_id"] = event_id
    if isinstance(event_id, str) and event_id and saved.get("grounds") == MOVE_WATCH \
            and saved.get("action") in {"add", "update"}:
        saved["reminder_id"] = event_id
    if isinstance(event_id, str) and event_id and saved.get("grounds") == DONE_CHECK \
            and saved.get("action") in {"add", "update"}:
        saved["reminder_id"] = event_id
    if state == SUCCESS and saved.get("grounds") == DEPARTURE_NOTICE and saved.get("action") == "delete":
        saved["deleted_at"] = at
    if state == SUCCESS and saved.get("grounds") == MOVE_WATCH and saved.get("action") in {"delete", "complete"}:
        saved["deleted_at" if saved["action"] == "delete" else "completed_at"] = at
    if state == SUCCESS and saved.get("grounds") == DONE_CHECK and saved.get("action") in {"delete", "complete"}:
        saved["deleted_at" if saved["action"] == "delete" else "completed_at"] = at
    handoff = "渡し済み（未確認）" if operation.get("kind") == "send" else None
    ledger.set_op_state(op_id, state, handoff_state=handoff, result=saved, now=at)
    if state == SUCCESS and saved.get("grounds") == SELF_RESOLVE:
        _apply_self_resolve(ledger, saved, at)
    if state == SUCCESS and saved.get("grounds") == DEPARTURE_NOTICE \
            and saved.get("action") in {"update", "delete"}:
        origin = ledger.get_op(saved.get("origin_reminder_op_id"))
        if origin and origin.get("state") == SUCCESS:
            origin_result = dict(origin.get("result") or {})
            origin_result["replaced_by_op_id" if saved.get("action") == "update" else "deleted_at"] = op_id if saved.get("action") == "update" else at
            ledger.set_op_state(origin["op_id"], SUCCESS, result=origin_result, now=at)
    if state == SUCCESS and saved.get("grounds") == MOVE_WATCH \
            and saved.get("action") in {"update", "delete", "complete"}:
        origin = ledger.get_op(saved.get("origin_reminder_op_id"))
        if origin and origin.get("state") == SUCCESS:
            origin_result = dict(origin.get("result") or {})
            mark = {"update": "replaced_by_op_id", "delete": "deleted_at", "complete": "completed_at"}[saved["action"]]
            origin_result[mark] = op_id if mark == "replaced_by_op_id" else at
            ledger.set_op_state(origin["op_id"], SUCCESS, result=origin_result, now=at)
    if state == SUCCESS and saved.get("grounds") == DONE_CHECK \
            and saved.get("action") in {"update", "delete", "complete"}:
        origin = ledger.get_op(saved.get("origin_reminder_op_id"))
        if origin and origin.get("state") == SUCCESS:
            origin_result = dict(origin.get("result") or {})
            mark = {"update": "replaced_by_op_id", "delete": "deleted_at", "complete": "completed_at"}[saved["action"]]
            origin_result[mark] = op_id if mark == "replaced_by_op_id" else at
            ledger.set_op_state(origin["op_id"], SUCCESS, result=origin_result, now=at)
    return {"ok": True, "op_id": op_id, "state": state, "reason": reason}


def _apply_self_resolve(ledger: Ledger, saved: dict[str, Any], at: str) -> None:
    """聞かずに直した結果を元の［仮］へ写す（動かした先の時刻／消したら件数から外す）。"""
    origin = ledger.get_op(saved.get("origin_op_id"))
    if origin is None:
        return
    ores = dict(origin.get("result") or {})
    if saved.get("self_resolve_kind") == TENTATIVE_MOVE and isinstance(saved.get("after"), dict):
        ores.update({"current_start": saved["after"].get("event_start"), "current_end": saved["after"].get("event_end"),
                     "title": saved["after"].get("title", ores.get("title"))})
    elif saved.get("self_resolve_kind") == DUPLICATE_DELETE:
        ores.update({"tentative_closed": True, "closed_how": "削除", "closed_at": at, "closed_reason": "ダブり"})
    ledger.set_op_state(origin["op_id"], origin["state"], result=ores, now=at)


def reconcile(ledger: Ledger, op_id: str, found: bool, now: str | datetime | None = None) -> dict[str, Any]:
    """結果不明を見つかった=成功、見つからない=失敗へ確定する。"""
    at = _now_text(now)
    operation = ledger.get_op(op_id)
    if operation is None:
        raise KeyError(op_id)
    if operation.get("state") != PENDING:
        return {"ok": False, "op_id": op_id, "state": operation.get("state"), "reason": "照合待ちの操作ではありません"}
    if not isinstance(found, bool):
        raise ValueError("found は真偽値で指定してください")
    state = SUCCESS if found else "失敗"
    old_result = operation.get("result") if isinstance(operation.get("result"), dict) else {}
    saved = dict(old_result)
    saved["reconciled"] = found
    ledger.set_op_state(op_id, state, result=saved, now=at)
    return {"ok": True, "op_id": op_id, "state": state,
            "reason": "操作を確認しました" if found else "操作が見つからないため失敗として記録しました"}


def _cli() -> int:
    parser = argparse.ArgumentParser(description="予定ばんの外部操作関所")
    parser.add_argument("--config", required=True)
    parser.add_argument("action", choices=("check", "result", "reconcile", "close_tentative"))
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        data = json.load(sys.stdin)
        ledger = Ledger(cfg["db_path"])
        try:
            now = data.get("now")
            if args.action == "check":
                out = check(ledger, cfg, data.get("op"), now)
            elif args.action == "result":
                out = result(ledger, data["op_id"], data.get("outcome"), data.get("readback_ok"), now, data.get("event_id"))
            elif args.action == "reconcile":
                out = reconcile(ledger, data["op_id"], data.get("found"), now)
            else:
                out = close_tentative(ledger, data["op_id"], data.get("how"), now)
        finally:
            ledger.close()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, SchemaError, InvalidTransition) as exc:
        out = {"ok": False, "reason": str(exc)}
    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if out.get("allow", out.get("ok", False)) else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
