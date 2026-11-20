"""計算係：日時・空き・重なり・移動の検算。AIの頭の中で計算しないための部品。

時刻はすべて timezone 付きの datetime で扱う。文字列は ISO 8601（オフセット必須）か
「YYYY-MM-DD HH:MM JST」を受け付ける。オフセットの無い時刻は誤入力として拒否する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import re
import unicodedata

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
    """本人（self/all）の予定どうしの重なりと、本人の予定と持ち主不明（unknown）の家用の予定の重なり。
    unknown との重なりは「未確認」の1行を添えて出すため（正本22章）。unknown どうし・他人（other）は数えない。
    同じ予定の写し（ダブり）は重なりにしない。"""
    evs = [e for e in events if not e.all_day and (not owner_only or e.owner in ("self", "all", "unknown"))]
    evs.sort(key=lambda e: e.start)
    out = []
    for i, a in enumerate(evs):
        for b in evs[i + 1:]:
            if b.start >= a.end:
                break
            if owner_only and a.owner == "unknown" and b.owner == "unknown":
                continue
            if overlaps(a, b) and not is_duplicate(a, b):
                out.append((a, b))
    return out


def conflict_kind(a: Event, b: Event) -> str:
    """重なりの種類。持ち主不明が入るものは「未確認の重なり」（案は出してよいが未確認の1行を入れる）。"""
    return "未確認の重なり" if "unknown" in (a.owner, b.owner) else "重なり"


def _same_plan(a: Event, b: Event) -> bool:
    return is_duplicate(a, b)


_HONORIFIC = re.compile(r"(さん|ちゃん|くん|君|様|さま)$")


def norm_text(t: str) -> str:
    """表記ゆれをそろえる：全半角（NFKC）・カタカナ→ひらがな・空白と記号を除く・小文字。"""
    t = unicodedata.normalize("NFKC", t or "").lower()
    t = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in t)
    return re.sub(r"[\s　・、。,.:：/／()（）【】\[\]「」『』!！?？~〜\-ー_]+", "", t)


def is_duplicate(a: Event, b: Event) -> bool:
    """同じ予定の二重登録か。時刻が同じで、件名がそろえて同じか、短い方が長い方に含まれる（2文字以上）。
    同じカレンダーの中の二重登録も、家用と個人用の写しも数える。"""
    if a.all_day or b.all_day or a.start != b.start:
        return False
    if a.end != b.end and abs((a.end - b.end).total_seconds()) > 15 * 60:
        return False
    na, nb = norm_text(a.title), norm_text(b.title)
    if not na or not nb:
        return False
    short, long_ = sorted((na, nb), key=len)
    return na == nb or (len(short) >= 2 and short in long_)


def find_duplicates(events: list[Event]) -> list[tuple[Event, Event]]:
    """ダブり（同じカレンダーの中の二重登録・家用と個人用の写し・件名の少しの違い）。何も消さない。"""
    evs = [e for e in events if not e.all_day]
    out = []
    for i, a in enumerate(evs):
        for b in evs[i + 1:]:
            if a.id != b.id and is_duplicate(a, b):
                out.append((a, b))
    return out


def _norm(t: str) -> str:
    return norm_text(t)


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
    """場所のある予定の前に移動の予定が無い・移動が間に合わない・帰りの移動だけがある、を見つける。
    移動は、予定の開始の3時間前から開始までに始まるものを「その予定への移動」とみなす。
    その移動の終わりが予定の開始より後なら「移動が間に合わない」。"""
    evs = sorted([e for e in events if not e.all_day and e.owner in ("self", "all")], key=lambda e: e.start)
    moves = [e for e in evs if e.kind == "move"]
    findings = []
    for e in evs:
        if e.kind in ("move", "prep", "meal") or not e.location:
            continue
        before = [m for m in moves if m.start < e.start and e.start - m.start <= timedelta(hours=3)
                  and m.meta.get("direction") != "return"]
        if not before:
            findings.append({"type": "移動の予定が無い", "event": e.id, "title": e.title})
            continue
        m = max(before, key=lambda x: x.start)
        if m.end > e.start:
            late = int((m.end - e.start).total_seconds() // 60)
            findings.append({"type": "移動が間に合わない", "event": e.id, "title": e.title, "late_min": late})
    for m in moves:
        prior = [e for e in evs if e.kind not in ("move", "prep", "meal") and e.end <= m.start
                 and m.start - e.end <= timedelta(hours=3)]
        later = [e for e in evs if e.kind not in ("move", "prep", "meal") and e.start >= m.end
                 and e.start - m.end <= timedelta(hours=2)]
        if not prior and not later and m.meta.get("direction") == "return":
            findings.append({"type": "帰りの移動だけがあり、元の予定が無い", "event": m.id, "title": m.title})
    return findings


def departure(start: str, travel_minutes: int | None, prep_minutes: int = 0,
              travel_confirmed: bool = True) -> dict:
    """Calculate a departure reminder time, or return an ask-once suggestion.

    Unknown travel duration never produces a reminder time.
    """
    event_start = parse_time(start)
    if not travel_confirmed or travel_minutes is None:
        return {"departure_time": None, "unconfirmed": True, "create_reminder": False,
                "ask_owner_once": True,
                "suggestion": "移動の所要時間が未確認です。所要を確認してよいですか？"}
    if isinstance(travel_minutes, bool) or not isinstance(travel_minutes, int) or travel_minutes < 0:
        raise CalcError("移動の所要時間は0以上の分数で指定してください")
    if isinstance(prep_minutes, bool) or not isinstance(prep_minutes, int) or prep_minutes < 0:
        raise CalcError("準備の時間は0以上の分数で指定してください")
    leave = event_start - timedelta(minutes=travel_minutes + prep_minutes)
    return {"departure_time": leave.isoformat(), "departure_label": fmt(leave),
            "unconfirmed": False, "create_reminder": True, "ask_owner_once": False}


def day_load_minutes(events: list[Event], day: date) -> int:
    """その日の本人の予定の合計（分）。日をまたぐ予定は、その日に入る分だけ数える。重なりは二重に数えない。"""
    d0 = datetime(day.year, day.month, day.day, tzinfo=JST)
    d1 = d0 + timedelta(days=1)
    spans = sorted((max(e.start, d0), min(e.end, d1)) for e in events
                   if not e.all_day and e.owner in ("self", "all") and e.end > d0 and e.start < d1)
    total, cur = 0, d0
    for s_, e_ in spans:
        s_ = max(s_, cur)
        if e_ > s_:
            total += int((e_ - s_).total_seconds() // 60)
            cur = e_
    return total


WEEKDAY_JA = "月火水木金土日"


def weekday_ja(d: date | datetime) -> str:
    """曜日（月〜日）。確認文の「11/23(月)」の検算に使う。"""
    if isinstance(d, datetime):
        d = d.astimezone(JST).date()
    return WEEKDAY_JA[d.weekday()]


def fmt_md(dt: datetime, with_time: bool = True) -> str:
    """「11/18(水) 13:50」の形。"""
    j = dt.astimezone(JST)
    s = f"{j.month}/{j.day}({weekday_ja(j)})"
    return s + (f" {j:%H:%M}" if with_time else "")


_MD_WD = re.compile(r"(?:(\d{4})[/年-])?(\d{1,2})[/月](\d{1,2})日?\s*[(（]([月火水木金土日])[)）]")


def check_weekdays(text: str, base: date) -> list[dict]:
    """文の中の「11/23(月)」「12月5日(土)」の曜日を確かめる。年が無ければ base 以降でいちばん近い年。
    合わないものを返す（空なら全部合っている）。"""
    out = []
    for m in _MD_WD.finditer(text or ""):
        y, mo, d, wd = m.groups()
        mo, d = int(mo), int(d)
        if y:
            yy = int(y)
        else:
            yy = base.year
            try:
                if date(yy, mo, d) < base - timedelta(days=31):
                    yy += 1
            except ValueError:
                pass
        try:
            real = weekday_ja(date(yy, mo, d))
        except ValueError:
            out.append({"text": m.group(0), "error": "ありえない日付"})
            continue
        if real != wd:
            out.append({"text": m.group(0), "date": f"{yy}-{mo:02d}-{d:02d}", "written": wd, "real": real})
    return out
