"""夜間スナップショットと台帳を照合し、要対応の差分を記録する。"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import load as load_config
from .ledger import Ledger


APP_MARKER = "yoteiban-op:"
SUCCESS = "成功（読み戻し済み）"
UNKNOWN = "実行結果不明（要照合）"
TERMINAL_CASES = {"済んだ", "見送り", "取消"}


def _at(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Tokyo"))
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("now はISO 8601日時で指定してください") from exc
    else:
        raise ValueError("now はISO 8601日時で指定してください")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("now はタイムゾーン付きISO 8601日時で指定してください")
    return dt


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None and dt.utcoffset() is not None else None


def _is_old(value: Any, now: datetime, age: timedelta) -> bool:
    then = _parse(value)
    if then is None:
        return False
    try:
        return now - then > age
    except TypeError:
        return False


def _finding(kind: str, refs: list[Any], text: str, urgency: str = "今日中") -> dict[str, Any]:
    return {"type": kind, "refs": refs, "text": text, "urgency": urgency}


def _valid_marker(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(APP_MARKER) and len(value) > len(APP_MARKER)


def audit(ledger: Ledger, cfg: dict[str, Any], snapshot: dict[str, Any],
          now: str | datetime | None = None) -> list[dict[str, Any]]:
    """台帳と外部状態を照合してFinding辞書の一覧を返す。"""
    at = _at(now)
    at_text = at.isoformat()
    if not isinstance(cfg, dict):
        raise ValueError("cfg は設定オブジェクトで指定してください")
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot はオブジェクトで指定してください")
    calendars = snapshot.get("calendar", [])
    reminders = snapshot.get("reminders", [])
    notion_rows = snapshot.get("notion", [])
    if not all(isinstance(value, list) for value in (calendars, reminders, notion_rows)):
        raise ValueError("snapshot.calendar / reminders / notion はリストで指定してください")

    findings: list[dict[str, Any]] = []
    cal_targets = {value for value in (cfg.get("cal_draft_id"), cfg.get("cal_personal_id")) if isinstance(value, str) and value}
    readonly = cfg.get("cal_readonly_ids", [])
    if not isinstance(readonly, list) or any(not isinstance(value, str) for value in readonly):
        raise ValueError("cal_readonly_ids はID文字列のリストで指定してください")
    readonly_calendars = set(readonly)
    dedicated_reminder = cfg.get("reminder_list_id")

    # 操作印が無い書き込みを、予定ばんが書ける対象に限って探す。
    for item in calendars:
        if not isinstance(item, dict):
            continue
        calendar_id = item.get("calendar_id")
        if isinstance(calendar_id, str) and calendar_id in cal_targets and not _valid_marker(item.get("marker")):
            findings.append(_finding("印の無い書き込み", [item.get("id"), calendar_id],
                                     "予定ばんが管理するカレンダーに印の無い予定があります", "今日中"))
        if isinstance(calendar_id, str) and calendar_id in readonly_calendars and _valid_marker(item.get("marker")):
            findings.append(_finding("家用カレンダーへの書き込み", [item.get("id"), calendar_id],
                                     "家用または共有の読み取り専用カレンダーに予定ばんの印があります", "すぐ"))
    for item in reminders:
        if isinstance(item, dict) and isinstance(item.get("list_id"), str) and item.get("list_id") == dedicated_reminder and not _valid_marker(item.get("marker")):
            findings.append(_finding("印の無い書き込み", [item.get("id"), item.get("list_id")],
                                     "専用リマインダーリストに印の無い項目があります", "今日中"))

    operations = ledger.list_ops()
    calendar_markers = {
        item.get("marker") for item in calendars
        if isinstance(item, dict) and _valid_marker(item.get("marker"))
        and isinstance(item.get("calendar_id"), str) and item.get("calendar_id") in cal_targets
    }
    reminder_markers = {
        item.get("marker") for item in reminders
        if isinstance(item, dict) and _valid_marker(item.get("marker"))
        and isinstance(item.get("list_id"), str) and item.get("list_id") == dedicated_reminder
    }
    notion_case_ids = {row.get("case_id") for row in notion_rows
                       if isinstance(row, dict) and isinstance(row.get("case_id"), str)}
    for op in operations:
        state, kind, op_id = op.get("state"), op.get("kind"), op.get("op_id")
        op_marker = APP_MARKER + str(op_id)
        if state == SUCCESS:
            present = None
            if kind in {"cal_draft", "cal_personal"}:
                present = op_marker in calendar_markers
            elif kind == "reminder":
                present = op_marker in reminder_markers
            elif kind == "notion":
                present = op.get("case_id") in notion_case_ids
            if present is False:
                findings.append(_finding("成功した書き込みが見つからない", [op_id, op.get("case_id")],
                                         "台帳は成功ですが、スナップショットに対応する実物がありません", "すぐ"))
        if state == UNKNOWN and _is_old(op.get("updated_at") or op.get("created_at"), at, timedelta(hours=24)):
            findings.append(_finding("24時間を過ぎた要照合", [op_id, op.get("case_id")],
                                     "実行結果不明の操作が24時間を超えて未照合です", "すぐ"))
        if state == "予約":
            findings.append(_finding("予約のまま止まった操作", [op_id, op.get("case_id")],
                                     "操作が予約状態のまま実行に進んでいません", "今日中"))

    rows = ledger.conn.execute("SELECT approval_id,expires_at FROM approvals WHERE state='有効'").fetchall()
    for row in rows:
        expiry = _parse(row["expires_at"])
        if expiry is not None:
            try:
                expired = expiry <= at
            except TypeError:
                expired = False
            if expired:
                findings.append(_finding("期限切れ承認が有効のまま", [row["approval_id"]],
                                         "期限を過ぎた承認が有効状態のまま残っています", "すぐ"))

    for case in ledger.list_cases():
        due = _parse(case.get("next_review_at"))
        updated = _parse(case.get("updated_at"))
        if due is None or case.get("status") in TERMINAL_CASES:
            continue
        try:
            stale = due <= at and (updated is None or updated <= due)
        except TypeError:
            stale = False
        if stale:
            findings.append(_finding("確認時刻を過ぎた案件", [case.get("case_id")],
                                     "次回確認時刻を過ぎても案件が更新されていません", "今日中"))

    cases = {case.get("case_id"): case for case in ledger.list_cases()}
    for row in notion_rows:
        if not isinstance(row, dict):
            continue
        case_id = row.get("case_id")
        if not isinstance(case_id, str):
            continue
        case = cases.get(case_id)
        if row.get("edited_by_user") is True:
            findings.append(_finding("Notion本人の直し", [row.get("case_id")],
                                     "Notion上で本人が内容を直しています。台帳との同期前に確認してください", "今日中"))
        elif case is not None and row.get("status") != case.get("status"):
            findings.append(_finding("Notion状態ずれ", [row.get("case_id")],
                                     "Notionの状態と台帳の状態が一致しません", "今日中"))
    # mirror_checks は個別の本文を複製せず、種別別件数と参照だけを保存する。
    counts = dict(Counter(item["type"] for item in findings))
    summary = {"count": len(findings), "by_type": counts,
               "refs": [ref for item in findings for ref in item["refs"] if ref is not None]}
    with ledger.tx():
        ledger.conn.execute(
            "INSERT OR REPLACE INTO mirror_checks(at,target,diff) VALUES(?,?,?)",
            (at_text, "nightly", json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )
    return findings


def _cli() -> int:
    parser = argparse.ArgumentParser(description="台帳と外部スナップショットの夜間監査")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        snapshot = json.load(sys.stdin)
        ledger = Ledger(cfg["db_path"])
        try:
            findings = audit(ledger, cfg, snapshot, snapshot.get("now") if isinstance(snapshot, dict) else None)
        finally:
            ledger.close()
        out = {"findings": findings}
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        out = {"error": str(exc), "findings": []}
    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 1 if out.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(_cli())
