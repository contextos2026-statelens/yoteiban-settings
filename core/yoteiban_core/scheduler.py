"""必須条件を守りながら、タスクを空き時間へ並べる純粋関数。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from yoteiban_rd import calc


def _parse_dt(value: Any, *, end_of_day=False) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(calc.JST) if value.tzinfo else None
    if isinstance(value, date):
        return datetime.combine(value, time.max if end_of_day else time.min, tzinfo=calc.JST)
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        if len(raw) == 10:
            return datetime.combine(date.fromisoformat(raw), time.max if end_of_day else time.min, tzinfo=calc.JST)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(calc.JST) if parsed.tzinfo else None


def _window(value):
    if isinstance(value, dict):
        start, end = _parse_dt(value.get("start")), _parse_dt(value.get("end"), end_of_day=True)
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        start, end = _parse_dt(value[0]), _parse_dt(value[1], end_of_day=True)
    else:
        return None
    if not start or not end or end <= start:
        return None
    return start, end


def _event(value, index=0):
    if isinstance(value, calc.Event):
        return value
    if not isinstance(value, dict):
        return None
    start, end = _parse_dt(value.get("start")), _parse_dt(value.get("end"), end_of_day=True)
    if not start or not end or end <= start:
        return None
    role = value.get("calendar_role", "personal")
    return calc.Event(id=str(value.get("id", f"fixed-{index}")), title=str(value.get("title", "予定")),
                      start=start, end=end,
                      calendar="household" if role in ("household", "family") else "personal",
                      owner="self", location=value.get("place_kind", value.get("location")))


def _durations(task):
    values = task.get("dur")
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError(f"タスク「{task.get('id', '?')}」のdurは3つの分数が必要です")
    try:
        optimistic, typical, pessimistic = (int(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ValueError("durには整数の分数を指定してください") from exc
    if optimistic <= 0 or not optimistic <= typical <= pessimistic:
        raise ValueError("durは楽観・ふつう・悲観の順に正の値で指定してください")
    return optimistic, typical, pessimistic


def _travel_minutes(a, b, settings):
    if not a or not b or not a.location or not b.location:
        return None
    travel = settings.get("travel_min")
    if callable(travel):
        try:
            value = travel(a.location, b.location)
        except (TypeError, ValueError):
            return None
    elif isinstance(travel, dict):
        value = travel.get(f"{a.location}|{b.location}")
        if value is None and isinstance(travel.get(a.location), dict):
            value = travel[a.location].get(b.location)
    else:
        return None
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _required_windows(task, constraints, default_window):
    task_id = str(task.get("id", ""))
    values = task.get("time_windows", task.get("time_window"))
    if values is None:
        values = constraints.get("time_windows", {}).get(task_id)
    if values is not None and not isinstance(values, list):
        values = [values]
    required = constraints.get("required", constraints.get("must", [])) or []
    for entry in required:
        if not isinstance(entry, dict):
            continue
        if entry.get("type", "time_window") != "time_window":
            continue
        target = entry.get("task_id", entry.get("task"))
        if target is None or str(target) == task_id:
            if values is None:
                values = []
            values.append(entry)
    if not values:
        return [default_window]
    parsed = [_window(value) for value in values]
    parsed = [(max(start, default_window[0]), min(end, default_window[1]))
              for window in parsed if window is not None for start, end in [window]]
    return [(start, end) for start, end in parsed if end > start]


def _required_deadline(task, constraints):
    value = task.get("deadline")
    for entry in constraints.get("required", constraints.get("must", [])) or []:
        if isinstance(entry, dict) and entry.get("type") == "deadline" and str(entry.get("task_id", entry.get("task", ""))) == str(task.get("id", "")):
            value = entry.get("deadline", entry.get("at", value))
            break
    return _parse_dt(value, end_of_day=True) if value is not None else None


def _after_ids(task, constraints):
    after = task.get("after", []) or []
    if isinstance(after, str):
        after = [after]
    result = [str(value) for value in after]
    for entry in constraints.get("required", constraints.get("must", [])) or []:
        if isinstance(entry, dict) and entry.get("type") == "after" and str(entry.get("task_id", entry.get("task", ""))) == str(task.get("id", "")):
            value = entry.get("after", entry.get("after_id"))
            if value is not None:
                result.append(str(value))
    return sorted(set(result))


def _ordered_tasks(tasks, constraints):
    ids = [str(task.get("id", "")) for task in tasks]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("タスクidは空でなく、重複しない値にしてください")
    by_id = dict(zip(ids, tasks))
    dependencies = {task_id: set(_after_ids(by_id[task_id], constraints)) for task_id in ids}
    for task_id, deps in dependencies.items():
        unknown = deps - set(ids)
        if unknown:
            raise ValueError(f"タスク「{task_id}」の前提タスクがありません: {', '.join(sorted(unknown))}")
    ordered = []
    remaining = set(ids)
    while remaining:
        ready = [task_id for task_id in remaining if not (dependencies[task_id] & remaining)]
        if not ready:
            raise ValueError("タスクの順番に循環があります")
        ready.sort(key=lambda task_id: (_required_deadline(by_id[task_id], constraints) or datetime.max.replace(tzinfo=calc.JST),
                                        ids.index(task_id)))
        for task_id in ready:
            ordered.append(by_id[task_id])
            remaining.remove(task_id)
    return ordered, dependencies


def _prefer_time(settings, constraints):
    prefs = constraints.get("preferences", constraints.get("prefer", [])) or []
    for pref in prefs:
        if isinstance(pref, dict) and pref.get("type") in ("morning", "朝"):
            start = _parse_clock(pref.get("start"))
            if start:
                return start, int(pref.get("weight", 1))
    start = _parse_clock(settings.get("preferred_morning_start", "08:00"))
    return (start if start else time(8, 0)), int(settings.get("morning_preference_weight", 1))


def _parse_clock(value):
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            if "T" in value or " " in value:
                parsed = _parse_dt(value)
                return parsed.timetz().replace(tzinfo=None) if parsed else None
            return time.fromisoformat(value).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _score(assignments, tasks, settings, constraints):
    task_by_id = {str(task["id"]): task for task in tasks}
    preferred, morning_weight = _prefer_time(settings, constraints)
    preferences = constraints.get("preferences", constraints.get("prefer", [])) or []
    group_weight = int(settings.get("grouping_weight", 1))
    for pref in preferences:
        if isinstance(pref, dict) and pref.get("type") in ("group", "まとめる", "まとめたい"):
            group_weight = int(pref.get("weight", group_weight))
    score = 0
    for assignment in assignments:
        task = task_by_id[assignment["id"]]
        if assignment["start_dt"].time() >= preferred and assignment["start_dt"].hour < 12:
            score += morning_weight
        group = task.get("group", task.get("batch", task.get("place_kind")))
        if group:
            neighbors = [other for other in assignments if other["id"] != assignment["id"]
                         and task_by_id[other["id"]].get("group", task_by_id[other["id"]].get("batch", task_by_id[other["id"]].get("place_kind"))) == group]
            if any(abs((assignment["start_dt"] - other["end_dt"]).total_seconds()) <= 3600
                   or abs((other["start_dt"] - assignment["end_dt"]).total_seconds()) <= 3600 for other in neighbors):
                score += group_weight
    return score


def _make_candidate(tasks, ordered, dependencies, fixed, bounds, constraints, settings, strategy):
    placed: dict[str, dict] = {}
    fixed_events = list(fixed)
    travel_margin = max(0, int(settings.get("travel_margin_min", 0)))
    preferred, _ = _prefer_time(settings, constraints)
    for task in ordered:
        task_id = str(task["id"])
        _optimistic, typical, pessimistic = _durations(task)
        duration = pessimistic
        deadline = _required_deadline(task, constraints)
        windows = _required_windows(task, constraints, bounds)
        if not windows:
            return None, f"タスク「{task.get('title', task_id)}」に有効な時間帯がありません"
        not_before = bounds[0]
        for dep in dependencies[task_id]:
            not_before = max(not_before, placed[dep]["end_dt"])
        if dependencies[task_id]:
            # 順番の直後に移動する場合、その所要と余裕を空ける。
            for dep in dependencies[task_id]:
                prior_task = next(t for t in tasks if str(t["id"]) == dep)
                a = calc.Event(id=dep, title=str(prior_task.get("title", dep)), start=placed[dep]["start_dt"],
                               end=placed[dep]["end_dt"], location=prior_task.get("place_kind"))
                b_location = task.get("place_kind")
                b = calc.Event(id=task_id, title=str(task.get("title", task_id)), start=not_before,
                               end=not_before + timedelta(minutes=duration), location=b_location)
                minutes = _travel_minutes(a, b, settings)
                if minutes is not None:
                    not_before = max(not_before, placed[dep]["end_dt"] + timedelta(minutes=minutes + travel_margin))
        feasible = []
        for win_start, win_end in windows:
            win_start = max(win_start, not_before)
            win_end = min(win_end, deadline) if deadline else win_end
            if win_end <= win_start:
                continue
            busy = fixed_events + [calc.Event(id=value["id"], title=value["title"], start=value["start_dt"], end=value["end_dt"],
                                              location=value.get("place_kind")) for value in placed.values()]
            neighbor_events = [(event.start, event.end, event.location, event.title) for event in fixed_events]
            neighbor_events.extend((item["start_dt"], item["end_dt"], item.get("place_kind"), item["title"])
                                   for item in placed.values())
            for slot_start, slot_end in calc.free_slots(busy, win_start, win_end, min_minutes=duration):
                starts = {slot_start}
                morning_anchor = datetime.combine(slot_start.date(), preferred, tzinfo=calc.JST)
                if slot_start <= morning_anchor and morning_anchor + timedelta(minutes=duration) <= slot_end:
                    starts.add(morning_anchor)
                # 直前の予定から移動が必要なら、移動後の最初の開始時刻も候補にする。
                for other_start, other_end, location, title in neighbor_events:
                    if other_end > slot_start or other_end > slot_end or not location or not task.get("place_kind"):
                        continue
                    prior = calc.Event(id="prior", title=title, start=other_start, end=other_end, location=location)
                    current = calc.Event(id=task_id, title=str(task.get("title", task_id)),
                                         start=slot_start, end=slot_start + timedelta(minutes=duration),
                                         location=task.get("place_kind"))
                    minutes = _travel_minutes(prior, current, settings)
                    if minutes is not None:
                        travel_start = other_end + timedelta(minutes=minutes + travel_margin)
                        if slot_start <= travel_start and travel_start + timedelta(minutes=duration) <= slot_end:
                            starts.add(travel_start)
                for start in starts:
                    end = start + timedelta(minutes=duration)
                    if deadline and end > deadline:
                        continue
                    if not _travel_safe(start, end, task, fixed_events, placed, tasks, settings, travel_margin):
                        continue
                    feasible.append((start, end))
        if not feasible:
            if deadline:
                return None, f"タスク「{task.get('title', task_id)}」は悲観所要でも期限に間に合いません"
            return None, f"タスク「{task.get('title', task_id)}」を必須の時間帯に置けません"
        unique = sorted(set(feasible))
        if strategy == "morning":
            choices = sorted(unique, key=lambda pair: (
                not (preferred <= pair[0].time() < time(12, 0)),
                abs((datetime.combine(pair[0].date(), pair[0].time()) -
                     datetime.combine(pair[0].date(), preferred)).total_seconds()), pair[0]))
        elif strategy == "compact":
            choices = sorted(unique, key=lambda pair: (_nearest_group_gap(pair[0], task, placed, tasks), pair[0]))
        else:
            choices = unique
        start, end = choices[0]
        placed[task_id] = {"id":task_id, "title":str(task.get("title", task_id)),
                           "start_dt":start, "end_dt":end, "duration_min":duration,
                           "typical_duration_min":typical, "deadline_safe":bool(deadline and end <= deadline) if deadline else None,
                           "place_kind":task.get("place_kind")}
    assignments = sorted(placed.values(), key=lambda item: (item["start_dt"], item["id"]))
    score = _score(assignments, tasks, settings, constraints)
    visible = [{"id":item["id"], "title":item["title"], "start":item["start_dt"].isoformat(),
                "end":item["end_dt"].isoformat(), "duration_min":item["duration_min"],
                "typical_duration_min":item["typical_duration_min"], "deadline_safe":item["deadline_safe"]}
               for item in assignments]
    return {"ok":True, "assignments":visible, "score":score, "why":"必須条件を満たす案です"}, None


def _nearest_group_gap(start, task, placed, tasks):
    group = task.get("group", task.get("batch", task.get("place_kind")))
    if not group:
        return float("inf")
    by_id = {str(item["id"]): item for item in tasks}
    same = [item for task_id, item in placed.items()
            if by_id[task_id].get("group", by_id[task_id].get("batch", by_id[task_id].get("place_kind"))) == group]
    if not same:
        return float("inf")
    return min(abs((start - item["end_dt"]).total_seconds()) for item in same)


def _travel_safe(start, end, task, fixed_events, placed, tasks, settings, margin):
    planned = []
    task_by_id = {str(item["id"]): item for item in tasks}
    for event in fixed_events:
        planned.append((event.start, event.end, event.location, event.title))
    for task_id, item in placed.items():
        planned.append((item["start_dt"], item["end_dt"], item.get("place_kind"), item["title"]))
    current_location = task.get("place_kind")
    prior = [item for item in planned if item[1] <= start and item[2] and current_location]
    following = [item for item in planned if item[0] >= end and item[2] and current_location]
    if prior:
        other_start, other_end, location, title = max(prior, key=lambda item: item[1])
        other = calc.Event(id="other", title=title, start=other_start, end=other_end, location=location)
        current = calc.Event(id="current", title=str(task.get("title", "")), start=start, end=end, location=current_location)
        minutes = _travel_minutes(other, current, settings)
        if minutes is not None and start - other_end < timedelta(minutes=minutes + margin):
            return False
    if following:
        other_start, other_end, location, title = min(following, key=lambda item: item[0])
        current = calc.Event(id="current", title=str(task.get("title", "")), start=start, end=end, location=current_location)
        other = calc.Event(id="other", title=title, start=other_start, end=other_end, location=location)
        minutes = _travel_minutes(current, other, settings)
        if minutes is not None and other_start - end < timedelta(minutes=minutes + margin):
            return False
    return True


def plan(tasks, fixed_events, window, constraints, settings) -> list[dict] | dict:
    """最大3候補を返す。必須条件の全体を満たせない場合は理由付きok=falseを返す。"""
    settings = settings or {}
    constraints = constraints or {}
    settings = dict(settings)
    required = constraints.get("required", constraints.get("must", [])) or []
    travel_map = dict(settings.get("travel_min", {}) or {}) if isinstance(settings.get("travel_min"), dict) else settings.get("travel_min")
    if travel_map is None and constraints.get("travel_min") is not None:
        travel_map = constraints.get("travel_min")
    if travel_map is None:
        travel_map = {}
    if isinstance(travel_map, dict):
        for entry in required:
            if isinstance(entry, dict) and entry.get("type") == "travel":
                source, target = entry.get("from_kind", entry.get("from")), entry.get("to_kind", entry.get("to"))
                minutes = entry.get("minutes", entry.get("travel_min"))
                if source is not None and target is not None and minutes is not None:
                    travel_map[f"{source}|{target}"] = minutes
    if travel_map:
        settings["travel_min"] = travel_map
    bounds = _window(window)
    if not bounds:
        return {"ok":False, "why":"計画期間の開始と終了を、タイムゾーン付きで指定してください"}
    tasks = list(tasks or [])
    if not tasks:
        return []
    try:
        ordered, dependencies = _ordered_tasks(tasks, constraints)
        for task in tasks:
            _durations(task)
        fixed = [_event(value, index) for index, value in enumerate(fixed_events or [])]
        fixed = [event for event in fixed if event]
    except (TypeError, ValueError) as exc:
        return {"ok":False, "why":str(exc)}
    if bounds[1] <= bounds[0]:
        return {"ok":False, "why":"計画期間の終了は開始より後にしてください"}
    results, reasons, seen = [], [], set()
    for strategy in ("earliest", "morning", "compact"):
        candidate, reason = _make_candidate(tasks, ordered, dependencies, fixed, bounds, constraints, settings, strategy)
        if candidate is None:
            reasons.append(reason)
            continue
        signature = tuple((item["id"], item["start"], item["end"]) for item in candidate["assignments"])
        if signature not in seen:
            seen.add(signature)
            results.append(candidate)
    if not results:
        return {"ok":False, "why":reasons[0] if reasons else "必須条件を満たす計画を作れません"}
    results.sort(key=lambda item: (-item["score"], tuple(a["start"] for a in item["assignments"])))
    return results[:3]
