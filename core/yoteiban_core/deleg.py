"""委任・共有許可・承認済み文面の純粋な検証関数。"""
from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any

try:
    from delegation import in_period as _stage2_in_period
except ImportError:  # 呼び出し元が tools/stage2 を検索パスに加えない場合
    _stage2_in_period = None

KINDS = {"cal_personal", "send", "reminder", "notion"}
STATES = ("下書き", "影の運用中", "本人の確認待ち", "有効")
SLOT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
EMAILISH = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
PHONEISH = re.compile(r"(?:\+?\d[\d\s()\-]{7,}\d)")


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo is not None and dt.utcoffset() is not None else None


def _valid_period(period: Any) -> bool:
    if not isinstance(period, dict):
        return False
    start, end = _when(period.get("from")), _when(period.get("to"))
    if start is None or end is None:
        return False
    try:
        return start <= end
    except TypeError:
        return False


def validate_delegation(d: dict[str, Any]) -> list[str]:
    """委任状に必要な範囲と値を検証し、日本語の問題一覧を返す。"""
    if not isinstance(d, dict):
        return ["委任状はオブジェクトで指定してください"]
    scope = d.get("scope")
    if not isinstance(scope, dict):
        return ["scope がありません"]
    errors: list[str] = []
    period = scope.get("period")
    if not isinstance(period, dict) or "from" not in period or "to" not in period:
        errors.append("scope.period.from と scope.period.to が必要です")
    elif not _valid_period(period):
        errors.append("scope.period はタイムゾーン付き日時で、開始から終了の順にしてください")
    count = scope.get("max_count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        errors.append("scope.max_count は正の整数が必要です")
    recipients = scope.get("recipients")
    if not isinstance(recipients, list) or not recipients or any(not isinstance(x, str) or not x.strip() for x in recipients):
        errors.append("scope.recipients に相手を1人以上指定してください")
    if not isinstance(scope.get("template_id"), str) or not scope["template_id"].strip():
        errors.append("scope.template_id が必要です")
    if not isinstance(scope.get("stop_how"), str) or not scope["stop_how"].strip():
        errors.append("scope.stop_how が必要です")
    if scope.get("kind") not in KINDS:
        errors.append("scope.kind は cal_personal・send・reminder・notion のいずれかが必要です")
    return errors


def transition(d: dict[str, Any], to_state: str, approval_id: str | None = None,
               now: str | datetime | None = None) -> dict[str, Any]:
    """決められた順序で状態を進めた新しい辞書を返す。"""
    if not isinstance(d, dict):
        raise ValueError("委任状はオブジェクトで指定してください")
    source = d.get("state", "下書き")
    if to_state == "取消":
        if source == "取消":
            raise ValueError("すでに取消済みです")
    elif source not in STATES or to_state not in STATES + ("期限切れ",):
        raise ValueError("委任状の状態が正しくありません")
    elif source == "有効" and to_state == "期限切れ":
        at = _when(now)
        end = _when((d.get("scope") or {}).get("period", {}).get("to"))
        if at is None or end is None or at < end:
            raise ValueError("有効期限を過ぎた委任だけ期限切れにできます")
    elif source in STATES and to_state in STATES and STATES.index(to_state) == STATES.index(source) + 1:
        pass
    else:
        raise ValueError("許可されていない状態遷移です")
    if to_state == "有効":
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("有効にするには本人の approval_id が必要です")
        problems = validate_delegation(d)
        if problems:
            raise ValueError("委任状が不完全です: " + "、".join(problems))
    result = copy.deepcopy(d)
    result["state"] = to_state
    if now is None:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Tokyo"))
    result["updated_at"] = now.isoformat() if isinstance(now, datetime) else now
    if to_state == "有効":
        result["approval_id"] = approval_id
    return result


def _within(period: dict[str, Any], now: str | datetime) -> bool:
    start, end, at = _when(period.get("from")), _when(period.get("to")), _when(now)
    if start is None or end is None or at is None:
        return False
    if _stage2_in_period is not None:
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo("Asia/Tokyo")
            fmt = lambda x: x.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S JST")
            return _stage2_in_period(fmt(start), fmt(end), fmt(at))
        except (ValueError, TypeError):
            return False
    try:
        return start <= at <= end
    except TypeError:
        return False


def _candidate_body(op: dict[str, Any]) -> str | None:
    body = op.get("body", op.get("text", op.get("payload")))
    if isinstance(body, dict):
        body = body.get("text", body.get("body"))
    return body if isinstance(body, str) else None


def _template_matches(template: str, candidate: str) -> bool:
    matches = list(SLOT.finditer(template))
    if not matches:
        return candidate == template
    ti = ci = 0
    for match in matches:
        literal = template[ti:match.start()]
        if not candidate.startswith(literal, ci):
            return False
        ci += len(literal)
        ti = match.end()
        next_match = SLOT.search(template, ti)
        suffix = template[ti:next_match.start() if next_match else len(template)]
        end = candidate.find(suffix, ci) if suffix else len(candidate)
        if end < ci or not candidate[ci:end].strip():
            return False
        ci = end
    return candidate[ci:] == template[ti:]


def in_scope(d: dict[str, Any], op: dict[str, Any], now: str | datetime,
             used_count: int) -> tuple[bool, str]:
    """期間・回数・相手・種別・テンプレートの全条件を確認する。"""
    if not isinstance(d, dict) or not isinstance(op, dict):
        return False, "委任または操作の形が正しくありません"
    if d.get("state") != "有効":
        return False, "委任は有効ではありません"
    scope = d.get("scope")
    if not isinstance(scope, dict):
        return False, "委任の範囲がありません"
    if not _within(scope.get("period") or {}, now):
        return False, "委任の期間外です"
    limit = scope.get("max_count")
    if isinstance(used_count, bool) or not isinstance(used_count, int) or used_count < 0 or not isinstance(limit, int) or used_count >= limit:
        return False, "委任の回数上限に達しています"
    recipient = op.get("recipient", op.get("recipient_id", op.get("target_id")))
    if recipient not in scope.get("recipients", []):
        return False, "相手が委任の一覧にありません"
    if op.get("kind") != scope.get("kind"):
        return False, "操作の種類が委任の範囲外です"
    if op.get("template_id") != scope.get("template_id"):
        return False, "承認済みの文の型と一致しません"
    template = op.get("approved_body", op.get("template_body", op.get("template")))
    if isinstance(template, dict):
        template = template.get("body", template.get("text"))
    body = _candidate_body(op)
    if not isinstance(template, str) or body is None or not _template_matches(template, body):
        return False, "文の型の空欄以外が変更されています"
    return True, "委任の範囲内です"


def validate_share_grant(g: dict[str, Any]) -> list[str]:
    """共有許可を検証する。共有から送信権限は得られない。"""
    if not isinstance(g, dict):
        return ["共有許可はオブジェクトで指定してください"]
    errors: list[str] = []
    if not isinstance(g.get("recipient"), str) or not g["recipient"].strip():
        errors.append("recipient が必要です")
    kinds = g.get("info_kinds")
    if not isinstance(kinds, list) or not kinds or any(not isinstance(x, str) or not x.strip() for x in kinds):
        errors.append("info_kinds に共有する情報の種類が必要です")
    if not isinstance(g.get("purpose"), str) or not g["purpose"].strip():
        errors.append("purpose が必要です")
    if not _valid_period(g.get("period")):
        errors.append("period は有効なタイムゾーン付き期間が必要です")
    if any(g.get(k) is True for k in ("send", "send_allowed", "send_permission")):
        errors.append("共有許可は送信を許可しません")
    return errors


def _slot_spec(template: dict[str, Any]) -> dict[str, Any]:
    value = template.get("fields", template.get("slots", {}))
    return value if isinstance(value, dict) else {}


def _valid_value(value: Any, spec: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if isinstance(spec, str):
        kind, maximum, allowed = spec, 120, None
    elif isinstance(spec, dict):
        kind = spec.get("kind", spec.get("type", spec.get("value_type", "自由文")))
        maximum, allowed = spec.get("max_chars", 120), spec.get("allowed")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            return False
    else:
        return False
    if len(value) > maximum or "\x00" in value or "\n" in value:
        return False
    if kind in ("日時", "date_time", "datetime"):
        return _when(value) is not None
    if kind in ("日付", "date"):
        try:
            from datetime import date
            date.fromisoformat(value)
            return True
        except ValueError:
            return False
    if kind in ("時刻", "time"):
        try:
            from datetime import time
            time.fromisoformat(value)
            return True
        except ValueError:
            return False
    if kind in ("場所", "場所の種類", "place_kind"):
        return value in allowed if isinstance(allowed, list) else len(value) <= min(maximum, 24) and not re.search(r"\d{2,}", value)
    if kind in ("人", "人の呼び名", "person"):
        return len(value) <= min(maximum, 30) and not EMAILISH.search(value) and not PHONEISH.search(value)
    if kind in ("自由文", "text", "free_text"):
        return len(value) <= maximum and not EMAILISH.search(value) and not PHONEISH.search(value)
    return False


def fill_template(t: dict[str, Any], values: dict[str, Any]) -> str:
    """空欄の種類と文字数を検証して文面を作る。"""
    if not isinstance(t, dict) or not isinstance(values, dict):
        raise ValueError("文の型と空欄の値をオブジェクトで指定してください")
    body = t.get("body", t.get("text"))
    if not isinstance(body, str):
        raise ValueError("文の型に body がありません")
    names = SLOT.findall(body)
    if len(names) != len(set(names)):
        raise ValueError("文の型で空欄名を重複できません")
    if set(values) != set(names):
        raise ValueError("空欄の値が足りないか、文の型にない値があります")
    specs = _slot_spec(t)
    result = body
    for name in names:
        if name not in specs or not _valid_value(values[name], specs[name]):
            raise ValueError(f"空欄 {{{name}}} の値が型に合いません")
        result = result.replace("{" + name + "}", values[name])
    return result
