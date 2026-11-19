"""外部操作を実行する前に、設定・権限・重複を確認して台帳へ記録する。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
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
GROUNDS = {FORCED_ADD, TENTATIVE}
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
        trusted = cfg.get("trusted_source_ids")
        source = basis.get("source_id")
        if (not isinstance(trusted, list) or not isinstance(source, str) or not source
                or source not in [x for x in trusted if isinstance(x, str)]):
            return False, "出どころが信頼する出どころに登録されていません", {}
        if basis.get("datetime_in_text") is not True:
            return False, "日付と時刻が文面に書かれていません", {}
        for key, reason in (("guessed", "推測した日時は強制追加しません"),
                            ("forwarded", "転送は強制追加しません"),
                            ("group", "グループからの連絡は強制追加しません"),
                            ("has_instruction", "命令の入った連絡は強制追加しません")):
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
    meta.update({"tentative_max": limit, "notify_level": "なし"})
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
    if op.get("grounds") is not None or op.get("basis") is not None:
        exception_ok, reason, exception_meta = _exception_check(ledger, cfg, op)
        if not exception_ok:
            return _deny(reason)

    if kind == "cal_personal":
        if not approval_ok and not delegation_ok and not exception_ok:
            return _deny("本人カレンダーには有効な承認か範囲内の有効な委任、または条件を満たす強制追加・［仮］が必要です")
    elif kind == "reminder":
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
    return out


def result(ledger: Ledger, op_id: str, outcome: Any, readback_ok: bool | None,
           now: str | datetime | None = None) -> dict[str, Any]:
    """外部実行結果を記録する。timeout/Noneは必ず照合待ちにする。"""
    at = _now_text(now)
    operation = ledger.get_op(op_id)
    if operation is None:
        raise KeyError(op_id)
    if operation.get("state") != RUNNING:
        return {"ok": False, "op_id": op_id, "state": operation.get("state"), "reason": "操作は実行中ではありません"}
    if outcome == "ok" and readback_ok is True:
        state, reason = SUCCESS, "実物との読み戻しが一致しました"
    elif outcome == "error":
        state, reason = "失敗", "外部操作が失敗しました"
    else:
        state = PENDING
        reason = "実行結果を照合してください" if outcome in ("timeout", None) else "読み戻しが一致しません。実行結果を照合してください"
    old_result = operation.get("result") if isinstance(operation.get("result"), dict) else {}
    saved = dict(old_result)
    saved.update({"outcome": outcome, "readback_ok": readback_ok, "reason": reason})
    handoff = "渡し済み（未確認）" if operation.get("kind") == "send" else None
    ledger.set_op_state(op_id, state, handoff_state=handoff, result=saved, now=at)
    return {"ok": True, "op_id": op_id, "state": state, "reason": reason}


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
                out = result(ledger, data["op_id"], data.get("outcome"), data.get("readback_ok"), now)
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
