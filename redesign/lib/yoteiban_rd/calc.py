"""計算係：日時・空き・重なり・移動の検算。AIの頭の中で計算しないための部品。

時刻はすべて timezone 付きの datetime で扱う。文字列は ISO 8601（オフセット必須）か
「YYYY-MM-DD HH:MM JST」を受け付ける。オフセットの無い時刻は誤入力として拒否する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import re

JST = timezone(timedelta(hours=9))


class CalcError(ValueError):
    pass


_JST_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))? ?JST$")


def parse_time(value: str) -> datetime:
    """オフセット付きの時刻だけを受け付ける。"""
    if not isinstance(value, str) or not value.strip():
        raise CalcError("時刻が空です")
    v = value.strip()
    m = _JST_RE.match(v)
    if m:
        y, mo, d, h, mi, s = m.groups()
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=JST)
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as e:
        raise CalcError(f"時刻を読めません: {value}") from e
    if dt.tzinfo is None:
        raise CalcError(f"時刻にタイムゾーンがありません: {value}")
    return dt


def fmt(dt: datetime) -> str:
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


@dataclass
class Event:
    """カレンダーや案件の時刻つき予定。all_day の終日予定は重なり判定に入れない。"""

    id: str
    title: str
    start: datetime
    end: datetime
    calendar: str = "personal"  # personal / household / candidate
    owner: str = "self"  # self / all / other / unknown
    movable: bool = False
    kind: str = ""  # move / prep / meal / class / deadline など
    location: str | None = None
    all_day: bool = False
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise CalcError(f"終わりが始まりより前です: {self.title}")


def event_from_google(ev: dict, calendar: str, owner: str = "unknown") -> Event | None:
    """Google カレンダーの1件を Event にする。取消し済みは None。"""
    if ev.get("status") == "cancelled":
        return None
    s, e = ev.get("start") or {}, ev.get("end") or {}
    if "date" in s:
        sd = date.fromisoformat(s["date"])
        ed = date.fromisoformat(e.get("date", s["date"]))
        return Event(
            id=str(ev.get("id", "")), title=ev.get("summary", ""),
            start=datetime(sd.year, sd.month, sd.day, tzinfo=JST),
            end=datetime(ed.year, ed.month, ed.day, tzinfo=JST),
            calendar=calendar, owner=owner, all_day=True, location=ev.get("location"),
        )
    if "dateTime" not in s or "dateTime" not in e:
        raise CalcError("開始か終了の時刻がありません")
    return Event(
        id=str(ev.get("id", "")), title=ev.get("summary", ""),
        start=parse_time(s["dateTime"]), end=parse_time(e["dateTime"]),
        calendar=calendar, owner=owner, location=ev.get("location"),
    )


def overlaps(a: Event, b: Event) -> bool:
    if a.all_day or b.all_day:
        return False
    return a.start < b.end and b.start < a.end


def find_conflicts(events: list[Event], owner_only: bool = True) -> list[tuple[Event, Event]]:
    """本人（self/all）の予定どうしの重なり。移動・準備がその予定自身に付いたものは除く。"""
    evs = [e for e in events if not e.all_day and (not owner_only or e.owner in ("self", "all"))]
    evs.sort(key=lambda e: e.start)
    out = []
    for i, a in enumerate(evs):
        for b in evs[i + 1:]:
            if b.start >= a.end:
                break
            if overlaps(a, b) and not _same_plan(a, b):
                out.append((a, b))
    return out


def _same_plan(a: Event, b: Event) -> bool:
    # 同じ時刻・同じ件名（家用と個人用の写し）は重なりではなくダブり
    return a.start == b.start and a.end == b.end and a.title == b.title


def find_duplicates(events: list[Event]) -> list[tuple[Event, Event]]:
    evs = [e for e in events if not e.all_day]
    out = []
    for i, a in enumerate(evs):
        for b in evs[i + 1:]:
            if a.calendar != b.calendar and _norm(a.title) == _norm(b.title) and a.start == b.start:
                out.append((a, b))
    return out


def _norm(t: str) -> str:
    return re.sub(r"[\s　]+", "", t or "")


def free_slots(events: list[Event], day_start: datetime, day_end: datetime,
               min_minutes: int = 30) -> list[tuple[datetime, datetime]]:
    if day_end <= day_start:
        raise CalcError("範囲の終わりが始まり以前です")
    busy = sorted([(max(e.start, day_start), min(e.end, day_end)) for e in events
                   if not e.all_day and e.owner in ("self", "all") and e.end > day_start and e.start < day_end])
    slots, cur = [], day_start
    for s, e in busy:
        if s > cur and (s - cur) >= timedelta(minutes=min_minutes):
            slots.append((cur, s))
        cur = max(cur, e)
    if day_end - cur >= timedelta(minutes=min_minutes):
        slots.append((cur, day_end))
    return slots


def latest_action_time(start: datetime, travel_min: int | None, prep_min: int | None,
                       default_prep: int = 15) -> tuple[datetime, bool]:
    """行動の締切 = 開始 − 移動 − 準備。所要が分からないときは既定を使い、推定=True を返す。"""
    estimated = travel_min is None or prep_min is None
    t = travel_min if travel_min is not None else 0
    p = prep_min if prep_min is not None else default_prep
    if t < 0 or p < 0:
        raise CalcError("所要が負です")
    return start - timedelta(minutes=t + p), estimated


def travel_check(events: list[Event]) -> list[dict]:
    """場所のある予定の前に移動の予定が無い・移動が間に合わない・帰りの移動だけがある、を見つける。"""
    evs = sorted([e for e in events if not e.all_day and e.owner in ("self", "all")], key=lambda e: e.start)
    moves = [e for e in evs if e.kind == "move"]
    findings = []
    for e in evs:
        if e.kind in ("move", "prep", "meal") or not e.location:
            continue
        before = [m for m in moves if m.end <= e.start and e.start - m.end <= timedelta(hours=2)]
        if not before:
            findings.append({"type": "移動の予定が無い", "event": e.id, "title": e.title})
        else:
            m = before[-1]
            if m.end > e.start:
                findings.append({"type": "移動が間に合わない", "event": e.id, "title": e.title})
    for m in moves:
        prior = [e for e in evs if e.kind not in ("move", "prep", "meal") and e.end <= m.start
                 and m.start - e.end <= timedelta(hours=3)]
        later = [e for e in evs if e.kind not in ("move", "prep", "meal") and e.start >= m.end
                 and e.start - m.end <= timedelta(hours=2)]
        if not prior and not later and m.meta.get("direction") == "return":
            findings.append({"type": "帰りの移動だけがあり、元の予定が無い", "event": m.id, "title": m.title})
    return findings


def day_load_minutes(events: list[Event], day: date) -> int:
    """その日の本人の予定の合計（分）。作業量の目安の検算用。"""
    total = 0
    for e in events:
        if e.all_day or e.owner not in ("self", "all"):
            continue
        if e.start.astimezone(JST).date() == day:
            total += int((e.end - e.start).total_seconds() // 60)
    return total
