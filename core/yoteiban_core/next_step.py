"""案件と未回答の質問から、安全な次の一手を決める純粋関数。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from yoteiban_rd import calc

METHODS = ("台帳", "調べる", "相手に聞く", "本人に聞く", "待つ")
_METHOD_RANK = {method: index for index, method in enumerate(METHODS)}
_TERMINAL_CASES = {"見送り", "取消", "済んだ"}
_TERMINAL_QUESTIONS = {"埋まった", "埋められない"}


def _date_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(calc.JST) if value.tzinfo else None
    if isinstance(value, date):
        return datetime.combine(value, time(18, 0), tzinfo=calc.JST)
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        if len(raw) == 10:
            parsed_date = date.fromisoformat(raw)
            return datetime.combine(parsed_date, time(18, 0), tzinfo=calc.JST)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(calc.JST) if parsed.tzinfo else None


def _case_deadline(case: dict) -> datetime | None:
    for field in ("reply_by", "due_at", "due", "deadline"):
        value = _date_time(case.get(field))
        if value:
            return value
    return None


def _question_deadline(question: dict, case_deadline: datetime | None) -> datetime | None:
    for field in ("due_at", "due", "deadline", "reply_by"):
        value = _date_time(question.get(field))
        if value:
            return value
    return case_deadline


def _method(question: dict) -> str:
    method = question.get("fill_method") or question.get("method")
    if method in _METHOD_RANK:
        return method
    if question.get("fillable_from_ledger") or question.get("source") in ("台帳", "記録", "履歴"):
        return "台帳"
    if question.get("researchable") or question.get("can_research"):
        return "調べる"
    if question.get("ask_contact") or question.get("can_ask_contact"):
        return "相手に聞く"
    if question.get("ask_owner") or question.get("can_ask_owner"):
        return "本人に聞く"
    return "待つ"


def _review_at(now: datetime, urgency: str, deadline: datetime | None) -> datetime:
    local_now = now.astimezone(calc.JST)
    if urgency == "すぐ":
        review = local_now + timedelta(hours=1)
    elif urgency == "今日中":
        review = datetime.combine(local_now.date(), time(18, 0), tzinfo=calc.JST)
    else:
        review = datetime.combine(local_now.date() + timedelta(days=1), time(8, 0), tzinfo=calc.JST)
    if deadline:
        review = min(review, deadline - timedelta(hours=24))
    return review


def _result(q_id, method, review, why, draft_needed):
    return {"q_id": q_id, "method": method,
            "next_review_at": review.isoformat(),
            "why": why, "draft_needed": bool(draft_needed)}


def decide(case, questions, now, settings, open_owner_questions: int) -> dict | None:
    """未解決質問を期限順に選び、質問方法と次の確認時刻を返す。副作用は持たない。"""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now はタイムゾーン付き日時で指定してください")
    if not isinstance(case, dict):
        raise TypeError("case は dict で指定してください")
    if int(open_owner_questions) < 0:
        raise ValueError("open_owner_questions は0以上で指定してください")
    if case.get("status") in _TERMINAL_CASES:
        return None

    settings = settings or {}
    deadline = _case_deadline(case)
    urgency = case.get("urgency", "朝でよい")
    review = _review_at(now, urgency, deadline)
    local_now = now.astimezone(calc.JST)

    # 返事なしから相手への再確認は期限前に1回だけ提案する。
    if case.get("status") == "返事なし":
        already_suggested = bool(case.get("reply_reask_suggested") or case.get("reply_reask_count", 0))
        if deadline and local_now < deadline and not already_suggested:
            qid = case.get("reply_question_id")
            return _result(str(qid) if qid is not None else None, "相手に聞く", review,
                           "返事がないため、期限前の再確認を一度だけ提案します", True)
        return _result(None, "待つ", review,
                       "返事の期限か再確認の機会を待ちます", False)

    unresolved = []
    for index, question in enumerate(questions or []):
        if not isinstance(question, dict) or question.get("status") in _TERMINAL_QUESTIONS:
            continue
        method = _method(question)
        q_deadline = _question_deadline(question, deadline)
        # 期限が決まった質問を先に、同じ期限なら台帳・調べる・相手・本人・待つの順。
        sort_deadline = q_deadline or datetime.max.replace(tzinfo=calc.JST)
        unresolved.append((sort_deadline, _METHOD_RANK[method], index, question, method, q_deadline))
    if not unresolved:
        return _result(None, "待つ", review, "埋まっていない質問はありません。案件の次の確認を待ちます", False)

    _, _, _, question, method, q_deadline = min(unresolved, key=lambda item: item[:3])
    qid = question.get("q_id", question.get("question_id", question.get("id")))
    qid = str(qid) if qid is not None else None
    if method == "本人に聞く" and int(open_owner_questions) >= 1:
        return _result(qid, "待つ", _review_at(now, urgency, q_deadline or deadline),
                       "本人への質問は同時に1問までなので、先の質問への返事を待ちます", False)
    if method == "台帳":
        why = "台帳にある情報で埋められる質問を先に確認します"
    elif method == "調べる":
        why = "調べて埋められる質問を確認します"
    elif method == "相手に聞く":
        why = "相手に確認する文案を用意します。送信はしません"
    elif method == "本人に聞く":
        why = "本人に確認する質問を1つ用意します"
    else:
        why = "埋め方が決まるまで待ちます"
    return _result(qid, method, _review_at(now, urgency, q_deadline or deadline), why,
                   method == "相手に聞く")
