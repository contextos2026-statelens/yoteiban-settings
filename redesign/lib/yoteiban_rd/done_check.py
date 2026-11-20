"""Offline planners for itinerary stops and end-of-event outcome checks."""
from __future__ import annotations

from datetime import timedelta
from math import asin, cos, radians, sin, sqrt

from .calc import CalcError, fmt, parse_time


def _coord(place):
    if isinstance(place, dict) and all(isinstance(place.get(k), (int, float)) for k in ("lat", "lon")):
        return float(place["lat"]), float(place["lon"])
    return None


def _distance_m(a, b):
    ca, cb = _coord(a), _coord(b)
    if not ca or not cb:
        return None
    lat1, lon1, lat2, lon2 = map(radians, (*ca, *cb))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371000 * 2 * asin(sqrt(h))


def plan_points(data: dict) -> dict:
    """Create safe stop metadata and ephemeral reminder inputs from an ordered itinerary."""
    if not isinstance(data, dict) or not isinstance(data.get("points"), list):
        raise CalcError("points は地点の配列にしてください")
    if data.get("case_status") != "決まった" or data.get("calendar") != "personal" \
            or data.get("personal_calendar_readback") is not True:
        return {"ok": False, "reason": "決まった本人用予定の読み戻し後だけ地点に分けます", "reminders": []}
    number = data.get("short_number")
    if not isinstance(number, str) or not number.isdigit() or not 1 <= len(number) <= 6:
        raise CalcError("案件の短い番号がありません")
    if data.get("movement_monitoring") not in (True, "使う"):
        return {"ok": False, "reason": "移動の見守りが使うではありません", "reminders": []}
    target = data.get("move_reminder_list") or data.get("reminder_list_id")
    if not isinstance(target, str) or not target or not data.get("case_id") or not data.get("calendar_op_id"):
        return {"ok": False, "reason": "専用リストまたは予定の照合情報がありません", "reminders": []}
    distance_limit = data.get("place_group_meters", 200)
    if isinstance(distance_limit, bool) or not isinstance(distance_limit, (int, float)) or distance_limit < 100:
        raise CalcError("place_group_meters は100m以上にしてください")

    points = sorted(data["points"], key=lambda p: p.get("order", 0))
    if any(not isinstance(p, dict) or p.get("kind") not in {"errand", "through"}
           or not isinstance(p.get("name"), str) or not p["name"] for p in points):
        raise CalcError("地点には順番・name・kind（errand／through）が必要です")
    errands = [p for p in points if p["kind"] == "errand"]
    parent = list(range(len(errands)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(errands)):
        for j in range(i + 1, len(errands)):
            distance = _distance_m(errands[i].get("location"), errands[j].get("location"))
            if distance is not None and distance <= distance_limit:
                parent[root(j)] = root(i)
    roots = {}
    grouped = []
    for i, point in enumerate(errands):
        r = root(i)
        if r not in roots:
            roots[r] = len(roots) + 1
        grouped.append((point, roots[r]))
    group_count = len(roots)
    group_members = {n: [p for p, pn in grouped if pn == n] for n in roots.values()}

    registry = []
    errand_group_for_index = {}
    errand_i = 0
    for point_no, point in enumerate(points, 1):
        if point["kind"] == "errand":
            _, group_no = grouped[errand_i]
            errand_group_for_index[point_no] = group_no
            registry.append({"point_number": point_no, "kind": "errand", "group_number": group_no,
                             "has_signal": True, "status": "まだ"})
            errand_i += 1
        else:
            registry.append({"point_number": point_no, "kind": "through", "group_number": None,
                             "has_signal": False, "status": "まだ"})

    route = data.get("google_maps_url", "")
    event_title = data.get("event_title", "")
    reminders = []
    origin_location, origin_source = select_departure_origin(data)
    if origin_location:
        reminders.append({"kind": "reminder", "grounds": "move_watch", "target_id": target,
                          "case_id": data["case_id"], "linked_op_id": data["calendar_op_id"],
                          "action": "add", "payload": {"operation": "add", "signal_type": "出発",
                          "title": f"出発：#{number}"}, "runtime_input": {"location": origin_location,
                          "trigger": "leave", "notes": f"{event_title}\n{route}"},
                          "origin_source": origin_source})
    departure_deferred = not origin_location and data.get("departure_notice", "reminder") == "reminder"
    departure_unavailable = not origin_location and not departure_deferred
    for group_no, members in group_members.items():
        if not members:
            continue
        location = members[0].get("location")
        coords = [_coord(m.get("location")) for m in members]
        coords = [c for c in coords if c]
        if len(coords) == len(members) and coords:
            location = {"lat": sum(c[0] for c in coords) / len(coords),
                        "lon": sum(c[1] for c in coords) / len(coords)}
        suffix = "" if group_count == 1 else f"-{group_no}"
        names = "、".join(p["name"] for p in members)
        payload = {"operation": "add", "signal_type": "到着", "title": f"到着：#{number}{suffix}"}
        if suffix:
            payload["point_number"] = group_no
        reminders.append({"kind": "reminder", "grounds": "move_watch", "target_id": target,
                          "case_id": data["case_id"], "linked_op_id": data["calendar_op_id"],
                          "action": "add", "payload": payload,
                          "runtime_input": {"location": location, "trigger": "arrive",
                                            "notes": f"{event_title}：{names}\n{route}"}})
        if any(p.get("completion_inferable") is True for p in members):
            departure_payload = {"operation": "add", "signal_type": "出発",
                                 "title": f"出発：#{number}-{group_no}", "point_number": group_no}
            reminders.append({"kind": "reminder", "grounds": "move_watch", "target_id": target,
                              "case_id": data["case_id"], "linked_op_id": data["calendar_op_id"],
                              "action": "add", "payload": departure_payload,
                              "runtime_input": {"location": location, "trigger": "leave",
                                                "notes": f"{event_title}：{names}\n{route}"}})
    return {"ok": True, "point_registry": registry, "reminders": reminders,
            "departure_after_notice": departure_deferred,
            "departure_unavailable": departure_unavailable,
            "departure_notice_text": ("出発の合図は置けなかったので、着いた時の合図で見ます"
                                      if departure_unavailable and not data.get("departure_failure_notified") else None)}


def select_departure_origin(data: dict):
    """Use the immediately preceding same-day event, then the home location, else defer."""
    previous = data.get("previous_event")
    if isinstance(previous, dict) and previous.get("same_day") is True \
            and previous.get("ends_before_departure") is True and previous.get("location"):
        return previous["location"], "previous_event"
    if data.get("home_event_at_time") is True and data.get("home_location"):
        return data["home_location"], "home"
    return None, None


def build_current_departure_reminder(data: dict) -> dict:
    """At a departure-notice signal, request current location only as ephemeral tool input."""
    number = data.get("short_number")
    if (data.get("departure_notice_received") is not True or data.get("departure_notice") != "reminder"
            or data.get("case_status") != "決まった" or data.get("personal_calendar_readback") is not True
            or data.get("movement_monitoring") not in (True, "使う")
            or not isinstance(number, str) or not number.isdigit()):
        return {"ok": False, "create": False, "reason": "出発の知らせ後に必要な条件を確認できません"}
    target = data.get("move_reminder_list") or data.get("reminder_list_id")
    if not isinstance(target, str) or not target or not data.get("case_id") or not data.get("calendar_op_id"):
        return {"ok": False, "create": False, "reason": "専用リストまたは予定の照合情報がありません"}
    return {"ok": True, "create": True,
            "operation": {"kind": "reminder", "grounds": "move_watch", "target_id": target,
                          "case_id": data["case_id"], "linked_op_id": data["calendar_op_id"], "action": "add",
                          "payload": {"operation": "add", "signal_type": "出発", "title": f"出発：#{number}"},
                          "runtime_input": {"current_location": True, "trigger": "leave",
                                            "notes": f"{data.get('event_title', '')}\n{data.get('google_maps_url', '')}"}}}


def prioritize_reminders(events: list, max_active: int = 10) -> dict:
    """Select nearest event reminders first; leave the rest for the prior-night job."""
    if not isinstance(events, list) or isinstance(max_active, bool) or not isinstance(max_active, int) or max_active < 0:
        raise CalcError("events と max_active の形が正しくありません")
    ordered = sorted(events, key=lambda e: parse_time(e["event_start"]))
    planned, deferred, remaining = [], [], max_active
    for event in ordered:
        jobs = event.get("reminders", [])
        take = jobs[:remaining]
        planned.extend({"case_id": event.get("case_id"), **job} for job in take)
        remaining -= len(take)
        deferred.extend({"case_id": event.get("case_id"),
                         "place_number": (j.get("payload") or {}).get("point_number"),
                         "due_at": event["event_start"]} for j in jobs[len(take):])
    return {"planned": planned, "deferred_to_previous_night": deferred}


def update_point_progress(points: list, group_number: int) -> list:
    """Apply an arrival group: through stops passed earlier become done; errands stay pending."""
    if not isinstance(points, list) or isinstance(group_number, bool) or not isinstance(group_number, int):
        raise CalcError("地点番号が正しくありません")
    group = [p for p in points if isinstance(p, dict) and p.get("kind") == "errand"
             and p.get("group_number") == group_number]
    if not group:
        raise CalcError("地点番号が一覧にありません")
    target_order = min(p["point_number"] for p in group)
    out = []
    for item in points:
        entry = dict(item)
        n = entry.get("point_number")
        if entry.get("kind") == "errand" and entry.get("group_number") == group_number:
            entry["status"] = "着いた"
        elif isinstance(n, int) and n < target_order and entry.get("kind") == "through" and entry.get("status") == "まだ":
            entry["status"] = "済んだ"
        out.append(entry)
    return out


def mark_point_skipped(points: list, point_number: int) -> list:
    """Record an explicitly skipped stop without treating an errand as completed."""
    if not isinstance(points, list) or isinstance(point_number, bool) or not isinstance(point_number, int):
        raise CalcError("地点番号が正しくありません")
    found, out = False, []
    for item in points:
        entry = dict(item)
        if entry.get("point_number") == point_number:
            entry["status"] = "飛ばした"
            found = True
        out.append(entry)
    if not found:
        raise CalcError("地点番号が一覧にありません")
    return out


def eligible_for_done_check(data: dict) -> bool:
    if data.get("done_check", "all") == "off" or data.get("cancelled") or data.get("deleted"):
        return False
    if data.get("all_day") or data.get("owner") != "self" or data.get("reply_or_check"):
        return False
    if data.get("household") and data.get("household_owner") != "self":
        return False
    if data.get("done_check") == "important" and data.get("importance") not in {"中", "高", "緊急"}:
        return False
    return True


def evaluate_done_check(data: dict) -> dict:
    """Classify known outcomes and schedule only unknown completion questions."""
    if not eligible_for_done_check(data):
        return {"eligible": False, "status": "対象外", "ask": False}
    if data.get("end_time_corrected"):
        start = parse_time(data["scheduled_start"])
        corrected_end = parse_time(data["corrected_end"])
        minutes = int((corrected_end - start).total_seconds() // 60)
        if minutes < 0:
            raise CalcError("修正された終了時刻が開始より前です")
        return {"eligible": True, "status": "終了時刻修正", "method": "カレンダー変更",
                "actual_duration_min": minutes, "ask": False, "learn_duration": True}
    if data.get("calendar_moved") or data.get("calendar_deleted"):
        return {"eligible": True, "status": "移動済み" if data.get("calendar_moved") else "削除済み", "ask": False}
    answer = data.get("chat_answer")
    answer_status = {"できた": "できた", "一部できた": "一部できた", "できなかった": "できなかった"}
    if answer in answer_status:
        return {"eligible": True, "status": answer_status[answer], "method": "本人の答え", "ask": False}
    end = parse_time(data["event_end"])
    errand_points = [p for p in data.get("points", []) if p.get("kind") == "errand"]
    group_no = data.get("point_group_number")
    if group_no is None and len({p.get("group_number") for p in errand_points}) == 1 and errand_points:
        group_no = errand_points[0].get("group_number")
    arrivals, departures = [], []
    for signal in data.get("move_signals", []):
        if not isinstance(signal, dict) or signal.get("point_number") != group_no:
            continue
        if signal.get("type") == "到着":
            arrivals.append(signal.get("at"))
        elif signal.get("type") == "出発":
            departures.append(signal.get("at"))
    arrived_value = data.get("arrived_at") or (max(arrivals) if arrivals else None)
    left_value = data.get("left_at") or (max(departures) if departures else None)
    arrived = parse_time(arrived_value) if arrived_value else None
    left = parse_time(left_value) if left_value else None
    unresolved = [p for p in errand_points if p.get("status") not in {"済んだ", "できた"}]
    not_visited = [p for p in errand_points if p.get("status") not in {"着いた", "済んだ", "できた"}]
    largest_group = max((sum(p.get("group_number") == n for p in errand_points)
                         for n in {p.get("group_number") for p in errand_points}), default=0)
    if (arrived and left and arrived <= left and left > end and data.get("completion_inferable") is True
            and not not_visited and largest_group <= 1 and not data.get("grouped_points_need_confirmation")):
        return {"eligible": True, "status": "できた", "method": "場所の合図", "ask": False}
    if data.get("end_check_due") is not True:
        return {"eligible": True, "status": "わからない", "ask": False, "ask_at": fmt(end)}
    if data.get("second_prompt_sent") and not data.get("chat_answer"):
        return {"eligible": True, "status": "わからない", "ask": False, "close": True}
    if data.get("asked_once") and not data.get("chat_answer"):
        return {"eligible": True, "status": "わからない", "ask": True, "ask_at": data.get("next_morning_at"),
                "final_prompt": True, "close_after": True}
    if unresolved:
        question = "、".join(str(p.get("display_name", f"地点{p['point_number']}")) for p in unresolved)
        question = f"{question}はできましたか？"
    else:
        question = f"{data.get('event_name', 'この予定')}はできましたか？"
    now = parse_time(data["now"]) if data.get("now") else None
    ask_at = fmt(end)
    if data.get("is_quiet_now"):
        ask_at = fmt(parse_time(data.get("next_morning_at", data["event_end"])))
    return {"eligible": True, "status": "わからない", "ask": True, "ask_at": ask_at,
            "question": question, "grouped_points": [p.get("point_number") for p in unresolved]}


def build_end_check_reminder(data: dict) -> dict:
    """Build a minimal time-reminder operation, keeping event details out of its title."""
    if not eligible_for_done_check(data):
        return {"ok": False, "create": False, "reason": "設定または予定の条件で対象外です"}
    household_read_only = (data.get("calendar") in {"household", "family"}
                           and data.get("household_read_only") is True
                           and data.get("household_owner") == "self")
    if not household_read_only and (data.get("calendar") != "personal"
                                    or data.get("personal_calendar_readback") is not True):
        return {"ok": False, "create": False, "reason": "終了時刻リマインダーは本人用読み戻し済みか本人の家用予定だけです"}
    if data.get("case_status") not in {"決まった", "仮", "［仮］", "自動", "［自動］"}:
        return {"ok": False, "create": False, "reason": "確認済み状態の予定ではありません"}
    number = data.get("short_number")
    end = data.get("event_end")
    target = data.get("move_reminder_list") or data.get("reminder_list_id")
    if not isinstance(number, str) or not number.isdigit() or not isinstance(end, str) or "T" not in end:
        return {"ok": False, "create": False, "reason": "案件番号または終了時刻がありません"}
    if not isinstance(target, str) or not target or not data.get("case_id") \
            or (not household_read_only and not data.get("calendar_op_id")):
        return {"ok": False, "create": False, "reason": "専用リストまたは照合情報がありません"}
    if data.get("already_exists") is True:
        return {"ok": False, "create": False, "reason": "終了確認リマインダーは既にあります"}
    operation = {"kind": "reminder", "grounds": "done_check", "target_id": target,
                 "case_id": data["case_id"], "linked_op_id": None if household_read_only else data["calendar_op_id"],
                 "action": "add",
                 "payload": {"operation": "add", "title": f"終わった？：#{number}", "due": end}}
    return {"ok": True, "create": True, "operation": operation}


def group_due_checks(items: list, minutes: int = 15) -> list[list]:
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
        raise CalcError("まとめる分数が正しくありません")
    ordered = sorted(items, key=lambda x: parse_time(x["due_at"]))
    groups = []
    for item in ordered:
        if not groups or parse_time(item["due_at"]) - parse_time(groups[-1][0]["due_at"]) > timedelta(minutes=minutes):
            groups.append([item])
        else:
            groups[-1].append(item)
    return groups


def cover_plan(data: dict) -> dict:
    status = data.get("status")
    if status not in {"できなかった", "一部できた"}:
        return {"propose": False, "reason_question": False}
    if data.get("cover_already_proposed"):
        return {"propose": False, "reason_question": False}
    return {"propose": True, "reason_question": not data.get("reason_answered"),
            "reason_options": ["時間が足りなかった", "体調", "忘れていた", "ほかの用事が入った", "その他"],
            "write_requires_approval": True, "send": False}
