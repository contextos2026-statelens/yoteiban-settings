"""予定ばん（再設計）の箱側の道具。python3 -m yoteiban_rd.cli <仕事> ...
JSON を標準入力から受け、JSON を返す。何も書かない（計算と案だけ）。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from datetime import date

from .calc import (CalcError, Event, find_conflicts, free_slots, fmt_md, parse_time, travel_check, departure, fmt, conflict_kind, check_weekdays,
                   weekday_ja, find_duplicates)
from .urgency import Context, Item, decide
from .nightly import Case, review, record_feedback, load_feedback, hit_rate, HORIZON_DAYS
from .morning import Source, build_morning, coverage
from .quick import diff as quick_diff, snapshot as quick_snapshot
from .latency import judge as latency_judge
from .route_links import build_route_links
from .move_watch import build_location_reminders, evaluate_move_status
from .done_check import (plan_points, build_current_departure_reminder, prioritize_reminders,
                         update_point_progress, mark_point_skipped, evaluate_done_check,
                         build_end_check_reminder, group_due_checks, cover_plan)


def _ev(d: dict) -> Event:
    return Event(id=d["id"], title=d.get("title", ""), start=parse_time(d["start"]), end=parse_time(d["end"]),
                 calendar=d.get("calendar", "personal"), owner=d.get("owner", "self"), kind=d.get("kind", ""),
                 location=d.get("location"), all_day=d.get("all_day", False), meta=d.get("meta", {}))


URGENCY_EXAMPLE = {
    "item": {"title": "通院の時刻が早まった（駅で待ち合わせ）", "time": "2026-11-19 08:20 JST", "travel_min": 30},
    "context": {"now": "2026-11-18 21:30 JST", "next_summary": "2026-11-19 07:00 JST"},
}
_ITEM_KEYS = set(Item.__dataclass_fields__)
_CTX_KEYS = set(Context.__dataclass_fields__)


class InputError(ValueError):
    pass


def _need(data, key: str, where: str) -> dict:
    if not isinstance(data, dict):
        raise InputError(f"{where}はJSONのオブジェクトにしてください")
    if key not in data:
        raise InputError(f"{where}に「{key}」がありません")
    v = data[key]
    if not isinstance(v, dict):
        raise InputError(f"「{key}」はJSONのオブジェクトにしてください")
    return v


def _check_urgency(data) -> None:
    it, ctx = _need(data, "item", "入力"), _need(data, "context", "入力")
    if "title" not in it:
        raise InputError("item に「title」がありません")
    for k in ("now", "next_summary"):
        if k not in ctx:
            raise InputError(f"context に「{k}」がありません")
    bad = sorted(set(it) - _ITEM_KEYS) + sorted(set(ctx) - _CTX_KEYS)
    if bad:
        raise InputError("知らない項目があります: " + ", ".join(bad))


def cmd_urgency(data: dict) -> dict:
    _check_urgency(data)
    it = dict(data["item"])
    for k in ("time", "end", "recover_by"):
        if it.get(k):
            it[k] = parse_time(it[k])
    it["impact"] = set(it.get("impact", []))
    ctx = dict(data["context"])
    ctx["now"], ctx["next_summary"] = parse_time(ctx["now"]), parse_time(ctx["next_summary"])
    if ctx.get("next_delivery"):
        ctx["next_delivery"] = parse_time(ctx["next_delivery"])
    d = decide(Item(**it), Context(**ctx))
    out = asdict(d)
    for k in ("action_deadline", "remind_at"):
        out[k] = fmt(out[k]) if out[k] else None
    return out


def _check_one(new: Event, evs: list) -> dict:
    pairs = [(a, b) for a, b in find_conflicts(evs + [new]) if new in (a, b)]
    conf = [(a.title, b.title) for a, b in pairs if conflict_kind(a, b) == "重なり"]
    unconf = [(a.title, b.title) for a, b in pairs if conflict_kind(a, b) == "未確認の重なり"]
    dup = [(a.title, b.title) for a, b in find_duplicates(evs + [new]) if new in (a, b)]
    trav = [t for t in travel_check(evs + [new]) if t["event"] == new.id]
    return {"ok": not conf and not trav and not dup, "conflicts": conf, "unconfirmed": unconf,
            "duplicates": dup, "travel": trav, "weekday": weekday_ja(new.start)}


def cmd_check_write(data: dict) -> dict:
    """書く前の検算：新しい予定と、その日の予定の重なり・移動・ダブり。書いてよいかは本人の承認と ID 照合で決まる。
    持ち主不明（unknown）の家用の予定との重なりは止めずに unconfirmed に出す（案に未確認の1行を入れる。第22章）。
    「news」に複数の新しい予定を渡すと1回でまとめて確かめる（同じ依頼の中の予定どうしの重なりも見る）。"""
    evs = [_ev(e) for e in data.get("events", [])]
    if "news" in data:
        out, placed = [], []
        for n in data["news"]:
            new = _ev(n)
            r = _check_one(new, evs + placed)
            r["id"] = new.id
            out.append(r)
            placed.append(new)
        return {"ok": all(r["ok"] for r in out), "items": out}
    return _check_one(_ev(data["new"]), evs)


def cmd_nightly(data: dict) -> dict:
    cases = []
    for c in data.get("cases", []):
        c = dict(c)
        for k in ("due", "waiting_since"):
            if c.get(k):
                c[k] = parse_time(c[k])
        cases.append(Case(**c))
    fb = data.get("feedback")
    if isinstance(fb, str):
        fb = load_feedback(fb)
    return review(cases, [_ev(e) for e in data.get("events", [])], parse_time(data["now"]),
                  data.get("goals", []), data.get("horizon_days", HORIZON_DAYS), data.get("daily_load_limit_min"),
                  data.get("previous"), fb, data.get("coverage"))


def _sources(data: dict) -> list:
    return [Source(s["key"], s["label"], s["role"], s.get("required", True)) for s in data["sources"]]


def cmd_coverage(data: dict) -> dict:
    return coverage(_sources(data), data.get("reads", {}))


def cmd_morning(data: dict) -> dict:
    return build_morning(date.fromisoformat(data["day"]), _sources(data), data.get("reads", {}), data.get("owner", {}),
                         data.get("weather"), data.get("confirm"), data.get("notices"))


def cmd_weekday(data: dict) -> dict:
    base = date.fromisoformat(data["base"]) if data.get("base") else date.today()
    out = {"mismatches": check_weekdays(data.get("text", ""), base)}
    if data.get("date"):
        out["weekday"] = weekday_ja(date.fromisoformat(data["date"]))
    out["ok"] = not out["mismatches"]
    return out


def cmd_diff(data: dict) -> dict:
    return quick_diff(data.get("previous"), data["now"], data.get("events", []), data.get("items", []),
                      data.get("unread"), data.get("unread_calendars"), data.get("window"))


def cmd_free(data: dict) -> dict:
    """空きを探す（対話の早道）。days：["2026-11-20", …]、from／to：毎日の時間帯（既定 9:00〜21:00）、minutes：要る長さ。
    持ち主が本人・全員の予定だけを埋まりとして数える（わからないは空きとして数え、slots の unconfirmed に出す。第22章）。"""
    from datetime import datetime, timedelta
    evs = [_ev(e) for e in data.get("events", [])]
    mins = int(data.get("minutes", 60))
    out = []
    for d in data["days"]:
        s = parse_time(f'{d} {data.get("from", "09:00")} JST')
        e = parse_time(f'{d} {data.get("to", "21:00")} JST')
        for a, b in free_slots(evs, s, e, mins):
            unk = [x.title for x in evs if x.owner == "unknown" and x.start < b and x.end > a]
            out.append({"start": fmt(a), "end": fmt(b), "label": f"{fmt_md(a)}〜{b:%H:%M}", "unconfirmed": unk})
    return {"slots": out, "count": len(out)}


def cmd_latency(data: dict) -> dict:
    """本番で測った待ち時間を基準に照らす。rows を直接渡すか、path に jsonl（1行1件）を渡す。"""
    rows = data.get("rows")
    if rows is None:
        with open(data["path"], encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    return latency_judge(rows)


def cmd_route_links(data: dict) -> dict:
    """経路を開くリンクだけを組み立てる（ネット接続・保存はしない）。"""
    return build_route_links(data)


def cmd_departure(data: dict) -> dict:
    """出発時刻を計算し、所要未確認なら質問案だけを返す。"""
    return departure(data["start"], data.get("travel_minutes"), data.get("prep_minutes", 0),
                     data.get("travel_confirmed", True))


def cmd_move_watch(data: dict) -> dict:
    """Location reminder input construction and progress checks; no network or persistence."""
    task = data.get("task")
    if task == "build_reminders":
        return build_location_reminders(data)
    if task == "status":
        return evaluate_move_status(data)
    if task == "plan_points":
        return plan_points(data)
    if task == "current_departure":
        return build_current_departure_reminder(data)
    if task == "prioritize_reminders":
        return prioritize_reminders(data.get("events", []), data.get("max_active", 10))
    if task == "point_progress":
        return {"points": update_point_progress(data.get("points", []), data["point_number"])}
    if task == "point_skipped":
        return {"points": mark_point_skipped(data.get("points", []), data["point_number"])}
    raise InputError("task は build_reminders／status／plan_points／current_departure／prioritize_reminders／point_progress／point_skipped")


def cmd_done_check(data: dict) -> dict:
    task = data.get("task", "evaluate")
    if task == "build_reminder":
        return build_end_check_reminder(data)
    if task == "evaluate":
        return evaluate_done_check(data)
    if task == "group":
        return {"groups": group_due_checks(data.get("items", []), data.get("minutes", 15))}
    if task == "cover":
        return cover_plan(data)
    raise InputError("task は build_reminder／evaluate／group／cover にしてください")


def cmd_snapshot(data: dict) -> dict:
    return quick_snapshot(data["now"], data.get("events", []), data.get("items", []), data.get("cursors"))


def cmd_feedback(data: dict) -> dict:
    return record_feedback(data["path"], data["key"], data["verdict"], parse_time(data["at"]), data.get("title", ""))


def cmd_hit_rate(data: dict) -> dict:
    return hit_rate(data["path"], parse_time(data["now"]), data.get("days", 7))


COMMANDS = {"urgency": cmd_urgency, "check-write": cmd_check_write, "nightly": cmd_nightly,
            "coverage": cmd_coverage, "morning": cmd_morning, "weekday": cmd_weekday,
            "feedback": cmd_feedback, "hit-rate": cmd_hit_rate, "diff": cmd_diff,
            "snapshot": cmd_snapshot, "free": cmd_free,
            "latency": cmd_latency, "route-links": cmd_route_links, "departure": cmd_departure,
            "move-watch": cmd_move_watch, "done-check": cmd_done_check}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("task", choices=sorted(COMMANDS))
    a = p.parse_args(argv)
    try:
        data = json.load(sys.stdin)
        out = COMMANDS[a.task](data)
    except InputError as e:
        out = {"error": str(e)}
        if a.task == "urgency":
            out["example"] = URGENCY_EXAMPLE
        print(json.dumps(out, ensure_ascii=False))
        return 2
    except KeyError as e:
        print(json.dumps({"error": f"入力に {e} がありません"}, ensure_ascii=False))
        return 2
    except (CalcError, TypeError, ValueError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
