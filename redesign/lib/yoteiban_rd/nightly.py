"""毎晩の見直し：案件ノートとカレンダーを目的と照らし、先回りの案を作る。

出すのは「案」だけ。カレンダーにも送信にも触らない。朝のまとめに載せるのは最大3件。
範囲外だった3つ（手順分け・戻る一行・作業量の目安）もここで扱う。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta

from .calc import JST, Event, find_conflicts, find_duplicates, travel_check, day_load_minutes, fmt

MAX_MORNING = 3
HEAVY_DAYS = 35
OCCASION_DAYS = 21


@dataclass
class Case:
    id: str
    title: str
    kind: str  # 予定あり / 確認待ち / 迷い / 締切 / 保留 / 返事待ち
    status: str = "open"  # open / done / dropped / paused
    due: datetime | None = None  # 期限か開始
    missing: list = field(default_factory=list)  # 欠けている項目（時刻・場所など）
    waiting_since: datetime | None = None
    goal: str | None = None  # 目的の一覧のどれに当たるか
    steps: list = field(default_factory=list)  # 手順（最大3）
    next_line: str | None = None  # 戻る一行
    on_calendar: bool = False
    heavy: bool = False  # 落とすと単位・お金・資格に響く


@dataclass
class Finding:
    kind: str
    title: str
    why: str
    proposal: str
    question: str | None = None
    options: list = field(default_factory=list)
    score: int = 0
    refs: list = field(default_factory=list)


def review(cases: list[Case], events: list[Event], now: datetime, goals: list[str],
           horizon_days: int = 14, daily_load_limit_min: int | None = None) -> dict:
    horizon = now + timedelta(days=horizon_days)
    heavy_horizon = now + timedelta(days=HEAVY_DAYS)  # 落とすと響くもの・お祝いは先まで見る
    found: list[Finding] = []
    open_cases = [c for c in cases if c.status in ("open", "paused")]
    all_future = [e for e in events if e.end > now and e.start < heavy_horizon]
    future_events = [e for e in all_future if e.start < horizon]

    for a, b in find_conflicts(future_events):
        found.append(Finding("重なり", f"{a.title} と {b.title}",
                             f"{fmt(a.start)}〜 が重なっている",
                             "どちらを優先するか、2案を出す（動かせない方を残す）",
                             "どちらを優先しますか？", [a.title, b.title, "あとで決める"],
                             _score(now, a.start, 3), [a.id, b.id]))
    for a, b in find_duplicates(future_events):
        found.append(Finding("ダブり", a.title, "家用と個人用に同じ予定がある",
                             "朝のまとめでは1行にまとめる（何も消さない）", None, [], 0, [a.id, b.id]))
    for t in travel_check(future_events):
        ev = next(e for e in future_events if e.id == t["event"])
        found.append(Finding("移動", t["title"], t["type"],
                             "前の日までに移動の時間を確かめる案" if t["type"] != "帰りの移動だけがあり、元の予定が無い"
                             else "元の予定が消えたか、入れ忘れかを確かめる",
                             "この日の予定を確かめますか？" if "帰り" in t["type"] else None,
                             ["確かめる", "そのままでいい"] if "帰り" in t["type"] else [],
                             _score(now, ev.start, 2), [t["event"]]))

    for c in open_cases:
        if c.kind == "予定あり" and c.on_calendar and not c.missing:
            found.append(Finding("片付け", c.title, "もうカレンダーにある", "案件を閉じる（記録だけ）", None, [], 0, [c.id]))
            continue
        if c.due and c.due < now and c.kind not in ("返事待ち",):
            found.append(Finding("過ぎた", c.title, "期日を過ぎたのに片付いていない",
                                 "済んだか聞いて閉じる", "これは済みましたか？", ["済んだ", "まだ", "やめた"], 1, [c.id]))
            continue
        lim = heavy_horizon if c.heavy else horizon
        if c.missing and c.due and c.due < lim:
            found.append(Finding("情報不足", c.title, "足りない：" + "・".join(c.missing),
                                 "調べ先を順に当たり、埋まらなければ期限の前に1回だけ聞く",
                                 None, [], _score(now, c.due, 2 + (1 if c.heavy else 0)), [c.id]))
        if c.kind in ("確認待ち", "返事待ち") and c.waiting_since and now - c.waiting_since > timedelta(days=2):
            found.append(Finding("返事待ち", c.title, f"{(now - c.waiting_since).days}日返事がない",
                                 "催促の文案を作る（送るのは本人のOKの後に1回）",
                                 "催促の文を作りますか？", ["作る", "もう少し待つ"], 1, [c.id]))
        if c.kind == "迷い" and not c.due:
            found.append(Finding("決め事", c.title, "決める期限が無いので、ずっと残る",
                                 "決める期限を1つ置き、空いている候補日を2つ出す",
                                 "いつまでに決めますか？", ["今週中", "今月中", "決めない"], 1, [c.id]))
        if c.kind == "締切" and c.due and c.due < heavy_horizon and not c.steps and c.heavy:
            steps = _default_steps(c, now)
            found.append(Finding("手順分け", c.title, f"締切 {fmt(c.due)}。落とすと響く",
                                 "手順を3つに分ける案：" + " → ".join(steps),
                                 "この手順で進めますか？", ["この手順で", "自分でやる"],
                                 _score(now, c.due, 3), [c.id]))
        if c.status == "paused":
            line = c.next_line or (c.steps[0] if c.steps else f"「{c.title}」の続きを決める")
            found.append(Finding("戻る一行", c.title, "止まっている案件", f"次にやること：{line}",
                                 None, [], 0, [c.id]))

    for e in all_future:
        if e.all_day and e.owner in ("self", "all", "unknown") and _is_occasion(e.title):
            days = (e.start - now).days
            if 0 <= days <= OCCASION_DAYS:
                found.append(Finding("機会", e.title, f"{days}日後",
                                     "お祝いの連絡や贈り物の準備を考える（買い物・送信はしない）",
                                     "準備の案を出しますか？", ["出して", "いらない"], 1, [e.id]))

    if daily_load_limit_min:
        days = sorted({e.start.astimezone(JST).date() for e in future_events if not e.all_day})
        for d in days:
            load = day_load_minutes(future_events, d)
            if load > daily_load_limit_min:
                found.append(Finding("作業量の目安", d.isoformat(), f"予定の合計 {load}分（目安 {daily_load_limit_min}分）",
                                     "1つを別の日に回す案を出す（管理はしない）", None, [], 0, []))

    if goals:
        for f in found:
            f.score += 1 if any(g in f.title for g in goals) else 0

    found.sort(key=lambda f: -f.score)
    morning = [f for f in found if f.score > 0][:MAX_MORNING]
    rest = [f for f in found if f not in morning]
    return {"at": fmt(now), "morning": [asdict(f) for f in morning], "record_only": [asdict(f) for f in rest]}


def _score(now: datetime, t: datetime, weight: int) -> int:
    d = (t - now).total_seconds() / 86400
    near = 3 if d <= 2 else 2 if d <= 5 else 1 if d <= 10 else 0
    return weight + near


def _default_steps(c: Case, now: datetime) -> list[str]:
    mid = now + (c.due - now) / 2
    return ["残りを数える（今日）", f"半分を終える（{mid.astimezone(JST):%m/%d}まで）", f"提出（{c.due.astimezone(JST):%m/%d %H:%M}まで）"]


def _is_occasion(title: str) -> bool:
    return any(k in title for k in ("誕生日", "記念日", "お祝い"))
