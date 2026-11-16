#!/usr/bin/env python3
"""予定ばん 第2段階試作：外部送信の委任管理（delegation）。

相手・内容の範囲・有効期間を持つ委任と、承認済みテンプレートの再利用判定、
引き渡し・送信状態を箱内の JSON ストアで扱う。実送信は行わない。

既存の casebook.py / queue.py / worklog.py とは独立した新規ファイルであり、
それらの振る舞いを変えない。スキル／ルーチンへの配線、本番ストアの利用、
外部送信、カレンダー書き込みは行わない。送信前に文面を確認する規則は維持する。

状態語彙（引き渡し・送信、実行結果不明）と、原子的書き込み（一時ファイルへ
書いてから rename）、冪等な再実行の作法は casebook.py に合わせる。

使い方:
  python3 delegation.py --file /tmp/delegation.json add-delegation \\
    --delegation-id del-001 --counterpart-id role-partner \\
    --scope schedule-confirm --valid-from "2026-11-01 00:00 JST" \\
    --valid-until "2026-11-30 23:59 JST" --share-allowed --send-allowed
  対象ストアは --file 必須（既定の本番パスは持たない）。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# 語彙（casebook.py の引き渡し・送信系統と揃える。混同しない）
# ---------------------------------------------------------------------------

HAND_BEFORE = "渡し前（未送信）"
HAND_UNCONFIRMED = "渡し済み（未確認）"
HAND_SENT = "送信完了"
HAND_REPLIED = "返事あり"
HANDOFF_STATUSES = (HAND_BEFORE, HAND_UNCONFIRMED, HAND_SENT, HAND_REPLIED)

# 返事待ちの追跡行は、送信完了のあとだけ作る
REPLY_WAITING = "返事待ち"

# 案件側の語。送信状態には置かない。委任側では別フィールドで扱う。
OUTCOME_UNKNOWN = "実行結果不明（要照合）"

HANDOFF_TRANSITIONS: dict[str, frozenset[str]] = {
    HAND_BEFORE: frozenset({HAND_UNCONFIRMED}),
    HAND_UNCONFIRMED: frozenset({HAND_SENT}),
    HAND_SENT: frozenset({HAND_REPLIED}),
    HAND_REPLIED: frozenset(),
}

# 判定の語彙
DECISION_CONFIRM = "要確認"
DECISION_SKIP = "確認省略可"
DECISION_CANNOT_FINALIZE = "確定不可"
DECISIONS = (DECISION_CONFIRM, DECISION_SKIP, DECISION_CANNOT_FINALIZE)

# 送信系統に置いてはいけない語
FORBIDDEN_IN_HANDOFF = (
    "受信済み",
    "分析中",
    "保存済み",
    "完了",
    "失敗・要再試行",
    "提案前",
    "提案中",
    "承認待ち",
    "承認済み",
    "実行中",
    "実行完了",
    OUTCOME_UNKNOWN,
    "却下",
    "期限切れ",
    "保留",
    REPLY_WAITING,
    DECISION_CONFIRM,
    DECISION_SKIP,
    DECISION_CANNOT_FINALIZE,
)

JST_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?:\:(\d{2}))?\s*JST$"
)
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

DELEGATION_FIELDS = (
    "delegation_id",
    "counterpart_id",
    "scope",
    "valid_from",
    "valid_until",
    "share_allowed",
    "send_allowed",
    "created_at",
    "updated_at",
)

TEMPLATE_FIELDS = (
    "template_id",
    "delegation_id",
    "approved",
    "body",
    "usage_conditions",
    "created_at",
    "updated_at",
)

DRAFT_FIELDS = (
    "draft_id",
    "delegation_id",
    "template_id",
    "counterpart_id",
    "scope",
    "variables",
    "body",
    "decision",
    "finalized",
    "confirmed_at",
    "confirmed_by",
    "handoff_status",
    "handed_off_at",
    "confirmation_received_at",
    "confirmation_ref",
    "reply_track",
    "outcome_unknown",
    "outcome_unknown_reason",
    "retry_send",
    "created_at",
    "updated_at",
)

AUDIT_FIELDS = (
    "at",
    "actor_id",
    "action",
    "delegation_id",
    "template_id",
    "draft_id",
    "from_status",
    "to_status",
    "decision",
)


class DelegationError(Exception):
    """ユーザー向けの明確なエラー。"""


def now_jst() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S JST")


def require_jst(label: str, value: str) -> str:
    s = value.strip()
    if not JST_RE.match(s):
        raise DelegationError(
            f"{label} は JST 形式で指定してください（例: 2026-11-17 01:00 JST）"
        )
    return s


def parse_jst(label: str, value: str) -> datetime:
    s = require_jst(label, value)
    m = JST_RE.match(s)
    assert m is not None
    return datetime(
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3)),
        int(m.group(4)),
        int(m.group(5)),
        int(m.group(6) or 0),
    )


def in_period(valid_from: str, valid_until: str, at: str) -> bool:
    t = parse_jst("判定時刻", at)
    start = parse_jst("有効期間の開始", valid_from)
    end = parse_jst("有効期間の終了", valid_until)
    return start <= t <= end


def require_id(label: str, value: str) -> str:
    key = (value or "").strip()
    if not ID_RE.match(key):
        raise DelegationError(
            f"{label} は英数字と . _ : - のみ、1〜128文字で指定してください"
        )
    return key


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
    return {
        "version": 1,
        "delegations": {},
        "templates": {},
        "drafts": {},
        "audit": [],
    }


def empty_delegation(
    delegation_id: str,
    counterpart_id: str,
    scope: list[str],
    valid_from: str,
    valid_until: str,
    share_allowed: bool,
    send_allowed: bool,
) -> dict[str, Any]:
    ts = now_jst()
    return {
        "delegation_id": delegation_id,
        "counterpart_id": counterpart_id,
        "scope": list(scope),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "share_allowed": bool(share_allowed),
        "send_allowed": bool(send_allowed),
        "created_at": ts,
        "updated_at": ts,
    }


def empty_template(
    template_id: str,
    delegation_id: str,
    body: str,
    usage_conditions: dict[str, list[str]],
    approved: bool,
) -> dict[str, Any]:
    ts = now_jst()
    return {
        "template_id": template_id,
        "delegation_id": delegation_id,
        "approved": bool(approved),
        "body": body,
        "usage_conditions": usage_conditions,
        "created_at": ts,
        "updated_at": ts,
    }


def empty_draft(
    draft_id: str,
    delegation_id: str,
    template_id: str,
    counterpart_id: str,
    scope: str,
    variables: dict[str, str],
    body: str,
    decision: str,
    finalized: bool,
) -> dict[str, Any]:
    ts = now_jst()
    return {
        "draft_id": draft_id,
        "delegation_id": delegation_id,
        "template_id": template_id,
        "counterpart_id": counterpart_id,
        "scope": scope,
        "variables": dict(variables),
        "body": body,
        "decision": decision,
        "finalized": bool(finalized),
        "confirmed_at": "",
        "confirmed_by": "",
        "handoff_status": HAND_BEFORE,
        "handed_off_at": "",
        "confirmation_received_at": "",
        "confirmation_ref": "",
        "reply_track": None,
        "outcome_unknown": False,
        "outcome_unknown_reason": "",
        "retry_send": False,
        "created_at": ts,
        "updated_at": ts,
    }


def load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_store()
    if path.is_dir():
        raise DelegationError(f"ストアパスがディレクトリです: {path}")
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return empty_store()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise DelegationError(f"{path}: JSON として読めません: {e}") from e
    if not isinstance(data, dict):
        raise DelegationError(f"{path}: オブジェクト（辞書）ではありません")
    for key in ("delegations", "templates", "drafts"):
        val = data.get(key)
        if val is None:
            val = {}
        if not isinstance(val, dict):
            raise DelegationError(f"{path}: {key} は辞書である必要があります")
        data[key] = val
    audit = data.get("audit")
    if audit is None:
        audit = []
    if not isinstance(audit, list):
        raise DelegationError(f"{path}: audit は配列である必要があります")
    data["audit"] = audit
    data.setdefault("version", 1)
    return data


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """ストア全体を一時ファイルへ書いてから rename する。半端なファイルを残さない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".delegation-", suffix=".json", dir=str(path.parent)
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


def get_delegation(store: dict[str, Any], delegation_id: str) -> dict[str, Any] | None:
    rec = store["delegations"].get(delegation_id)
    return rec if isinstance(rec, dict) else None


def get_template(store: dict[str, Any], template_id: str) -> dict[str, Any] | None:
    rec = store["templates"].get(template_id)
    return rec if isinstance(rec, dict) else None


def get_draft(store: dict[str, Any], draft_id: str) -> dict[str, Any] | None:
    rec = store["drafts"].get(draft_id)
    return rec if isinstance(rec, dict) else None


def require_delegation(store: dict[str, Any], delegation_id: str) -> dict[str, Any]:
    rec = get_delegation(store, delegation_id)
    if rec is None:
        raise DelegationError(
            f"委任ID {delegation_id} は登録されていません。先に add-delegation してください。"
        )
    return rec


def require_template(store: dict[str, Any], template_id: str) -> dict[str, Any]:
    rec = get_template(store, template_id)
    if rec is None:
        raise DelegationError(
            f"テンプレートID {template_id} は登録されていません。先に add-template してください。"
        )
    return rec


def require_draft(store: dict[str, Any], draft_id: str) -> dict[str, Any]:
    rec = get_draft(store, draft_id)
    if rec is None:
        raise DelegationError(f"送信案ID {draft_id} は登録されていません。")
    return rec


def parse_json_dict(label: str, raw: str | None) -> dict[str, Any]:
    if raw is None or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise DelegationError(f"{label} は JSON オブジェクトで指定してください: {e}") from e
    if not isinstance(data, dict):
        raise DelegationError(f"{label} は JSON オブジェクト（辞書）で指定してください")
    return data


def parse_variables(raw: str | None) -> dict[str, str]:
    data = parse_json_dict("variables", raw)
    out: dict[str, str] = {}
    for key, val in data.items():
        name = str(key).strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise DelegationError(f"変数名 {name} の形が不正です")
        if val is None or isinstance(val, (dict, list)):
            raise DelegationError(f"変数 {name} の値は文字列で指定してください")
        out[name] = str(val)
    return out


def parse_usage_conditions(raw: str | None) -> dict[str, list[str]]:
    data = parse_json_dict("allowed-vars", raw)
    out: dict[str, list[str]] = {}
    for key, val in data.items():
        name = str(key).strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise DelegationError(f"変数名 {name} の形が不正です")
        if not isinstance(val, list) or not val:
            raise DelegationError(
                f"変数 {name} の使用条件は、空でない文字列の配列で指定してください"
            )
        cleaned: list[str] = []
        for item in val:
            if item is None or isinstance(item, (dict, list)):
                raise DelegationError(f"変数 {name} の使用条件の要素は文字列で指定してください")
            cleaned.append(str(item))
        out[name] = cleaned
    return out


def parse_scope_list(values: list[str] | None) -> list[str]:
    if not values:
        raise DelegationError("内容の範囲（--scope）を1つ以上指定してください")
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        token = require_id("内容の範囲", raw)
        if token not in seen:
            out.append(token)
            seen.add(token)
    return out


def append_audit(
    store: dict[str, Any],
    *,
    actor_id: str,
    action: str,
    delegation_id: str = "",
    template_id: str = "",
    draft_id: str = "",
    from_status: str = "",
    to_status: str = "",
    decision: str = "",
) -> None:
    """監査ログへ1行追記する。既存行は書き換えない。識別子のみ。"""
    entry = {
        "at": now_jst(),
        "actor_id": actor_id,
        "action": action,
        "delegation_id": delegation_id,
        "template_id": template_id,
        "draft_id": draft_id,
        "from_status": from_status,
        "to_status": to_status,
        "decision": decision,
    }
    store.setdefault("audit", []).append(ordered(AUDIT_FIELDS, entry))


def render_body(body: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            missing.append(name)
            return match.group(0)
        return variables[name]

    return PLACEHOLDER_RE.sub(repl, body), missing


def classify_variables(
    template: dict[str, Any],
    variables: dict[str, str],
) -> tuple[str, list[str]]:
    """変数と使用条件の関係を分類する。ok / out / ambiguous。"""
    conditions = template.get("usage_conditions") or {}
    if not isinstance(conditions, dict):
        return "ambiguous", ["使用条件の形が不正で判定が曖昧です"]
    placeholders = set(PLACEHOLDER_RE.findall(template.get("body") or ""))
    reasons: list[str] = []
    kind = "ok"

    def bump(new_kind: str, message: str) -> None:
        nonlocal kind
        reasons.append(message)
        order = {"ok": 0, "out": 1, "ambiguous": 2}
        if order[new_kind] > order[kind]:
            kind = new_kind

    for name in placeholders:
        if name not in variables:
            bump("ambiguous", f"変数 {name} が未指定のため判定が曖昧です")
            continue
        if name not in conditions:
            bump("ambiguous", f"変数 {name} の使用条件が無いため判定が曖昧です")
            continue
        allowed = conditions[name]
        if not isinstance(allowed, list):
            bump("ambiguous", f"変数 {name} の使用条件の形が不正です")
            continue
        if variables[name] not in allowed:
            bump("out", f"変数 {name} の値が使用条件の範囲外です")

    for name, value in variables.items():
        if name in placeholders:
            continue
        if name not in conditions:
            bump("ambiguous", f"未知の変数 {name} があり判定が曖昧です")
        elif not isinstance(conditions[name], list):
            bump("ambiguous", f"変数 {name} の使用条件の形が不正です")
        elif value not in conditions[name]:
            bump("out", f"変数 {name} の値が使用条件の範囲外です")

    return kind, reasons


def evaluate(
    store: dict[str, Any],
    *,
    delegation_id: str,
    template_id: str | None,
    scope: str | None,
    counterpart_id: str | None,
    variables: dict[str, str],
    at: str,
) -> dict[str, Any]:
    """委任・テンプレート・変数から、確認省略の可否と送信可否を判定する。"""
    reasons: list[str] = []
    decision = DECISION_SKIP
    send_ok = True
    finalize_ok = True
    skip_confirm_ok = True

    def downgrade(new_decision: str) -> None:
        nonlocal decision, send_ok, finalize_ok, skip_confirm_ok
        skip_confirm_ok = False
        finalize_ok = False
        send_ok = False
        rank = {
            DECISION_SKIP: 0,
            DECISION_CONFIRM: 1,
            DECISION_CANNOT_FINALIZE: 2,
        }
        if rank[new_decision] > rank[decision]:
            decision = new_decision

    delegation = get_delegation(store, delegation_id)
    if delegation is None:
        downgrade(DECISION_CANNOT_FINALIZE)
        reasons.append(f"委任ID {delegation_id} は登録されていません")
        return _eval_result(
            decision,
            send_ok,
            finalize_ok,
            skip_confirm_ok,
            reasons,
            None,
            None,
            scope,
            counterpart_id,
            at,
        )

    share_allowed = bool(delegation.get("share_allowed"))
    send_allowed = bool(delegation.get("send_allowed"))
    scopes = list(delegation.get("scope") or [])

    if not in_period(str(delegation.get("valid_from") or ""), str(delegation.get("valid_until") or ""), at):
        downgrade(DECISION_CONFIRM)
        reasons.append("委任の有効期間外です")

    if not scope:
        downgrade(DECISION_CONFIRM)
        reasons.append("内容の範囲が指定されていないため判定が曖昧です")
        in_scope = False
    else:
        in_scope = scope in scopes
        if not in_scope:
            downgrade(DECISION_CONFIRM)
            reasons.append("内容の範囲外です")

    if counterpart_id and counterpart_id != delegation.get("counterpart_id"):
        downgrade(DECISION_CONFIRM)
        reasons.append("相手の識別子が委任と一致しません")

    if not send_allowed:
        downgrade(DECISION_CANNOT_FINALIZE)
        if share_allowed:
            reasons.append("情報共有の許可だけでは送信できません。送信権限が必要です")
        else:
            reasons.append("送信権限がありません")

    template = None
    if template_id:
        template = get_template(store, template_id)
        if template is None:
            downgrade(DECISION_CANNOT_FINALIZE)
            reasons.append(f"テンプレートID {template_id} は登録されていません")
        else:
            bound = (template.get("delegation_id") or "").strip()
            if bound and bound != delegation_id:
                downgrade(DECISION_CANNOT_FINALIZE)
                reasons.append("テンプレートが別の委任に紐付いています")
            if not template.get("approved"):
                downgrade(DECISION_CANNOT_FINALIZE)
                reasons.append("テンプレートが未承認のため、送信案を自動では確定できません")
            var_kind, var_reasons = classify_variables(template, variables)
            if var_kind == "ambiguous":
                downgrade(DECISION_CONFIRM)
                reasons.extend(var_reasons)
            elif var_kind == "out":
                downgrade(DECISION_CONFIRM)
                reasons.extend(var_reasons)
    else:
        # 承認済みテンプレートが無ければ、文面の確認省略はできない。
        downgrade(DECISION_CONFIRM)
        reasons.append("テンプレートが指定されていないため、確認省略はできません")

    if decision == DECISION_SKIP:
        skip_confirm_ok = True
        finalize_ok = True
        send_ok = True
    else:
        skip_confirm_ok = False

    return _eval_result(
        decision,
        send_ok,
        finalize_ok,
        skip_confirm_ok,
        reasons,
        delegation,
        template,
        scope,
        counterpart_id or str(delegation.get("counterpart_id") or ""),
        at,
    )


def _eval_result(
    decision: str,
    send_ok: bool,
    finalize_ok: bool,
    skip_confirm_ok: bool,
    reasons: list[str],
    delegation: dict[str, Any] | None,
    template: dict[str, Any] | None,
    scope: str | None,
    counterpart_id: str | None,
    at: str,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "send_ok": send_ok,
        "finalize_ok": finalize_ok,
        "skip_confirm_ok": skip_confirm_ok,
        "reasons": reasons,
        "share_allowed": bool(delegation.get("share_allowed")) if delegation else False,
        "send_allowed": bool(delegation.get("send_allowed")) if delegation else False,
        "delegation_id": (delegation or {}).get("delegation_id", ""),
        "template_id": (template or {}).get("template_id", ""),
        "template_approved": bool((template or {}).get("approved")) if template else False,
        "scope": scope or "",
        "counterpart_id": counterpart_id or "",
        "at": at,
    }


def retry_decision(rec: dict[str, Any]) -> dict[str, Any]:
    """外部送信の再実行をしてよいか（安全側。促さない）。"""
    base = {
        "draft_id": rec.get("draft_id"),
        "handoff_status": rec.get("handoff_status"),
        "outcome_unknown": bool(rec.get("outcome_unknown")),
        "retry_ok": False,
        "prompt_retry": False,
        "retry_send": False,
    }
    if rec.get("outcome_unknown"):
        base.update(
            {
                "message": (
                    "実行結果不明（要照合）です。"
                    "外部送信は再実行しません。先に結果を照合してください。"
                    "無条件では再送しません。"
                ),
                "allowed": ["結果を照合する"],
                "forbidden": [
                    "外部送信の再実行",
                    "引き渡しのやり直し",
                ],
            }
        )
        return base
    status = rec.get("handoff_status")
    if status == HAND_UNCONFIRMED:
        base["message"] = (
            "渡し済み（未確認）です。確認が来るまで送信完了にせず、渡し直しもしません。"
            "再送は促しません。"
        )
        return base
    if status in (HAND_SENT, HAND_REPLIED):
        base["message"] = (
            f"引き渡し・送信状態が「{status}」のため、外部送信は再実行しません。"
        )
        return base
    base["message"] = "このコマンドは再実行を促しません。"
    return base


def findings_for_store(store: dict[str, Any]) -> list[dict[str, str]]:
    """整合性チェック。期間切れ送信、権限不足、未承認確定、確認欠落などを検出する。"""
    issues: list[dict[str, str]] = []
    delegations: dict[str, Any] = store.get("delegations") or {}
    templates: dict[str, Any] = store.get("templates") or {}
    drafts: dict[str, Any] = store.get("drafts") or {}
    audit = store.get("audit") or []

    def add(target: str, code: str, message: str) -> None:
        issues.append({"対象": target, "種別": code, "内容": message})

    for did, rec in delegations.items():
        if not isinstance(rec, dict):
            add(str(did), "形", "委任レコードが辞書ではありません")
            continue
        if rec.get("share_allowed") and rec.get("send_allowed") is True:
            pass
        # フラグの型だけ見る
        for flag in ("share_allowed", "send_allowed"):
            if flag in rec and not isinstance(rec.get(flag), bool):
                add(did, "形", f"{flag} が真偽値ではありません")

    for tid, rec in templates.items():
        if not isinstance(rec, dict):
            add(str(tid), "形", "テンプレートレコードが辞書ではありません")
            continue
        bound = (rec.get("delegation_id") or "").strip()
        if bound and bound not in delegations:
            add(tid, "対応", f"紐付く委任ID {bound} が委任ストアにありません")

    for sid, rec in drafts.items():
        if not isinstance(rec, dict):
            add(str(sid), "形", "送信案レコードが辞書ではありません")
            continue
        hstatus = rec.get("handoff_status")
        if hstatus in FORBIDDEN_IN_HANDOFF:
            add(
                sid,
                "系統の混同",
                f"引き渡し・送信状態に別系統の語が入っています: {hstatus}",
            )
        elif hstatus not in HANDOFF_STATUSES:
            add(sid, "未知", f"未知の引き渡し・送信状態です: {hstatus}")

        decision = rec.get("decision")
        if decision not in DECISIONS and decision not in (None, ""):
            add(sid, "未知", f"未知の判定です: {decision}")

        did = rec.get("delegation_id")
        delegation = delegations.get(did) if did else None
        tid = rec.get("template_id")
        template = templates.get(tid) if tid else None

        if rec.get("finalized") and isinstance(template, dict) and not template.get("approved"):
            add(
                sid,
                "未承認テンプレ",
                "未承認のテンプレートで送信案が確定されています",
            )
        if rec.get("finalized") and template is None:
            add(sid, "未承認テンプレ", "テンプレートが無いのに送信案が確定されています")

        if hstatus in (HAND_UNCONFIRMED, HAND_SENT, HAND_REPLIED):
            if isinstance(delegation, dict) and not delegation.get("send_allowed"):
                add(
                    sid,
                    "送信権限",
                    "情報共有の許可だけで引き渡しまたは送信が行われています",
                )
            if isinstance(delegation, dict):
                handed_at = (rec.get("handed_off_at") or "").strip()
                when = handed_at or None
                if when:
                    try:
                        if not in_period(
                            str(delegation.get("valid_from") or ""),
                            str(delegation.get("valid_until") or ""),
                            when,
                        ):
                            add(
                                sid,
                                "期間切れ",
                                "期間切れの委任で引き渡しまたは送信が行われています",
                            )
                    except DelegationError:
                        add(sid, "期間切れ", "引き渡し時刻または委任の期間が判定できません")

        conf_ref = (rec.get("confirmation_ref") or "").strip()
        conf_at = (rec.get("confirmation_received_at") or "").strip()
        if hstatus in (HAND_SENT, HAND_REPLIED):
            if not conf_ref or not conf_at:
                add(
                    sid,
                    "確認欠落",
                    "送信完了（または返事あり）なのに、Bot間の完了確認が記録されていません",
                )

        reply = rec.get("reply_track")
        if reply and hstatus not in (HAND_SENT, HAND_REPLIED):
            add(sid, "返事待ちの時期", "返事待ちの追跡行がありますが、送信完了の前です")
        if hstatus == HAND_REPLIED and not reply:
            add(sid, "返事待ちの欠落", "返事ありなのに、追跡行がありません")

        if rec.get("outcome_unknown") and rec.get("retry_send") is True:
            add(
                sid,
                "再実行フラグ",
                "実行結果不明（要照合）なのに、再実行フラグが立っています",
            )

        if did and did not in delegations:
            add(sid, "対応", f"親の委任ID {did} が委任ストアにありません")
        if tid and tid not in templates:
            add(sid, "対応", f"テンプレートID {tid} がテンプレートストアにありません")

    if not isinstance(audit, list):
        add("audit", "形", "監査ログが配列ではありません")
    else:
        for i, entry in enumerate(audit):
            if not isinstance(entry, dict):
                add(f"audit[{i}]", "形", "監査ログの行が辞書ではありません")

    return issues


def print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def actor_of(args: argparse.Namespace) -> str:
    return require_id("actor", getattr(args, "actor", None) or "operator")


def at_of(args: argparse.Namespace, default: str | None = None) -> str:
    raw = getattr(args, "at", None)
    if raw:
        return require_jst("at", raw)
    return default or now_jst()


# ---------------------------------------------------------------------------
# コマンド
# ---------------------------------------------------------------------------

def cmd_add_delegation(args: argparse.Namespace) -> int:
    delegation_id = require_id("delegation-id", args.delegation_id)
    counterpart_id = require_id("counterpart-id", args.counterpart_id)
    scope = parse_scope_list(args.scope)
    valid_from = require_jst("valid-from", args.valid_from)
    valid_until = require_jst("valid-until", args.valid_until)
    if parse_jst("valid-from", valid_from) > parse_jst("valid-until", valid_until):
        raise DelegationError("有効期間の開始が終了より後になっています")
    share_allowed = bool(args.share_allowed)
    send_allowed = bool(args.send_allowed)
    actor = actor_of(args)
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        existing = get_delegation(store, delegation_id)
        if existing is not None:
            return None, {
                "ok": True,
                "created": False,
                "delegation_id": delegation_id,
                "record": ordered(DELEGATION_FIELDS, existing),
            }
        rec = empty_delegation(
            delegation_id,
            counterpart_id,
            scope,
            valid_from,
            valid_until,
            share_allowed,
            send_allowed,
        )
        store["delegations"][delegation_id] = rec
        append_audit(
            store,
            actor_id=actor,
            action="add-delegation",
            delegation_id=delegation_id,
        )
        return store, {
            "ok": True,
            "created": True,
            "delegation_id": delegation_id,
            "record": ordered(DELEGATION_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_add_template(args: argparse.Namespace) -> int:
    template_id = require_id("template-id", args.template_id)
    delegation_id = require_id("delegation-id", args.delegation_id) if args.delegation_id else ""
    body = args.body
    if body is None or not str(body):
        raise DelegationError("テンプレート本文（--body）を指定してください")
    usage = parse_usage_conditions(args.allowed_vars)
    approved = bool(args.approve)
    actor = actor_of(args)
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        if delegation_id:
            require_delegation(store, delegation_id)
        existing = get_template(store, template_id)
        if existing is not None:
            return None, {
                "ok": True,
                "created": False,
                "template_id": template_id,
                "approved": bool(existing.get("approved")),
                "record": ordered(TEMPLATE_FIELDS, existing),
            }
        rec = empty_template(template_id, delegation_id, str(body), usage, approved)
        store["templates"][template_id] = rec
        append_audit(
            store,
            actor_id=actor,
            action="add-template",
            delegation_id=delegation_id,
            template_id=template_id,
            to_status="承認済み" if approved else "未承認",
        )
        return store, {
            "ok": True,
            "created": True,
            "template_id": template_id,
            "approved": approved,
            "record": ordered(TEMPLATE_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_approve_template(args: argparse.Namespace) -> int:
    template_id = require_id("template-id", args.template_id)
    actor = actor_of(args)
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_template(store, template_id)
        if rec.get("approved"):
            return None, {
                "ok": True,
                "changed": False,
                "template_id": template_id,
                "approved": True,
                "record": ordered(TEMPLATE_FIELDS, rec),
            }
        rec["approved"] = True
        rec["updated_at"] = now_jst()
        append_audit(
            store,
            actor_id=actor,
            action="approve-template",
            delegation_id=str(rec.get("delegation_id") or ""),
            template_id=template_id,
            from_status="未承認",
            to_status="承認済み",
        )
        return store, {
            "ok": True,
            "changed": True,
            "template_id": template_id,
            "approved": True,
            "record": ordered(TEMPLATE_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    delegation_id = require_id("delegation-id", args.delegation_id)
    template_id = require_id("template-id", args.template_id) if args.template_id else None
    scope = require_id("scope", args.scope) if args.scope else None
    counterpart_id = (
        require_id("counterpart-id", args.counterpart_id) if args.counterpart_id else None
    )
    variables = parse_variables(args.variables)
    at = at_of(args)
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        result = evaluate(
            store,
            delegation_id=delegation_id,
            template_id=template_id,
            scope=scope,
            counterpart_id=counterpart_id,
            variables=variables,
            at=at,
        )
        return None, result

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    draft_id = require_id("draft-id", args.draft_id)
    delegation_id = require_id("delegation-id", args.delegation_id)
    template_id = require_id("template-id", args.template_id)
    scope = require_id("scope", args.scope)
    counterpart_id = (
        require_id("counterpart-id", args.counterpart_id) if args.counterpart_id else None
    )
    variables = parse_variables(args.variables)
    at = at_of(args)
    actor = actor_of(args)
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        existing = get_draft(store, draft_id)
        if existing is not None:
            return None, {
                "ok": True,
                "created": False,
                "draft_id": draft_id,
                "decision": existing.get("decision"),
                "finalized": bool(existing.get("finalized")),
                "record": ordered(DRAFT_FIELDS, existing),
            }
        delegation = require_delegation(store, delegation_id)
        template = require_template(store, template_id)
        used_counterpart = counterpart_id or str(delegation.get("counterpart_id") or "")
        ev = evaluate(
            store,
            delegation_id=delegation_id,
            template_id=template_id,
            scope=scope,
            counterpart_id=used_counterpart,
            variables=variables,
            at=at,
        )
        body, _missing = render_body(str(template.get("body") or ""), variables)
        finalized = ev["decision"] == DECISION_SKIP and ev["finalize_ok"]
        rec = empty_draft(
            draft_id,
            delegation_id,
            template_id,
            used_counterpart,
            scope,
            variables,
            body,
            ev["decision"],
            finalized,
        )
        if finalized:
            rec["confirmed_at"] = ""
            rec["confirmed_by"] = ""
        store["drafts"][draft_id] = rec
        append_audit(
            store,
            actor_id=actor,
            action="draft",
            delegation_id=delegation_id,
            template_id=template_id,
            draft_id=draft_id,
            to_status=HAND_BEFORE,
            decision=ev["decision"],
        )
        return store, {
            "ok": True,
            "created": True,
            "draft_id": draft_id,
            "decision": ev["decision"],
            "finalized": finalized,
            "send_ok": ev["send_ok"],
            "finalize_ok": ev["finalize_ok"],
            "skip_confirm_ok": ev["skip_confirm_ok"],
            "reasons": ev["reasons"],
            "record": ordered(DRAFT_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    draft_id = require_id("draft-id", args.draft_id)
    confirmed = bool(args.confirmed)
    at = at_of(args)
    actor = actor_of(args)
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_draft(store, draft_id)
        if rec.get("finalized"):
            return None, {
                "ok": True,
                "changed": False,
                "draft_id": draft_id,
                "decision": rec.get("decision"),
                "finalized": True,
                "note": "既に確定済みです。",
                "record": ordered(DRAFT_FIELDS, rec),
            }
        ev = evaluate(
            store,
            delegation_id=str(rec.get("delegation_id") or ""),
            template_id=str(rec.get("template_id") or ""),
            scope=str(rec.get("scope") or "") or None,
            counterpart_id=str(rec.get("counterpart_id") or "") or None,
            variables=dict(rec.get("variables") or {}),
            at=at,
        )
        rec["decision"] = ev["decision"]
        rec["updated_at"] = now_jst()

        if ev["decision"] == DECISION_CANNOT_FINALIZE:
            raise DelegationError(
                "この送信案は自動では確定できません。"
                + (" " + " / ".join(ev["reasons"]) if ev["reasons"] else "")
            )
        if ev["decision"] == DECISION_SKIP and ev["finalize_ok"]:
            rec["finalized"] = True
        elif ev["decision"] == DECISION_CONFIRM and confirmed and ev["send_allowed"]:
            # 文面の確認を人が行った場合のみ。送信権限が無いときは確定しない。
            # 期間外でも文面確定はできるが、引き渡し時に期間を再判定する。
            rec["finalized"] = True
            rec["confirmed_at"] = at
            rec["confirmed_by"] = actor
        elif ev["decision"] == DECISION_CONFIRM and not confirmed:
            raise DelegationError(
                "判定が要確認のため、自動では確定できません。"
                "確認済み（--confirmed）の指定が必要です。"
                + (" " + " / ".join(ev["reasons"]) if ev["reasons"] else "")
            )
        else:
            raise DelegationError(
                "この送信案は確定できません。"
                + (" " + " / ".join(ev["reasons"]) if ev["reasons"] else "")
            )

        append_audit(
            store,
            actor_id=actor,
            action="finalize",
            delegation_id=str(rec.get("delegation_id") or ""),
            template_id=str(rec.get("template_id") or ""),
            draft_id=draft_id,
            decision=ev["decision"],
            to_status="確定済み",
        )
        return store, {
            "ok": True,
            "changed": True,
            "draft_id": draft_id,
            "decision": ev["decision"],
            "finalized": True,
            "reasons": ev["reasons"],
            "record": ordered(DRAFT_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    draft_id = require_id("draft-id", args.draft_id)
    at = at_of(args)
    actor = actor_of(args)
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_draft(store, draft_id)
        current = rec.get("handoff_status")
        if rec.get("outcome_unknown"):
            raise DelegationError(
                "実行結果不明（要照合）のため、引き渡しのやり直しはしません。"
            )
        if current == HAND_UNCONFIRMED:
            return None, {
                "ok": True,
                "changed": False,
                "draft_id": draft_id,
                "handoff_status": current,
                "note": "既に渡し済み（未確認）です。確認が来るまで待ち、渡し直しはしません。",
                "record": ordered(DRAFT_FIELDS, rec),
            }
        if current in (HAND_SENT, HAND_REPLIED):
            raise DelegationError(
                f"引き渡し・送信状態が「{current}」のため、渡し直しはできません。"
            )
        if current != HAND_BEFORE:
            raise DelegationError(
                f"引き渡し・送信状態を「{current}」から渡し済み（未確認）へは遷移できません。"
            )
        if not rec.get("finalized"):
            raise DelegationError(
                "送信案が未確定のため、引き渡しできません。"
                "先に確定するか、承認済みテンプレートで確認省略可としてください。"
            )
        ev = evaluate(
            store,
            delegation_id=str(rec.get("delegation_id") or ""),
            template_id=str(rec.get("template_id") or ""),
            scope=str(rec.get("scope") or "") or None,
            counterpart_id=str(rec.get("counterpart_id") or "") or None,
            variables=dict(rec.get("variables") or {}),
            at=at,
        )
        if not ev["send_allowed"]:
            raise DelegationError(
                "情報共有の許可だけでは送信できません。送信権限が必要です。"
            )
        # 期間外・範囲外は引き渡ししない（要確認として止める）
        period_or_scope = [
            r
            for r in ev["reasons"]
            if "有効期間外" in r or "範囲外" in r or "範囲が指定されていない" in r
        ]
        if period_or_scope:
            raise DelegationError(
                "委任の期間または範囲を満たさないため、引き渡しできません。"
                "要確認です。"
                + " "
                + " / ".join(period_or_scope)
            )
        rec["handoff_status"] = HAND_UNCONFIRMED
        rec["handed_off_at"] = at
        rec["updated_at"] = now_jst()
        append_audit(
            store,
            actor_id=actor,
            action="handoff",
            delegation_id=str(rec.get("delegation_id") or ""),
            template_id=str(rec.get("template_id") or ""),
            draft_id=draft_id,
            from_status=str(current),
            to_status=HAND_UNCONFIRMED,
            decision=ev["decision"],
        )
        return store, {
            "ok": True,
            "changed": True,
            "draft_id": draft_id,
            "handoff_status": rec["handoff_status"],
            "note": "渡し済み（未確認）にしました。相手側の送信確認が来るまで送信完了にはしません。",
            "record": ordered(DRAFT_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_confirm_sent(args: argparse.Namespace) -> int:
    draft_id = require_id("draft-id", args.draft_id)
    confirmation_ref = (args.confirmation_ref or "").strip()
    if not confirmation_ref:
        raise DelegationError(
            "送信完了にするには、相手側からの確認参照（--confirmation-ref）が必要です。"
            "確認が無いまま送信完了にはできません。"
        )
    # 確認参照も識別子に限る（固有名や本文を残さない）
    confirmation_ref = require_id("confirmation-ref", confirmation_ref)
    at = at_of(args)
    actor = actor_of(args)
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_draft(store, draft_id)
        current = rec.get("handoff_status")
        if rec.get("outcome_unknown"):
            raise DelegationError(
                "実行結果不明（要照合）のため、送信完了には進めません。先に結果を照合してください。"
            )
        if current == HAND_SENT or current == HAND_REPLIED:
            return None, {
                "ok": True,
                "changed": False,
                "draft_id": draft_id,
                "handoff_status": current,
                "note": "既に送信完了（または返事あり）です。追加の確認では状態を変えません。",
                "record": ordered(DRAFT_FIELDS, rec),
            }
        if current != HAND_UNCONFIRMED:
            raise DelegationError(
                f"引き渡し・送信状態が「{current}」のため、送信完了にできません。"
                "先に handoff で渡し済み（未確認）にし、相手側の確認を受け取ってからにしてください。"
                "確認無しで送信完了にはできません。"
            )
        rec["handoff_status"] = HAND_SENT
        rec["confirmation_ref"] = confirmation_ref
        rec["confirmation_received_at"] = at
        rec["updated_at"] = now_jst()
        append_audit(
            store,
            actor_id=actor,
            action="confirm-sent",
            delegation_id=str(rec.get("delegation_id") or ""),
            template_id=str(rec.get("template_id") or ""),
            draft_id=draft_id,
            from_status=str(current),
            to_status=HAND_SENT,
        )
        return store, {
            "ok": True,
            "changed": True,
            "draft_id": draft_id,
            "handoff_status": rec["handoff_status"],
            "record": ordered(DRAFT_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_mark_reply(args: argparse.Namespace) -> int:
    draft_id = require_id("draft-id", args.draft_id)
    actor = actor_of(args)
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_draft(store, draft_id)
        current = rec.get("handoff_status")
        if current == HAND_REPLIED:
            if not rec.get("reply_track"):
                rec["reply_track"] = {
                    "status": REPLY_WAITING,
                    "created_at": now_jst(),
                    "note": "送信完了後の追跡行",
                }
                rec["updated_at"] = now_jst()
                append_audit(
                    store,
                    actor_id=actor,
                    action="mark-reply",
                    delegation_id=str(rec.get("delegation_id") or ""),
                    template_id=str(rec.get("template_id") or ""),
                    draft_id=draft_id,
                    from_status=str(current),
                    to_status=HAND_REPLIED,
                )
                return store, {
                    "ok": True,
                    "changed": True,
                    "draft_id": draft_id,
                    "handoff_status": current,
                    "record": ordered(DRAFT_FIELDS, rec),
                }
            return None, {
                "ok": True,
                "changed": False,
                "draft_id": draft_id,
                "handoff_status": current,
                "note": "既に返事ありです。",
                "record": ordered(DRAFT_FIELDS, rec),
            }
        if current != HAND_SENT:
            raise DelegationError(
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
        append_audit(
            store,
            actor_id=actor,
            action="mark-reply",
            delegation_id=str(rec.get("delegation_id") or ""),
            template_id=str(rec.get("template_id") or ""),
            draft_id=draft_id,
            from_status=str(current),
            to_status=HAND_REPLIED,
        )
        return store, {
            "ok": True,
            "changed": True,
            "draft_id": draft_id,
            "handoff_status": rec["handoff_status"],
            "record": ordered(DRAFT_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_mark_outcome_unknown(args: argparse.Namespace) -> int:
    draft_id = require_id("draft-id", args.draft_id)
    reason = (args.reason or "").strip() or "実行結果を確認できない"
    actor = actor_of(args)
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_draft(store, draft_id)
        rec["outcome_unknown"] = True
        rec["outcome_unknown_reason"] = reason
        rec["retry_send"] = False
        rec["updated_at"] = now_jst()
        append_audit(
            store,
            actor_id=actor,
            action="mark-outcome-unknown",
            delegation_id=str(rec.get("delegation_id") or ""),
            template_id=str(rec.get("template_id") or ""),
            draft_id=draft_id,
            to_status=OUTCOME_UNKNOWN,
        )
        return store, {
            "ok": True,
            "draft_id": draft_id,
            "outcome_unknown": True,
            "retry_send": False,
            "prompt_retry": False,
            "note": "実行結果不明（要照合）にしました。外部送信は再実行しません。",
            "record": ordered(DRAFT_FIELDS, rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_can_retry_send(args: argparse.Namespace) -> int:
    draft_id = require_id("draft-id", args.draft_id)
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rec = require_draft(store, draft_id)
        return None, retry_decision(rec)

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        issues = findings_for_store(store)
        return None, {
            "委任件数": len(store.get("delegations") or {}),
            "テンプレート件数": len(store.get("templates") or {}),
            "送信案件数": len(store.get("drafts") or {}),
            "監査件数": len(store.get("audit") or []),
            "問題件数": len(issues),
            "問題": issues,
        }

    result = with_store(path, mut)
    if args.json:
        print_json(result)
    else:
        print(f"委任: {result['委任件数']}件")
        print(f"テンプレート: {result['テンプレート件数']}件")
        print(f"送信案: {result['送信案件数']}件")
        print(f"監査: {result['監査件数']}件")
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
        if args.delegation_id:
            rec = require_delegation(store, require_id("delegation-id", args.delegation_id))
            return None, {"delegation": ordered(DELEGATION_FIELDS, rec)}
        if args.template_id:
            rec = require_template(store, require_id("template-id", args.template_id))
            return None, {"template": ordered(TEMPLATE_FIELDS, rec)}
        rec = require_draft(store, require_id("draft-id", args.draft_id))
        return None, {"draft": ordered(DRAFT_FIELDS, rec)}

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    path = Path(args.file)

    def mut(store: dict[str, Any]):
        rows = [ordered(AUDIT_FIELDS, e) for e in (store.get("audit") or []) if isinstance(e, dict)]
        return None, {"件数": len(rows), "audit": rows}

    result = with_store(path, mut)
    print_json(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="delegation.py",
        description=(
            "予定ばん 第2段階試作。外部送信の委任・テンプレート・送信案と"
            "引き渡し・送信状態を扱う。実送信はしない。--file 必須。"
        ),
    )
    p.add_argument(
        "--file",
        required=True,
        help="対象ストア（JSON辞書）。検証は一時ファイルを指定すること。既定の本番パスは持たない",
    )
    p.add_argument(
        "--actor",
        default="operator",
        help="操作した主体の識別子（役割名など。固有名は使わない）",
    )
    sub = p.add_subparsers(dest="command", required=True)

    dp = sub.add_parser("add-delegation", help="委任を登録（委任IDで冪等）")
    dp.add_argument("--delegation-id", required=True, help="委任の識別子")
    dp.add_argument("--counterpart-id", required=True, help="相手の識別子（役割名など）")
    dp.add_argument(
        "--scope",
        action="append",
        required=True,
        help="内容の範囲（繰り返し指定可）",
    )
    dp.add_argument("--valid-from", required=True, help="有効期間の開始（例: 2026-11-01 00:00 JST）")
    dp.add_argument("--valid-until", required=True, help="有効期間の終了（例: 2026-11-30 23:59 JST）")
    dp.add_argument(
        "--share-allowed",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="情報共有の許可。これだけでは送信できない",
    )
    dp.add_argument(
        "--send-allowed",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="送信権限。情報共有の許可とは別フィールド",
    )
    dp.set_defaults(func=cmd_add_delegation)

    tp = sub.add_parser("add-template", help="テンプレートを登録（テンプレートIDで冪等）")
    tp.add_argument("--template-id", required=True)
    tp.add_argument("--delegation-id", default=None, help="紐付ける委任の識別子（任意）")
    tp.add_argument("--body", required=True, help="本文。{name} を変数にできる")
    tp.add_argument(
        "--allowed-vars",
        default=None,
        help='使用条件（JSON辞書。例: {"date":["2026-11-18"]}）',
    )
    tp.add_argument("--approve", action="store_true", help="登録と同時に承認済みにする")
    tp.set_defaults(func=cmd_add_template)

    ap = sub.add_parser("approve-template", help="テンプレートを承認済みにする")
    ap.add_argument("--template-id", required=True)
    ap.set_defaults(func=cmd_approve_template)

    ep = sub.add_parser("evaluate", help="委任とテンプレートを判定する（読むだけ）")
    ep.add_argument("--delegation-id", required=True)
    ep.add_argument("--template-id", default=None)
    ep.add_argument("--scope", default=None)
    ep.add_argument("--counterpart-id", default=None)
    ep.add_argument("--variables", default=None, help="変数の JSON 辞書")
    ep.add_argument("--at", default=None, help="判定時刻（例: 2026-11-17 10:00 JST）")
    ep.set_defaults(func=cmd_evaluate)

    rp = sub.add_parser("draft", help="変数を埋めた送信案を作る（送信案IDで冪等）。未承認なら確定しない")
    rp.add_argument("--draft-id", required=True)
    rp.add_argument("--delegation-id", required=True)
    rp.add_argument("--template-id", required=True)
    rp.add_argument("--scope", required=True)
    rp.add_argument("--counterpart-id", default=None)
    rp.add_argument("--variables", default=None)
    rp.add_argument("--at", default=None)
    rp.set_defaults(func=cmd_draft)

    fp = sub.add_parser(
        "finalize",
        help="送信案を確定する。確認省略可なら自動可。要確認は --confirmed が必要。未承認は不可",
    )
    fp.add_argument("--draft-id", required=True)
    fp.add_argument(
        "--confirmed",
        action="store_true",
        help="文面を人が確認済みであるときに付ける。未承認テンプレや送信権限なしでは使えない",
    )
    fp.add_argument("--at", default=None)
    fp.set_defaults(func=cmd_finalize)

    hp = sub.add_parser("handoff", help="外部へ引き渡す（渡し済み・未確認）。渡し直しはしない。実送信はしない")
    hp.add_argument("--draft-id", required=True)
    hp.add_argument("--at", default=None)
    hp.set_defaults(func=cmd_handoff)

    kp = sub.add_parser(
        "confirm-sent",
        help="相手側の完了確認を記録し、送信完了にする。確認参照が必須",
    )
    kp.add_argument("--draft-id", required=True)
    kp.add_argument("--confirmation-ref", required=True, help="相手側の確認参照（識別子）")
    kp.add_argument("--at", default=None)
    kp.set_defaults(func=cmd_confirm_sent)

    yp = sub.add_parser("mark-reply", help="送信完了後に返事ありとし、返事待ちの追跡行を作る")
    yp.add_argument("--draft-id", required=True)
    yp.set_defaults(func=cmd_mark_reply)

    up = sub.add_parser(
        "mark-outcome-unknown",
        help="実行結果不明（要照合）にする。再送は促さない",
    )
    up.add_argument("--draft-id", required=True)
    up.add_argument("--reason", default=None)
    up.set_defaults(func=cmd_mark_outcome_unknown)

    cp = sub.add_parser(
        "can-retry-send",
        help="外部送信を再実行してよいか確認する（読むだけ。不明時は促さない）",
    )
    cp.add_argument("--draft-id", required=True)
    cp.set_defaults(func=cmd_can_retry_send)

    ch = sub.add_parser("check", help="整合性チェック（読むだけ）")
    ch.add_argument("--json", action="store_true")
    ch.set_defaults(func=cmd_check)

    sh = sub.add_parser("show", help="委任・テンプレート・送信案を表示")
    g = sh.add_mutually_exclusive_group(required=True)
    g.add_argument("--delegation-id", default=None)
    g.add_argument("--template-id", default=None)
    g.add_argument("--draft-id", default=None)
    sh.set_defaults(func=cmd_show)

    au = sub.add_parser("audit", help="監査ログを表示（読むだけ。追記専用で消しはしない）")
    au.set_defaults(func=cmd_audit)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DelegationError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
