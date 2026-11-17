"""予定ばん（再設計）の箱側の道具。python3 -m yoteiban_rd.cli <仕事> ...
JSON を標準入力から受け、JSON を返す。何も書かない（計算と案だけ）。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .calc import CalcError, Event, find_conflicts, parse_time, travel_check, fmt
from .urgency import Context, Item, decide
from .nightly import Case, review


def _ev(d: dict) -> Event:
    return Event(id=d["id"], title=d.get("title", ""), start=parse_time(d["start"]), end=parse_time(d["end"]),
                 calendar=d.get("calendar", "personal"), owner=d.get("owner", "self"), kind=d.get("kind", ""),
                 location=d.get("location"), all_day=d.get("all_day", False), meta=d.get("meta", {}))


URGENCY_EXAMPLE = {
    "item": {"title": "通院の時刻が早まった", "time": "2026-11-19 08:45 JST", "travel_min": 30, "prep_min": 25},
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
    if it.get("time"):
        it["time"] = parse_time(it["time"])
    it["impact"] = set(it.get("impact", []))
    ctx = dict(data["context"])
    ctx["now"], ctx["next_summary"] = parse_time(ctx["now"]), parse_time(ctx["next_summary"])
    d = decide(Item(**it), Context(**ctx))
    out = asdict(d)
    for k in ("action_deadline", "remind_at"):
        out[k] = fmt(out[k]) if out[k] else None
    return out


def cmd_check_write(data: dict) -> dict:
    """書く前の検算：新しい予定と、その日の予定の重なり・移動。書いてよいかは本人の承認と ID 照合で決まる。"""
    new = _ev(data["new"])
    evs = [_ev(e) for e in data.get("events", [])]
    conf = [(a.title, b.title) for a, b in find_conflicts(evs + [new]) if new in (a, b)]
    trav = [t for t in travel_check(evs + [new]) if t["event"] == new.id]
    return {"ok": not conf and not trav, "conflicts": conf, "travel": trav}


def cmd_nightly(data: dict) -> dict:
    cases = []
    for c in data.get("cases", []):
        c = dict(c)
        for k in ("due", "waiting_since"):
            if c.get(k):
                c[k] = parse_time(c[k])
        cases.append(Case(**c))
    return review(cases, [_ev(e) for e in data.get("events", [])], parse_time(data["now"]),
                  data.get("goals", []), data.get("horizon_days", 14), data.get("daily_load_limit_min"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("task", choices=["urgency", "check-write", "nightly"])
    a = p.parse_args(argv)
    try:
        data = json.load(sys.stdin)
        out = {"urgency": cmd_urgency, "check-write": cmd_check_write, "nightly": cmd_nightly}[a.task](data)
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
