"""予定ばんが作成したリマインダーの完了・削除判断。場所情報は扱わない。"""
from __future__ import annotations

from datetime import datetime, timedelta
import re


OWNED_GROUNDS = {"move_watch", "departure_notice", "done_check"}


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except ValueError:
        return None


def _kind(result):
    if result.get("grounds") == "move_watch":
        return result.get("signal_type") if result.get("signal_type") in {"出発", "到着"} else None
    if result.get("grounds") in {"departure_notice", "done_check"}:
        return "時刻"
    return None


def index_created(ledger, now=None):
    """作成成功した操作だけから識別情報を台帳へ記録する。"""
    at = now or datetime.now().astimezone().isoformat()
    added = 0
    for op in ledger.list_ops():
        result = op.get("result") if isinstance(op.get("result"), dict) else {}
        kind = _kind(result)
        reminder_id, title = result.get("reminder_id"), result.get("reminder_title")
        if (op.get("kind") != "reminder" or op.get("state") != "成功（読み戻し済み）"
                or result.get("action") not in {"add", "update"} or kind is None
                or not isinstance(reminder_id, str) or not reminder_id
                or not isinstance(title, str) or not title
                or not isinstance(op.get("target_id"), str) or not op.get("target_id")
                or not isinstance(op.get("case_id"), str)):
            continue
        # update は既存IDの更新なので作成時刻を上書きしない。
        ledger.conn.execute("""INSERT OR IGNORE INTO reminder_cleanup
            (reminder_id,list_name,case_id,kind,title,created_at) VALUES(?,?,?,?,?,?)""",
            (reminder_id, op["target_id"], op["case_id"], kind, title,
             op.get("created_at") or at))
        added += ledger.conn.execute("SELECT changes()").fetchone()[0]
    ledger.conn.commit()
    return added


def _reason(ledger, row, at):
    case = ledger.get_case(row["case_id"])
    if not case:
        return None
    if case.get("status") == "取消":
        return "取消"
    end = _date(case.get("event_end"))
    if end:
        try:
            if at >= end + timedelta(hours=6):
                return "予定終了から6時間"
        except TypeError:
            pass
    if row["kind"] in {"出発", "到着"}:
        import json
        try:
            extra = json.loads(case.get("extra") or "{}")
        except (TypeError, ValueError):
            extra = {}
        move = extra.get("move_status", {}) if isinstance(extra, dict) else {}
        signals = move.get("signals", []) if isinstance(move, dict) else []
        match = re.search(r"#\d+(?:-(\d+))?$", row["title"])
        point_number = int(match.group(1)) if match and match.group(1) else None
        if any(isinstance(item, dict) and item.get("type") == row["kind"]
               and item.get("point_number") == point_number for item in signals):
            return "合図受信"
    return None


def plan(ledger, settings, now, reminders):
    """安全な処理候補を返す。外部操作は呼び出し側が読戻し後に実行する。"""
    at = _date(now)
    if at is None:
        raise ValueError("now は時差付き日時が必要です")
    mode = settings.get("reminder_cleanup", "完了にするだけ")
    if mode == "何もしない":
        return {"complete": [], "delete": [], "pending": 0}
    if mode not in {"完了にするだけ", "片づける（完了→7日後に削除）"}:
        raise ValueError("reminder_cleanup の設定が不正です")
    index_created(ledger, now)
    items = {r.get("id"): r for r in reminders if isinstance(r, dict) and isinstance(r.get("id"), str)}
    complete, delete = [], []
    rows = ledger.conn.execute("SELECT * FROM reminder_cleanup WHERE deleted_at IS NULL AND mismatch_at IS NULL").fetchall()
    for row in rows:
        if row["list_name"] == "テスト":
            continue
        item = items.get(row["reminder_id"])
        if not item or item.get("list_name", item.get("list_id")) != row["list_name"]:
            continue
        if item.get("title") != row["title"]:
            continue
        if not row["completed_at"] and _reason(ledger, row, at):
            if item.get("completed") is True:
                record_completed(ledger, row["reminder_id"], at.isoformat())
            else:
                complete.append(dict(row))
        elif row["completed_at"] and mode == "片づける（完了→7日後に削除）":
            completed = _date(row["completed_at"])
            if completed and at >= completed + timedelta(days=7):
                delete.append(dict(row))
    return {"complete": complete, "delete": delete, "pending": 0}


def record_completed(ledger, reminder_id, at):
    ledger.conn.execute("UPDATE reminder_cleanup SET completed_at=COALESCE(completed_at,?) WHERE reminder_id=?",
                        (at, reminder_id))
    ledger.conn.commit()


def record_deleted(ledger, reminder_id, at):
    ledger.conn.execute("UPDATE reminder_cleanup SET deleted_at=? WHERE reminder_id=?", (at, reminder_id))
    ledger.conn.commit()


def record_mismatch(ledger, reminder_id, at):
    ledger.conn.execute("UPDATE reminder_cleanup SET mismatch_at=? WHERE reminder_id=?", (at, reminder_id))
    ledger.conn.commit()


def deletion_matches(ledger, reminder_id, fresh_item, at):
    """IOSReadReminders の直前読戻しを検証。相違は消さずに台帳へ残す。"""
    row = ledger.conn.execute("SELECT * FROM reminder_cleanup WHERE reminder_id=?", (reminder_id,)).fetchone()
    if row is None or row["list_name"] == "テスト" or not row["completed_at"]:
        return False
    completed = _date(row["completed_at"])
    current = _date(at)
    if (completed is None or current is None or current < completed + timedelta(days=7)
            or not isinstance(fresh_item, dict)
            or fresh_item.get("id") != row["reminder_id"]
            or fresh_item.get("title") != row["title"]
            or fresh_item.get("list_name", fresh_item.get("list_id")) != row["list_name"]
            or fresh_item.get("completed") is not True):
        record_mismatch(ledger, reminder_id, at)
        return False
    return True
