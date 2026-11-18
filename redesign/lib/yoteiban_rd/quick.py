"""本人が待っている対話のための早道（計算と比べるだけ。何も書かない）。

- diff：前回の確認の控え（snapshot）と今回読んだものを比べ、新しい・変わった・消えた予定と、新着だけを返す。
  口頭の「定期確認と同じことをして」は、これで差分だけを読む。控えが無い・古いときは full_needed=True。
- snapshot：今回の結果を次回のための控えの形にする（予定は id・件名・開始・終わり・場所だけ。本文は入れない）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .calc import parse_time, fmt_md

STALE_HOURS = 12  # 控えがこれより古ければ、差分でなく全部読む


def _key(e: dict) -> str:
    return str(e.get("id") or f'{e.get("title")}|{e.get("start")}')


def _shape(e: dict) -> dict:
    return {"id": _key(e), "title": e.get("title", ""), "start": e.get("start"), "end": e.get("end"),
            "location": e.get("location"), "calendar": e.get("calendar", "personal")}


def snapshot(now: str, events: list[dict], items: list[dict], cursors: dict | None = None) -> dict:
    return {"at": now, "events": [_shape(e) for e in events],
            "items": [{"ref": str(i.get("ref")), "title": i.get("title", "")} for i in items],
            "cursors": cursors or {}}


def _when(e: dict) -> str:
    try:
        return fmt_md(parse_time(e["start"]))
    except Exception:  # noqa: BLE001 - 表示だけ
        return str(e.get("start"))


def diff(previous: dict | None, now: str, events: list[dict], items: list[dict], unread: list[str] | None = None,
         unread_calendars: list[str] | None = None, window: dict | None = None) -> dict:
    """前回の控えと今回を比べる。
    unread：今回読めなかった入口の名前（「未確認」と書く。予定なし・変わりなしとは書かない）。
    unread_calendars：読めなかったカレンダーのキー。その予定は「消えた」にせず、控えにそのまま残す。
    window：今回読んだ範囲 {"start","end"}。範囲の外の前回の予定は比べず、控えに残す。"""
    t_now = parse_time(now)
    stale = True
    if previous and previous.get("at"):
        stale = t_now - parse_time(previous["at"]) > timedelta(hours=STALE_HOURS)
    if not previous or stale:
        return {"full_needed": True, "reason": "前回の控えが無い" if not previous else f"前回の控えが{STALE_HOURS}時間より古い",
                "snapshot": snapshot(now, events, items), "unread": unread or []}
    skip_cal = set(unread_calendars or [])
    w0 = parse_time(window["start"]) if window else None
    w1 = parse_time(window["end"]) if window else None

    def in_scope(e: dict) -> bool:
        if e.get("calendar", "personal") in skip_cal:
            return False
        if w0 is None:
            return True
        try:
            s = parse_time(e["start"])
        except Exception:  # noqa: BLE001
            return True
        return w0 <= s < w1

    carried = [e for e in previous.get("events", []) if not in_scope(e)]
    old = {e["id"]: e for e in previous.get("events", []) if in_scope(e)}
    cur = {_key(e): _shape(e) for e in events}
    new_ev = [cur[k] for k in cur if k not in old]
    gone = [old[k] for k in old if k not in cur]
    changed = []
    for k in cur:
        if k in old:
            diffs = [f for f in ("title", "start", "end", "location") if (cur[k].get(f) or None) != (old[k].get(f) or None)]
            if diffs:
                changed.append({"before": old[k], "after": cur[k], "fields": diffs})
    seen = {i["ref"] for i in previous.get("items", [])}
    new_items = [i for i in items if str(i.get("ref")) not in seen]
    lines = ([f"・新しい予定：{_when(e)} {e['title']}" for e in new_ev] +
             [f"・変わった予定：{c['before']['title']}（{_when(c['before'])} → {_when(c['after'])}）" if "start" in c["fields"]
              else f"・変わった予定：{c['after']['title']}（{'・'.join(c['fields'])}）" for c in changed] +
             [f"・消えた予定：{_when(e)} {e['title']}" for e in gone] +
             [f"・新着：{i.get('title', '')}" for i in new_items] +
             [f"・{u}は未確認（読めていない）" for u in (unread or [])])
    nothing = not (new_ev or changed or gone or new_items)
    return {"full_needed": False, "since": previous["at"], "new_events": new_ev, "changed": changed, "gone": gone,
            "new_items": new_items, "lines": lines,
            # 1つでも読めていなければ「変わりなし」と言わない（第25章）
            "can_say_no_change": nothing and not unread,
            "snapshot": {**snapshot(now, events, list(previous.get("items", [])) + new_items, previous.get("cursors")),
                         "events": [_shape(e) for e in events] + [e for e in carried if e["id"] not in cur]}}
