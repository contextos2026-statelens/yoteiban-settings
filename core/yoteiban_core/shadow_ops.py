"""委任の影の運用を記録・集計する。ここから外部操作は実行しない。"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _now(value: str | datetime | None) -> str:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Tokyo")).isoformat()
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
    return dt.isoformat()


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("影の運用記録はJSONで表せる値にしてください") from exc


def _template(ledger: Any, del_id: str) -> str | None:
    try:
        delegation = ledger.conn.execute("SELECT scope FROM delegations WHERE del_id=?", (del_id,)).fetchone()
        if delegation is None:
            return None
        scope = json.loads(delegation["scope"])
        template_id = scope.get("template_id") if isinstance(scope, dict) else None
        if not template_id:
            return None
        row = ledger.conn.execute("SELECT body FROM templates WHERE template_id=?", (template_id,)).fetchone()
        return row["body"] if row else None
    except (AttributeError, TypeError, json.JSONDecodeError):
        return None


def _values(value: Any, template: str | None) -> dict[str, Any] | None:
    if isinstance(value, dict):
        for key in ("values", "slots", "placeholder_values"):
            candidate = value.get(key)
            if isinstance(candidate, dict):
                return candidate
        body = value.get("body", value.get("text"))
    elif isinstance(value, str):
        body = value
    else:
        return None
    if not isinstance(body, str):
        return None
    if template is None:
        return None
    names = PLACEHOLDER.findall(template)
    pieces: list[str] = []
    cursor = 0
    for match in PLACEHOLDER.finditer(template):
        pieces.append(re.escape(template[cursor:match.start()]))
        pieces.append("([\\s\\S]*?)")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    try:
        found = re.fullmatch("".join(pieces), body)
    except re.error:
        return None
    if found is None:
        return None
    result: dict[str, Any] = {}
    for name, item in zip(names, found.groups()):
        if name in result and result[name] != item:
            return None
        result[name] = item
    return result


def _slot_values(proposed: Any, user_final: Any, template: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    before, after = _values(proposed, template), _values(user_final, template)
    # G5の呼び出し元がテンプレートの値だけを渡す形も受け取る。
    if before is None and after is None and isinstance(proposed, dict) and isinstance(user_final, dict):
        if set(proposed) == set(user_final) and all(isinstance(k, str) for k in proposed):
            return proposed, user_final
    return before, after


def _same_slots(proposed: Any, user_final: Any, template: str | None) -> bool:
    before, after = _slot_values(proposed, user_final, template)
    if before is not None and after is not None:
        return before.keys() == after.keys() and before == after
    # 同じ完成文なら全ての空欄値も同じである。
    if isinstance(proposed, str) and isinstance(user_final, str):
        return proposed == user_final
    if isinstance(proposed, dict) and isinstance(user_final, dict):
        p = proposed.get("body", proposed.get("text"))
        u = user_final.get("body", user_final.get("text"))
        return isinstance(p, str) and p == u
    return False


def record(ledger: Any, del_id: str, proposed: Any, user_final: Any,
           now: str | datetime | None = None) -> dict[str, Any]:
    """影の運用中の提案と本人の最終文を保存し、空欄値が同一ならmatch=1。"""
    if not isinstance(del_id, str) or not del_id:
        raise ValueError("del_id が必要です")
    at = _now(now)
    proposed_json, final_json = _json(proposed), _json(user_final)
    row = ledger.conn.execute("SELECT state FROM delegations WHERE del_id=?", (del_id,)).fetchone()
    if row is None:
        raise KeyError(del_id)
    if row["state"] != "影の運用中":
        raise ValueError("影の運用中の委任だけ記録できます")
    template = _template(ledger, del_id)
    same = _same_slots(proposed, user_final, template)
    record_id = secrets.token_hex(12)
    with ledger.tx():
        ledger.conn.execute("INSERT INTO shadow_records(id,del_id,at,proposed,user_final,match) VALUES(?,?,?,?,?,?)",
                            (record_id, del_id, at, proposed_json, final_json, int(same)))
    return {"id": record_id, "del_id": del_id, "match": int(same)}


def _edit_summary(proposed: Any, user_final: Any, template: str | None) -> list[str]:
    before, after = _slot_values(proposed, user_final, template)
    if before is not None and after is not None:
        names = sorted(set(before) | set(after))
        changed = [name for name in names if before.get(name) != after.get(name) or (name in before) != (name in after)]
        return [f"文の型の空欄「{name}」が修正されました" for name in changed]
    return ["提案と本人の文面に差があります（空欄の差を特定できません）"]


def report(ledger: Any, del_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    """影の運用の一致率と修正点を集計する。candidateは提案値で、スイッチは変更しない。"""
    if not isinstance(del_id, str) or not del_id:
        raise ValueError("del_id が必要です")
    if not isinstance(settings, dict):
        raise ValueError("settings は辞書で指定してください")
    rows = ledger.conn.execute("SELECT proposed,user_final,match FROM shadow_records WHERE del_id=? ORDER BY at,id", (del_id,)).fetchall()
    n = len(rows)
    matches = sum(1 for row in rows if row["match"] == 1)
    edits: list[str] = []
    template = _template(ledger, del_id)
    for row in rows:
        if row["match"]:
            continue
        try:
            proposed, user_final = json.loads(row["proposed"]), json.loads(row["user_final"])
        except json.JSONDecodeError:
            edits.append("提案と本人の文面に差があります")
            continue
        edits.extend(_edit_summary(proposed, user_final, template))
    threshold = settings.get("shadow_pass", {})
    if not isinstance(threshold, dict):
        threshold = {}
    required_n = threshold.get("n", 30)
    max_edits = threshold.get("max_edits", 1)
    valid_thresholds = (isinstance(required_n, int) and not isinstance(required_n, bool) and required_n > 0
                        and isinstance(max_edits, int) and not isinstance(max_edits, bool) and max_edits >= 0)
    candidate = bool(valid_thresholds and n >= required_n and len(edits) <= max_edits)
    return {"n": n, "match_rate": matches / n if n else 0.0, "edits": edits, "candidate": candidate}


def guard(op: dict[str, Any]) -> dict[str, Any]:
    """影の運用中の委任を使った外部操作は必ず拒否する。"""
    if not isinstance(op, dict):
        return {"allow": False, "reason": "操作の形が正しくありません"}
    delegation = op.get("delegation")
    if not isinstance(delegation, dict) and op.get("state") == "影の運用中":
        delegation = op
    if isinstance(delegation, dict) and delegation.get("state") == "影の運用中":
        return {"allow": False, "reason": "影の運用中は外部操作を実行できません"}
    return {"allow": True, "reason": "影の運用による拒否対象ではありません"}
