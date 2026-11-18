"""案件・予定から、利用者が有効にした定型の見落としを探す。"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timedelta
from typing import Callable

from yoteiban_rd import calc
from yoteiban_core import lookahead

Finding = dict


def _as_date(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(calc.JST).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _events_on(events, day):
    found = []
    for event in events or []:
        start = event.get("start")
        d = _as_date(start)
        if d == day:
            found.append(event)
    return found


def _base(cases, events, today, settings):
    span = max(14, int((settings or {}).get("pattern_days", 14)))
    return lookahead.run(events, cases, today, settings, weather=(settings or {}).get("weather"), days=span)


def _from_lookahead(kind):
    def check(cases, events, today, settings):
        return [f for f in _base(cases, events, today, settings) if f["type"] == kind]
    return check


def _due_backwards(cases, events, today, settings):
    return _from_lookahead("締切の逆算")(cases, events, today, settings)


def _reply_deadline(cases, events, today, settings):
    return _from_lookahead("返事の期限")(cases, events, today, settings)


def _weather(cases, events, today, settings):
    return _from_lookahead("雨と外出")(cases, events, today, settings)


def _meal(cases, events, today, settings):
    return _from_lookahead("食事の時間が無い")(cases, events, today, settings)


def _supplies(cases, events, today, settings):
    first = _as_date(today)
    if first is None:
        raise ValueError("today は日付またはタイムゾーン付き日時で指定してください")
    span = max(14, int((settings or {}).get("pattern_days", 14)))
    last = first + timedelta(days=span)
    findings = []
    for case in cases or []:
        due = None
        for key in ("event_start", "due_at", "due", "deadline"):
            due = _as_date(case.get(key))
            if due:
                break
        if not due:
            continue
        reminder = due - timedelta(days=1)
        if not first <= reminder < last:
            continue
        for question in case.get("questions", []) or []:
            description = " ".join(str(question.get(k, "")) for k in ("kind", "category", "text", "question", "title"))
            is_supply = "持ち物" in description or question.get("kind") == "持ち物" or question.get("category") == "持ち物"
            if not is_supply or question.get("status") == "埋まった":
                continue
            cid = str(case.get("case_id", case.get("id", "案件")))
            qid = str(question.get("q_id", question.get("question_id", "持ち物")))
            findings.append({"type":"持ち物", "date":reminder.isoformat(), "refs":sorted([cid, qid]),
                             "urgency":str(case.get("urgency", "今日中")),
                             "text":f"{case.get('title', '予定')}の持ち物を前日までに確認してください",
                             "key":f"持ち物|{reminder.isoformat()}|{','.join(sorted([cid, qid]))}"})
    return findings


def _monthly(cases, events, today, settings):
    first = _as_date(today)
    if first is None:
        raise ValueError("today は日付またはタイムゾーン付き日時で指定してください")
    span = max(14, int((settings or {}).get("pattern_days", 14)))
    target_month = (first.year, first.month)
    by_title = {}
    for event in events or []:
        day = _as_date(event.get("start"))
        title = calc.norm_text(str(event.get("title", "")))
        if not day or not title:
            continue
        entry = by_title.setdefault(title, {"months": set(), "days": [], "events": [], "title": str(event.get("title", "予定"))})
        entry["months"].add((day.year, day.month))
        entry["days"].append(day.day)
        entry["events"].append(str(event.get("id", "")))
    findings = []
    minimum = max(2, int((settings or {}).get("monthly_min_occurrences", 2)))
    for value in by_title.values():
        if len(value["months"]) < minimum or target_month in value["months"]:
            continue
        # 過去の月の開催日から、今月の予定日を求める（存在しない日付は月末）。
        day_num = sorted(value["days"])[len(value["days"]) // 2]
        day_num = min(day_num, monthrange(first.year, first.month)[1])
        due = date(first.year, first.month, day_num)
        # 月例日を過ぎていても今月の予定が無ければ、今日知らせる。
        due = max(due, first)
        if due >= first + timedelta(days=span):
            continue
        refs = sorted(set(value["events"]))
        findings.append({"type":"毎月の用事", "date":due.isoformat(), "refs":refs, "urgency":"朝でよい",
                         "text":f"毎月ある「{value['title']}」が今月の予定に見当たりません",
                         "key":f"毎月の用事|{due.isoformat()}|{','.join(refs)}"})
    return findings


def _anniversary_date(value, today):
    raw = value.get("date") if isinstance(value, dict) else value
    if not isinstance(raw, str):
        return None, value.get("title", "記念日") if isinstance(value, dict) else "記念日"
    title = value.get("title", "記念日") if isinstance(value, dict) else "記念日"
    try:
        return date.fromisoformat(raw[:10]), title
    except ValueError:
        try:
            month, day = raw.lstrip("-/").split("-")[-2:]
            candidate = date(today.year, int(month), int(day))
            if candidate < today:
                candidate = date(today.year + 1, int(month), int(day))
            return candidate, title
        except (ValueError, TypeError):
            return None, title


def _anniversary(cases, events, today, settings):
    first = _as_date(today)
    if first is None:
        raise ValueError("today は日付またはタイムゾーン付き日時で指定してください")
    span = max(14, int((settings or {}).get("pattern_days", 14)))
    anniversary_values = (settings or {}).get("anniversaries", (settings or {}).get("anniversary_dates", []))
    findings = []
    for index, value in enumerate(anniversary_values or []):
        day, title = _anniversary_date(value, first)
        if not day:
            continue
        alert = day - timedelta(days=7)
        if not first <= alert < first + timedelta(days=span):
            continue
        ref = str(value.get("id", f"記念日{index + 1}")) if isinstance(value, dict) else f"記念日{index + 1}"
        findings.append({"type":"記念日", "date":alert.isoformat(), "refs":[ref], "urgency":"今日中",
                         "text":f"「{title}」まであと7日です", "key":f"記念日|{alert.isoformat()}|{ref}"})
    return findings


def _person_ids(event):
    people = []
    for key in ("person_id", "person_ids", "people", "attendees"):
        value = event.get(key)
        if isinstance(value, str):
            people.append(value)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, dict):
                    person = item.get("person_id", item.get("id"))
                    if person:
                        people.append(str(person))
                elif item:
                    people.append(str(item))
    return set(people)


def _person_overload(cases, events, today, settings):
    first = _as_date(today)
    if first is None:
        raise ValueError("today は日付またはタイムゾーン付き日時で指定してください")
    week_start = first - timedelta(days=first.weekday())
    week_end = week_start + timedelta(days=7)
    threshold = max(1, int((settings or {}).get("same_person_weekly_threshold", (settings or {}).get("person_weekly_threshold", 5))))
    seen = {}
    for event in events or []:
        day = _as_date(event.get("start"))
        if not day or not week_start <= day < week_end:
            continue
        for person in _person_ids(event):
            seen.setdefault(person, []).append(event)
    findings = []
    for person, person_events in seen.items():
        if len(person_events) <= threshold:
            continue
        refs = sorted(str(event.get("id", "")) for event in person_events)
        findings.append({"type":"重なりすぎ", "date":week_start.isoformat(), "refs":refs, "urgency":"今日中",
                         "text":f"同じ人との予定が1週間に{len(person_events)}件あります",
                         "key":f"重なりすぎ|{week_start.isoformat()}|{','.join(refs)}"})
    return findings


REGISTRY: dict[str, Callable] = {
    "締切の逆算": _due_backwards,
    "返事の期限": _reply_deadline,
    "天気（雨と外出）": _weather,
    "持ち物": _supplies,
    "毎月の用事": _monthly,
    "記念日": _anniversary,
    "食事の時間": _meal,
    "重なりすぎ": _person_overload,
}


def check_all(cases, events, today, enabled: set, settings) -> list[Finding]:
    """有効な型だけを実行し、Findingを日付・型順で返す。"""
    if not isinstance(enabled, (set, frozenset)):
        raise TypeError("enabled は型名の set で指定してください")
    findings = []
    delegated = {
        "締切の逆算": "締切の逆算", "返事の期限": "返事の期限",
        "天気（雨と外出）": "雨と外出", "食事の時間": "食事の時間が無い",
    }
    selected = {name: kind for name, kind in delegated.items() if name in enabled}
    if selected:
        findings.extend(f for f in _base(cases, events, today, settings or {})
                         if f["type"] in set(selected.values()))
    for name in sorted(enabled):
        if name in delegated:
            continue
        checker = REGISTRY.get(name)
        if checker is not None:
            findings.extend(checker(cases, events, today, settings or {}))
    unique = {finding["key"]: finding for finding in findings}
    return sorted(unique.values(), key=lambda item: (item["date"], item["type"], item["refs"]))


def propose(missed_examples) -> dict:
    """見落とし例から、人が確認する型の案を返す。型の登録や実行は行わない。"""
    examples = [dict(item) if isinstance(item, dict) else {"example": str(item)} for item in (missed_examples or [])]
    return {"title": "新しい見張り型の案", "examples": examples,
            "signals": sorted({str(item.get("type", item.get("kind", "未分類"))) for item in examples}),
            "requires_human_approval": True}
