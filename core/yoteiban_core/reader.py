"""入力をローカル規則、速いAI、強いAIの順で読み取り台帳へ渡す。"""
from __future__ import annotations
import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import ai_gate
from . import rule_reader

JST = ZoneInfo("Asia/Tokyo")
_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_PATH = _ROOT / "schemas" / "reading.schema.json"
_PROMPT_DIR = _ROOT / "prompts"
_IMPORTANT_ROLE = {"学校", "仕事", "家族"}
_IMPORTANT_TEXT = re.compile(r"締切|期限|取消|取り消|中止|キャンセル|変更|金額|料金|費用|支払|お金")


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _now_value(now):
    if now is None:
        return datetime.now(JST)
    if isinstance(now, str):
        value = datetime.fromisoformat(now.replace("Z", "+00:00"))
        return value.replace(tzinfo=JST) if value.tzinfo is None else value.astimezone(JST)
    if not isinstance(now, datetime):
        raise TypeError("now は datetime またはISO日時文字列で指定してください")
    return now.replace(tzinfo=JST) if now.tzinfo is None else now.astimezone(JST)


def _base_date(received_at):
    value = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("received_at にタイムゾーンが必要です")
    return value.astimezone(JST).date()


def _prompt(name):
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


def _important(record, text):
    return record.get("sender_role") in _IMPORTANT_ROLE or bool(_IMPORTANT_TEXT.search(text or ""))


def _items(result):
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return [item for item in result["items"] if isinstance(item, dict)]
    return []


def _signature(items):
    return sorted((str(item.get("kind", "")),
                   str(item.get("event_start") or "")[:10],
                   re.sub(r"\s+", "", str(item.get("title", "")))) for item in items)


def _merge_missing(primary, extra):
    merged = list(primary)
    seen = set(_signature(merged))
    for item in extra:
        key = (str(item.get("kind", "")), str(item.get("event_start") or "")[:10],
               re.sub(r"\s+", "", str(item.get("title", ""))))
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def _call(provider, tier, task, payload, schema, ledger, settings, now):
    if provider is None:
        return None
    try:
        return ai_gate.call(task, payload, schema, provider, tier,
                            ledger=ledger, settings=settings, now=now)
    except Exception:
        # A provider outage or malformed integration leaves the item for a human/bot reader.
        return None


def _mark_manual(ledger, input_id, now, reason):
    """Keep unreadable records visible as in-progress and add an audit history row."""
    with ledger.tx():
        row = ledger.conn.execute("SELECT recv_state FROM raw_inputs WHERE input_id=?", (input_id,)).fetchone()
        if row is None:
            raise KeyError(input_id)
        if row["recv_state"] != "読み取り中":
            ledger.conn.execute("UPDATE raw_inputs SET recv_state='読み取り中' WHERE input_id=?", (input_id,))
            at = now.isoformat()
            ledger._record_history("input:" + input_id,
                {"recv_state": {"before": row["recv_state"], "after": "読み取り中"}},
                reason, at, actor="ボット", basis=input_id)


def _already_read(ledger, input_id, read_level):
    rows = ledger.conn.execute("SELECT * FROM cases WHERE cause_input_id=? ORDER BY created_at,case_id", (input_id,)).fetchall()
    found = []
    for row in rows:
        item = dict(row)
        for field in ("kind", "title", "event_start", "due_at", "certainty", "urgency"):
            item.setdefault(field, None)
        item["date_only"] = bool(item.get("event_start") and len(item["event_start"]) <= 10)
        item["needs_reply"] = False
        item["reply_by"] = None
        item["questions"] = []
        found.append(item)
    return {"items": found, "read_level": read_level or "記録済み", "checks": {"already_read": True}}


def read_input(ledger, input_id, settings, fast_provider, strong_provider, now):
    """Read one stored input and return items, processing level and branch checks.

    Provider functions are injected; this function never performs network I/O itself.
    External calls receive body_masked only. If no safe body or usable result exists,
    the source remains in `読み取り中` for the bot's manual reading queue.
    """
    at = _now_value(now)
    row = ledger.conn.execute("SELECT * FROM raw_inputs WHERE input_id=?", (input_id,)).fetchone()
    if row is None:
        raise KeyError(input_id)
    record = dict(row)
    if record.get("recv_state") == "読み取り済み":
        return _already_read(ledger, input_id, record.get("read_level"))
    raw_text = record.get("body_text") or ""
    masked_text = record.get("body_masked") or ""
    base = _base_date(record["received_at"])
    program_items = rule_reader.read(raw_text, base)
    enabled = _get(_get(settings, "ai", {}), "enabled", _get(settings, "ai_enabled", False)) is True
    high_importance = _important(record, raw_text)
    checks = {"program": "読み取り済み" if program_items is not None else "読めず",
              "important": high_importance, "double_check": False,
              "agreement": None, "strong_used": False, "missed_check": False}

    if program_items is not None and (not high_importance or not enabled or not masked_text.strip()):
        chosen = program_items
        read_level = "プログラム"
        if high_importance and enabled and not masked_text.strip():
            checks["double_check_unavailable"] = "伏せ字本文がありません"
    elif program_items is None and (not enabled or not masked_text.strip()):
        reason = "AIを使わない設定" if not enabled else "伏せ字本文がありません"
        _mark_manual(ledger, input_id, at, "手動読み取り待ち: " + reason)
        return {"items": [], "read_level": "ボットが読む", "checks": {**checks, "needs_bot_read": True, "reason": reason}}
    else:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        payload = {"body": masked_text, "base_date": base.isoformat()}
        if program_items is not None:
            first_items, read_level = program_items, "プログラム"
        else:
            fast_result = _call(fast_provider, "fast", _prompt("read_fast.md"), payload, schema, ledger, settings, at)
            if fast_result is None:
                _mark_manual(ledger, input_id, at, "速いAIで読めず、手動読み取り待ち")
                return {"items": [], "read_level": "ボットが読む", "checks": {**checks, "needs_bot_read": True, "reason": "速いAIで読み取れません"}}
            first_items, read_level = _items(fast_result), "速いAI"

        chosen = first_items
        if high_importance:
            checks["double_check"] = True
            second_result = _call(fast_provider, "fast", _prompt("double_check.md"), payload, schema, ledger, settings, at)
            second_items = _items(second_result) if second_result is not None else None
            if second_items is not None:
                checks["agreement"] = _signature(first_items) == _signature(second_items)
            if second_items is None or checks["agreement"] is False:
                checks["strong_used"] = True
                strong_payload = {**payload, "first_read": first_items, "independent_read": second_items or []}
                strong_result = _call(strong_provider, "strong", _prompt("read_strong.md"), strong_payload, schema, ledger, settings, at)
                if strong_result is None:
                    _mark_manual(ledger, input_id, at, "重要な連絡の照合に失敗し、手動読み取り待ち")
                    return {"items": [], "read_level": "ボットが読む", "checks": {**checks, "needs_bot_read": True, "reason": "強いAIで照合できません"}}
                chosen, read_level = _items(strong_result), "強いAI"

        missed_result = _call(fast_provider, "fast", _prompt("missed_check.md"), payload, schema, ledger, settings, at)
        if missed_result is not None:
            missing = _items(missed_result)
            checks["missed_check"] = True
            chosen = _merge_missing(chosen, missing)

    # Persist only ledger fields; date_text and time_text remain useful to checks,
    # while record_reading stores recognized values in their supported columns.
    case_ids = ledger.record_reading(input_id, chosen, read_level, now=at.isoformat())
    if not chosen:
        with ledger.tx():
            current = ledger.conn.execute("SELECT recv_state,read_level FROM raw_inputs WHERE input_id=?", (input_id,)).fetchone()
            if current and current["recv_state"] != "読み取り済み":
                ledger.conn.execute("UPDATE raw_inputs SET recv_state='読み取り済み',read_level=? WHERE input_id=?", (read_level,input_id))
                ledger._record_history("input:" + input_id,
                    {"recv_state":{"before":current["recv_state"],"after":"読み取り済み"},"read_level":{"before":current["read_level"],"after":read_level}},
                    "入力の読み取り完了（該当なし）", at.isoformat(), actor="ボット", basis=input_id)
    checks["case_ids"] = case_ids
    return {"items": chosen, "read_level": read_level, "checks": checks}
