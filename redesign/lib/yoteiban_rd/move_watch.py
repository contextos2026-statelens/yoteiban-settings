"""Location reminder inputs and movement progress calculations (offline only)."""
from __future__ import annotations

from datetime import timedelta

from .calc import CalcError, fmt, parse_time

_DAY = timedelta(days=14)
_SIGNALS = {"出発": ("出発：", "leave"), "到着": ("到着：", "arrive")}


def _enabled(value):
    return value is True or value == "使う"


def _location_ok(value):
    if isinstance(value, str):
        return bool(value.strip())
    return (isinstance(value, dict) and set(value) >= {"lat", "lon"}
            and all(isinstance(value[k], (int, float)) and not isinstance(value[k], bool) for k in ("lat", "lon")))


def build_location_reminders(data: dict) -> dict:
    """Build two tool inputs; place and URL values exist only in runtime_input."""
    if not isinstance(data, dict):
        raise CalcError("入力はJSONオブジェクトにしてください")
    failures = []
    if data.get("case_status") != "決まった": failures.append("案件が決まっていません")
    if data.get("calendar") != "personal" or data.get("is_household") is True:
        failures.append("本人用カレンダーの予定ではありません")
    if data.get("personal_calendar_readback") is not True: failures.append("個人用カレンダーの読み戻しが未確認です")
    if not _enabled(data.get("movement_monitoring")): failures.append("移動の見守り設定が使うではありません")
    target = data.get("move_reminder_list") or data.get("reminder_list_id")
    if not isinstance(target, str) or not target: failures.append("専用リストが未設定です")
    if data.get("already_exists") is True: failures.append("この予定の場所リマインダーが既にあります")
    number = data.get("short_number")
    if not isinstance(number, str) or not number.isdigit() or not 1 <= len(number) <= 6:
        failures.append("案件の短い番号がありません")
    title = data.get("event_title")
    if not isinstance(title, str) or not title: failures.append("予定名がありません")
    route = data.get("google_maps_url")
    if not isinstance(route, str) or not route.startswith("https://www.google.com/maps/dir/?api=1"):
        failures.append("Google マップの経路リンクがありません")
    origin, destination = data.get("origin_location"), data.get("destination_location")
    if not _location_ok(origin) or not _location_ok(destination): failures.append("出発地または目的地が未確認です")
    if failures:
        return {"ok": False, "create": False,
                "notify_owner": "場所のリマインダーを作れませんでした",
                "reason": failures[0], "reminders": []}

    reminders = []
    for kind, location in (("出発", origin), ("到着", destination)):
        title_prefix, trigger = _SIGNALS[kind]
        reminders.append({
            "kind": "reminder", "grounds": "move_watch", "target_id": target,
            "case_id": data.get("case_id"), "linked_op_id": data.get("calendar_op_id"),
            "action": "add",
            # Gate-persisted payload: no place, address, coordinates, route URL, or event name.
            "payload": {"operation": "add", "signal_type": kind,
                        "title": f"{title_prefix}#{number}"},
            # Pass this directly to the device tool. Never put it in ledger or history.
            "runtime_input": {"location": location, "trigger": trigger,
                              "notes": f"{title}\n{route}"},
        })
    return {"ok": True, "create": True, "reminders": reminders}


def evaluate_move_status(data: dict) -> dict:
    """Calculate lateness, next-event feasibility, observation window and one-shot proposal."""
    now = parse_time(data["now"])
    planned_departure = parse_time(data["scheduled_departure"])
    scheduled_start = parse_time(data["scheduled_start"])
    travel_ok = data.get("travel_confirmed") is True
    travel = data.get("travel_minutes")
    if travel_ok and (isinstance(travel, bool) or not isinstance(travel, int) or travel < 0):
        raise CalcError("移動所要は0以上の分数で指定してください")
    departure = parse_time(data["departure_signal_at"]) if data.get("departure_signal_at") else None
    arrival = parse_time(data["arrival_signal_at"]) if data.get("arrival_signal_at") else None
    next_start = parse_time(data["next_event_start"]) if data.get("next_event_start") else None
    late_min = 0
    if departure:
        late_min = max(0, int((departure - planned_departure).total_seconds() // 60))
    elif now > planned_departure:
        late_min = max(0, int((now - planned_departure).total_seconds() // 60))

    estimated_departure = departure or max(planned_departure, now)
    estimated_arrival = estimated_departure + timedelta(minutes=travel) if travel_ok else None
    if arrival:
        status = "到着済み"
    elif departure:
        status = "出発済み"
    elif late_min:
        status = "出発遅れ"
    else:
        status = "出発前"

    deadline = next_start or scheduled_start
    if estimated_arrival is None:
        arrival_check = "未確認"
    elif arrival:
        arrival_check = "間に合う" if arrival <= deadline else "予定失敗"
    elif estimated_arrival > deadline:
        arrival_check = "予定失敗" if now >= deadline else "間に合わない"
    else:
        arrival_check = "間に合う"

    configured_start = data.get("monitoring_started_at")
    signal_times = [v for v in (departure, arrival) if v]
    if configured_start:
        monitoring_started = parse_time(configured_start)
    elif signal_times:
        monitoring_started = min(signal_times)
    else:
        monitoring_started = None
    observation_only = monitoring_started is None or now < monitoring_started + _DAY
    issue = late_min > 0 or arrival_check in {"間に合わない", "予定失敗"}
    if arrival_check == "予定失敗" and not arrival:
        status = "予定失敗"
    already_proposed = data.get("cover_already_proposed") is True
    propose = issue and not observation_only and not already_proposed
    result = {
        "status": status,
        "departure_late_min": late_min,
        "arrival_check": arrival_check,
        "estimated_arrival": fmt(estimated_arrival) if estimated_arrival else None,
        "monitoring_started_at": fmt(monitoring_started) if monitoring_started else None,
        "observation_only": observation_only,
        "record_only": observation_only,
        "propose_adjustment_and_cover": propose,
        "proposal_key": data.get("event_key") if propose else None,
        "calibration": {"planned_departure": fmt(planned_departure),
                        "actual_departure": fmt(departure) if departure else None,
                        "actual_arrival": fmt(arrival) if arrival else None,
                        "predicted_arrival": fmt(estimated_arrival) if estimated_arrival else None,
                        "next_event_start": fmt(next_start) if next_start else None,
                        "arrival_check": arrival_check},
    }
    return result
