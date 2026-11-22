"""先1週間の予定を調べ、衝突や準備不足の手がかりを返す。ずらし先探し（7→14→21日）は計算係 nightly。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from yoteiban_rd import calc

JST = calc.JST
DEFAULTS = {"overload_minutes": 480, "rain_prob": 60, "prep_min": 30, "travel_margin_min": 15}


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(JST) if value.tzinfo else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(JST) if parsed.tzinfo else None


def _date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    dt = _dt(value)
    if dt:
        return dt.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _event(value: dict) -> calc.Event | None:
    start, end = _dt(value.get("start")), _dt(value.get("end"))
    if not start or not end or end <= start:
        return None
    role = value.get("calendar_role", "personal")
    return calc.Event(
        id=str(value.get("id", "")), title=str(value.get("title", "予定")),
        start=start, end=end,
        calendar="household" if role in ("household", "family") else "candidate" if role == "draft" else "personal",
        owner="self" if role == "personal" else "unknown" if role in ("household", "family") else "other",
        location=value.get("location", value.get("place_kind")), kind=str(value.get("kind", "")),
        meta={"source": value.get("calendar_id", value.get("calendar_role", role))},
    )


def _finding(kind: str, day: date, refs: list[str], urgency: str, text: str) -> dict:
    refs = sorted({str(x) for x in refs if x is not None})
    return {"type": kind, "date": day.isoformat(), "refs": refs, "urgency": urgency,
            "text": text, "key": f"{kind}|{day.isoformat()}|{','.join(refs)}"}


def _case_due(case: dict) -> datetime | None:
    for field in ("due_at", "reply_by", "due", "deadline"):
        result = _dt(case.get(field))
        if result:
            return result
    return None


def _day_window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=JST)
    return start, start + timedelta(days=1)


def run(events, cases, today, settings, weather=None, travel_min=None, days=7,
        record_only: list | None = None) -> list[dict]:
    """todayを含むdays日間を調べる。入力は読み取り専用で、日付はJSTで扱う。"""
    first = _date(today)
    if first is None:
        raise ValueError("today は日付またはタイムゾーン付き日時で指定してください")
    if not isinstance(days, int) or days < 0:
        raise ValueError("days は0以上の整数です")
    cfg = {**DEFAULTS, **(settings or {})}
    evs = [_event(e) for e in events or []]
    evs = [e for e in evs if e is not None]
    by_id = {e.id: e for e in evs}
    personal = [e for e in evs if e.calendar == "personal"]
    households = [e for e in evs if e.calendar == "household"]
    candidates = [e for e in evs if e.calendar == "candidate"]
    # Keep every suppressed pair in an explicit audit sink; never turn a copy or
    # enclosing block into a warning. Callers that persist lookahead results can
    # store this alongside findings without date-specific exception rules.
    ignored_pairs = []
    for i, a in enumerate(evs):
        for b in evs[i + 1:]:
            relation = calc.relate_events(a, b)
            if relation in (calc.RELATION_SAME, calc.RELATION_CONTAINS):
                ignored_pairs.append({"type": "記録だけ", "relation": relation,
                                      "date": a.start.date().isoformat(), "refs": [a.id, b.id],
                                      "values": [{"id": a.id, "title": a.title, "start": a.start.isoformat(), "end": a.end.isoformat(), "location": a.location, "source": a.meta.get("source")},
                                                 {"id": b.id, "title": b.title, "start": b.start.isoformat(), "end": b.end.isoformat(), "location": b.location, "source": b.meta.get("source")}]})
    if record_only is not None:
        record_only.extend(ignored_pairs)
    weather_by_date = {_date(w.get("date")): w for w in (weather or []) if _date(w.get("date"))}
    findings: list[dict] = []
    def add(kind, day, refs, urgency, text):
        findings.append(_finding(kind, day, refs, urgency, text))

    for offset in range(days):
        day = first + timedelta(days=offset)
        day_events = [e for e in personal + households + candidates if e.start.date() == day]
        for i, a in enumerate(day_events):
            for b in day_events[i + 1:]:
                if calc.relate_events(a, b) == calc.RELATION_CONFLICT:
                    add("予定の食い違い", day, [a.id, b.id], "今日中",
                        f"{a.title}の時刻または場所が食い違っています")
        # 本人の予定同士、家族予定との重なり、候補と確定予定の重なり。
        for i, a in enumerate(personal):
            if a.start.date() != day:
                continue
            for b in personal[i + 1:]:
                if b.start.date() == day and calc.overlaps(a, b) and calc.relate_events(a, b) == calc.RELATION_SEPARATE:
                    add("重なり", day, [a.id, b.id], "今日中", f"{a.title}と{b.title}が重なっています")
            for b in households:
                if b.start.date() == day and calc.overlaps(a, b) and calc.relate_events(a, b) == calc.RELATION_SEPARATE:
                    add("家族と重なる", day, [a.id, b.id], "今日中", f"{a.title}が家族の予定と重なっています")
        for a in candidates:
            if a.start.date() != day:
                continue
            for b in personal:
                if b.start.date() == day and calc.overlaps(a, b) and calc.relate_events(a, b) == calc.RELATION_SEPARATE:
                    add("候補の予定が確定と重なる", day, [a.id, b.id], "今日中", f"候補の{a.title}が確定した{b.title}と重なっています")

        # 移動の所要を予定間で確認。所要不明なら判定を行わない。
        ordered = sorted([e for e in personal if e.start.date() == day], key=lambda e: e.start)
        if callable(travel_min):
            for a, b in zip(ordered, ordered[1:]):
                if calc.relate_events(a, b) in (calc.RELATION_SAME, calc.RELATION_CONTAINS):
                    continue
                if a.end > b.start or not a.location or not b.location:
                    continue
                try:
                    minutes = travel_min(a.location, b.location)
                except (TypeError, ValueError):
                    minutes = None
                if minutes is not None and int((b.start - a.end).total_seconds() // 60) < max(0, int(minutes)) + int(cfg["travel_margin_min"]):
                    add("移動が間に合わない", day, [a.id, b.id], "今日中", f"{a.title}から{b.title}への移動時間が足りません")

        # 準備時間。予定の直前の空きが設定値より短い場合に知らせる。
        prep = max(0, int(cfg["prep_min"]))
        for e in personal:
            if e.start.date() != day or prep == 0:
                continue
            if any(x is not e and calc.relate_events(e, x) in (calc.RELATION_SAME, calc.RELATION_CONTAINS) for x in personal):
                continue
            previous = [x for x in personal if x is not e and x.end <= e.start]
            prior_end = max((x.end for x in previous), default=_day_window(day)[0])
            latest_prep, _estimated = calc.latest_action_time(e.start, 0, prep, default_prep=prep)
            if prior_end > latest_prep:
                add("準備の時間が無い", day, [e.id], "今日中", f"{e.title}の前に準備の時間が足りません")

        # 締切までに、案件に記録された必要時間分の空きがあるか。
        d0, d1 = _day_window(day)
        for case in cases or []:
            due = _case_due(case)
            if not due or due.date() != day:
                continue
            needed = case.get("required_minutes", case.get("needed_minutes", case.get("estimate_minutes", 0)))
            try:
                needed = max(0, int(needed or 0))
            except (TypeError, ValueError):
                needed = 0
            if needed == 0:
                continue
            busy = [e for e in personal if e.start < due and e.end > d0]
            slots = calc.free_slots(busy, d0, due, min_minutes=1)
            available = sum(int((min(end, due) - start).total_seconds() // 60) for start, end in slots if start < due)
            if available < needed:
                add("締切の逆算", day, [str(case.get("case_id", case.get("id", "案件")))],
                    str(case.get("urgency", "今日中")), f"{case.get('title', '案件')}は期限までの空き時間が足りません")

        # 返事期限: 3日以内、期限超過や返事なしも対象。
        for case in cases or []:
            reply = _dt(case.get("reply_by"))
            status = case.get("status")
            within = reply is not None and timedelta(0) <= reply - datetime.combine(day, time.min, tzinfo=JST) <= timedelta(days=3)
            overdue = reply is not None and reply.date() < first and day == first
            unanswered = status in ("返事なし", "未返信")
            if within or overdue or (unanswered and day == first):
                due_day = reply.date() if reply and reply.date() >= first else day
                if first <= due_day < first + timedelta(days=days):
                    add("返事の期限", due_day, [str(case.get("case_id", case.get("id", "案件")))],
                        str(case.get("urgency", "今日中")), f"{case.get('title', '用事')}の返事を確認してください")

        # 雨予報と外出予定。
        forecast = weather_by_date.get(day)
        if forecast:
            try:
                probability = float(forecast.get("rain_prob", 0))
            except (TypeError, ValueError):
                probability = 0
            if probability >= float(cfg["rain_prob"]):
                for e in personal:
                    if e.start.date() == day and (e.location or "") in ("外", "屋外", "outdoor"):
                        add("雨と外出", day, [e.id], "今日中", f"{e.title}の日は雨の予報です")

        # 本人の予定だけの負荷を集計。
        load = calc.day_load_minutes(calc.representative_events(personal), day)
        if load > int(cfg["overload_minutes"]):
            add("詰め込みすぎ", day, [e.id for e in personal if e.start.date() == day], "今日中", "予定が詰まりすぎています")

        # 指定された食事時間帯に30分以上の空き枠があるか。
        meal_windows = ((11, 14), (17, 21))
        missing = []
        for start_hour, end_hour in meal_windows:
            start = datetime.combine(day, time(start_hour), tzinfo=JST)
            end = datetime.combine(day, time(end_hour), tzinfo=JST)
            if not calc.free_slots(personal, start, end, min_minutes=30):
                missing.append(f"{start_hour}時から{end_hour}時")
        if missing:
            refs = [e.id for e in personal if e.start.date() == day]
            add("食事の時間が無い", day, refs, "今日中", f"{'・'.join(missing)}に30分の空きがありません")

    # 返事期限の案件については、範囲内を一度だけ評価すればよいため重複を除く。
    unique = {item["key"]: item for item in findings}
    return sorted(unique.values(), key=lambda f: (f["date"], f["type"], f["refs"]))


def rerun(changed_dates, events, cases, today, settings, weather=None, travel_min=None,
          days=7, existing=None) -> list[dict]:
    """変更日と前後1日だけ再計算し、既存の他日付のFindingを保つ。"""
    first = _date(today)
    if first is None:
        raise ValueError("today は日付またはタイムゾーン付き日時で指定してください")
    targets = set()
    for value in changed_dates or []:
        changed = _date(value)
        if changed:
            targets.update((changed - timedelta(days=1), changed, changed + timedelta(days=1)))
    targets = {d for d in targets if first <= d < first + timedelta(days=days)}
    refreshed = run(events, cases, first, settings, weather, travel_min, days)
    kept = [f for f in (existing or []) if _date(f.get("date")) not in targets]
    return sorted(kept + [f for f in refreshed if _date(f.get("date")) in targets],
                  key=lambda f: (f["date"], f["type"], f["refs"]))
