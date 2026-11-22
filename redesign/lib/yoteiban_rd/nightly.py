"""毎晩の見直し：案件ノートとカレンダーを目的と照らし、先回りの案を作り、昨日の案と比べて計画し直す。

出すのは「案」だけ。カレンダーにも送信にも触らない。朝のまとめに載せるのは最大3件。
- 機械で見つけるもの：重なり（持ち主不明は未確認の重なり）・ダブり・移動・家用だけにある本人の予定・
  過ぎた案件・欠けた情報（期限の無いものも）・返事待ち・期限の無い決め事・重い締切の手順分け（空いている枠つき）・
  止まった案件の戻る一行・作業量の目安・お祝い・読めていないカレンダー。
- つなぐ：同じ日付を指す案件と移動（同じ原因）は1件にまとめる。2件にだけ出てくる語（土地・行事）でつながるものは1件にまとめる。
- 計画し直す：昨日の結果（previous）と当たり外れ（feedback）を受け取り、新しい・続き・片付いたを出す。
  2晩続けて出して返事が無いものは1段上げ、3回目は朝に出さず週次の一覧へ。「外れ」と言われた案は30日出さない。
  「外れ」で止めるのは、その1件（同じ種類・同じ予定や案件・同じ日付）だけ。同じ件名でも別の日・別の回は止めない。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .calc import (JST, Event, find_conflicts, related_event_pairs, relate_events,
                   RELATION_CONFLICT, representative_events, travel_check, day_load_minutes, fmt, fmt_md,
                   conflict_kind, free_slots)

MAX_MORNING = 3
HORIZON_DAYS = 7  # nightly スキルの「先1週間」とそろえる。ずらし先探しは SHIFT_SEARCH_STEPS。重い締切の手順分けとお祝いは HEAVY_DAYS / OCCASION_DAYS
SHIFT_SEARCH_STEPS = (7, 14, 21)  # ずらし先：1週間、無ければ2週間、3週間
SHIFT_DAY_START_HOUR = 9
SHIFT_DAY_END_HOUR = 21
SHIFT_MAX_SLOTS = 3
HEAVY_DAYS = 35
OCCASION_DAYS = 21
INFO_EARLY_DAYS = 14  # 欠けた情報は、期限の14日前（重いものは21日前）から朝に出す。それより先は記録だけ
INFO_EARLY_HEAVY_DAYS = 21
HOUSEHOLD_ONLY_DAYS = 2  # 家用だけにある本人の予定を、明日・あさって分まで確かめる
MISS_SUPPRESS_DAYS = 30
MAX_SHOWN = 2  # 朝に出すのは2回まで。3回目は週次の一覧
OCCASION_RESHOW_DAYS = 7  # 仮：機会を期日の7日前に1回だけ再掲
_STOP = {"予定", "移動", "確認", "連絡", "日程", "時刻", "時間", "提出", "予約", "準備", "決める", "情報", "最終",
         "今日", "明日", "毎日", "毎週", "本人", "家族", "全員", "自宅"}


@dataclass
class Case:
    id: str
    title: str
    kind: str  # 予定あり / 確認待ち / 迷い / 締切 / 保留 / 返事待ち
    status: str = "open"  # open / done / dropped / paused
    due: datetime | None = None  # 期限か開始
    next_review_at: datetime | None = None
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
    key: str = ""
    status: str = "新しい"  # 新しい / 続き / 上げた
    shown: int = 0  # 朝に出した回数（今回を含む）
    when: str | None = None  # 関係する日付（つなぐのに使う）
    linked: list = field(default_factory=list)  # まとめた元の指摘


def _key(f: Finding) -> str:
    """当たり外れ・続きの照合に使う、その1件だけの鍵。

    予定や案件の参照があればそれで決まる（別の回は別の参照）。参照が無いときは、
    件名だけだと同じ件名の別の日まで「外れ」で止めてしまうので、種類・日付・件名を合わせて使う。
    """
    if f.refs:
        return f.kind + "|" + ",".join(sorted(f.refs))
    return f.kind + "|" + (f.when or "日付なし") + "|" + f.title




def find_shift_slots(target: Event, events: list[Event], now: datetime,
                     steps: tuple[int, ...] = SHIFT_SEARCH_STEPS,
                     max_slots: int = SHIFT_MAX_SLOTS) -> dict:
    """その日でなくてよい予定のずらし先。1週間、無ければ2週間、3週間と広げる。

    家用・ファミリーの予定は動かさない。承認前にカレンダーは触らない（案だけ）。
    見る範囲の外（8〜21日）は、ずらし先探しのためだけに読む。
    窓の始まりは now。返り値：window_days（見つかった段の日数、または最後の段）、slots、reason。
    """
    if target.calendar in ("household", "family"):
        return {"window_days": None, "slots": [], "reason": "家用の予定は動かさない"}
    duration = max(int((target.end - target.start).total_seconds() // 60), 1)
    others = [e for e in events if e.id != target.id]
    today = now.astimezone(JST).date()
    earliest = target.start.astimezone(JST).date()
    if earliest < today:
        earliest = today
    if earliest == today and target.end <= now:
        earliest = today + timedelta(days=1)
    origin = today
    for window in steps:
        found: list[tuple[datetime, datetime]] = []
        window_end = now + timedelta(days=window)
        day = origin
        while datetime(day.year, day.month, day.day, tzinfo=JST) < window_end:
            if day >= earliest:
                day_start = datetime(day.year, day.month, day.day, SHIFT_DAY_START_HOUR, tzinfo=JST)
                day_end = datetime(day.year, day.month, day.day, SHIFT_DAY_END_HOUR, tzinfo=JST)
                if day == today:
                    day_start = max(day_start, now)
                if day_end > day_start:
                    for a, b in free_slots(others, day_start, day_end, min_minutes=duration):
                        if a == target.start or a >= window_end:
                            continue
                        end_at = a + timedelta(minutes=duration)
                        if end_at > b:
                            continue
                        found.append((a, end_at))
                        if len(found) >= max_slots:
                            break
            if len(found) >= max_slots:
                break
            day += timedelta(days=1)
        if found:
            return {"window_days": window, "slots": found, "reason": None}
    last = steps[-1] if steps else None
    return {"window_days": last, "slots": [], "reason": "有力なずらし先は見つかりませんでした"}


def _attach_shift_and_alts(f: Finding, events: list[Event], now: datetime) -> None:
    """重なり・移動の指摘に、ずらし先または「案が無い」報告と、別の方法を考える一文を足す。"""
    if f.kind not in ("重なり", "移動", "未確認の重なり"):
        return
    by_id = {e.id: e for e in events}
    targets = [by_id[i] for i in f.refs if i in by_id
               and by_id[i].calendar not in ("household", "family")
               and by_id[i].owner in ("self", "all")]
    alt = "目的（何のため）と方法（どこで・どうやって）を分け、方法を変えても目的が果たせる別案も考える。買う・予約・支払いはしない。調べた店・経路は未確認と出どころをつける。"
    report = "有力な代替案が無ければ、その旨を朝のまとめで報告する。"
    if not f.proposal.endswith("。"):
        f.proposal += "。"
    if not targets:
        f.proposal += "家用の予定は動かさない。" + report
        return
    picked = None
    for ev in targets:
        result = find_shift_slots(ev, events, now)
        if result["slots"]:
            picked = (ev, result)
            break
        if picked is None:
            picked = (ev, result)
    ev, result = picked
    if result["slots"]:
        win = result["window_days"]
        lines = "、".join(fmt_md(a) for a, _ in result["slots"])
        f.proposal += f"{ev.title} のずらし先（先{win}日で見つかった）：{lines}。" + alt
        f.options = list(f.options) + [f"{fmt_md(a)}にずらす" for a, _ in result["slots"]]
    else:
        reason = result.get("reason") or "有力なずらし先は見つかりませんでした"
        f.proposal += reason + "。" + alt + report


def review(cases: list[Case], events: list[Event], now: datetime, goals: list[str],
           horizon_days: int = HORIZON_DAYS, daily_load_limit_min: int | None = None,
           previous: dict | None = None, feedback: dict | None = None, coverage: dict | None = None,
           answers: dict | None = None, no_answer_days: int = 3) -> dict:
    horizon = now + timedelta(days=horizon_days)
    heavy_horizon = now + timedelta(days=max(HEAVY_DAYS, horizon_days))
    found: list[Finding] = []
    open_cases = [c for c in cases if c.status in ("open", "paused")]
    all_future = [e for e in events if e.end > now and e.start < heavy_horizon]
    future_events = [e for e in all_future if e.start < horizon]
    related_pairs = related_event_pairs(future_events)

    # 0. 読めていないカレンダー（読めていないのに「予定なし」と言わない）
    if coverage and not coverage.get("all_ok", True):
        found.append(Finding("未確認", "読めていない入口：" + "・".join(coverage.get("unread", [])),
                             "読めていないので、この見直しは不完全", "朝のまとめに「〇〇は未確認」と1行出す（予定なしとは書かない）",
                             None, [], 9, [], when=None))

    for a, b in find_conflicts(future_events):
        k = conflict_kind(a, b)
        if k == RELATION_CONFLICT:
            def time_range(ev):
                return f"{ev.start.astimezone(JST):%H:%M}〜{ev.end.astimezone(JST):%H:%M}"
            found.append(Finding("食い違い", a.title, f"{a.calendar} {time_range(a)}／{b.calendar} {time_range(b)}。同じ用事の時刻または場所が異なる",
                                 "どちらの予定が正しいか確認する", None, [], _score(now, min(a.start, b.start), 2),
                                 [a.id, b.id], when=_d(min(a.start, b.start))))
        elif k == "未確認の重なり":
            unk = a if a.owner == "unknown" else b
            found.append(Finding(k, f"{a.title} と {b.title}", f"{fmt_md(a.start)}〜 が、誰の予定か分からない家用の予定と重なる",
                                 "重なる案は出してよいが未確認の1行を入れる（第22章）",
                                 "これは誰の予定ですか？", ["自分の予定", "家族全員の予定", "自分以外の予定"],
                                 _score(now, a.start, 1), [a.id, b.id], when=_d(a.start)))
        else:
            found.append(Finding("重なり", f"{a.title} と {b.title}", f"{fmt_md(a.start)}〜 が重なっている",
                                 "どちらを優先するか、2案を出す（動かせない方を残す）",
                                 "どちらを優先しますか？", [a.title, b.title, "あとで決める"],
                                 _score(now, a.start, 3), [a.id, b.id], when=_d(a.start)))
    for a, b, relation in related_pairs:
        details = [{"title": e.title, "start": fmt(e.start), "end": fmt(e.end), "location": e.location, "calendar": e.calendar}
                   for e in (a, b)]
        found.append(Finding("記録だけ", f"{a.title}／{b.title}", f"{relation}。予定値を保持：{details}",
                             "本人には表示せず台帳に残す", None, [], 0, [a.id, b.id], when=_d(a.start)))
    for t in travel_check(future_events):
        ev = next(e for e in future_events if e.id == t["event"])
        typ = t["type"]
        if typ == "帰りの移動だけがあり、元の予定が無い":
            f = Finding("移動", t["title"], f"{fmt_md(ev.start, False)}：{typ}", "元の予定が消えたか、入れ忘れかを確かめる",
                        "この日の予定を確かめますか？", ["確かめる", "そのままでいい"], _score(now, ev.start, 4), [t["event"]],
                        when=_d(ev.start))
        elif typ == "移動が間に合わない":
            f = Finding("移動", t["title"], f"{fmt_md(ev.start)}：移動の終わりが開始より{t.get('late_min', 0)}分遅い",
                        "出発を早めるか、移動の予定を直す案を2つ出す", "どちらにしますか？", ["出発を早める", "そのまま"],
                        _score(now, ev.start, 3), [t["event"]], when=_d(ev.start))
        else:
            f = Finding("移動", t["title"], f"{fmt_md(ev.start)}：{typ}", "前の日までに移動の時間を確かめる案",
                        None, [], _score(now, ev.start, 2), [t["event"]], when=_d(ev.start))
        found.append(f)

    # 家用だけにある本人の予定（今朝の失敗の型）。朝のまとめの今日の予定に載せる
    personal = [e for e in events if e.calendar == "personal"]
    for e in all_future:
        if e.calendar in ("household", "family") and e.owner in ("self", "all") and not e.all_day \
                and e.start < now + timedelta(days=HOUSEHOLD_ONLY_DAYS) \
                and not any(relate_events(e, p) != "別件" for p in personal):
            found.append(Finding("家用だけ", e.title, f"{fmt_md(e.start)}：家用カレンダーにだけある本人の予定",
                                 "その日の朝のまとめの📅予定に載せる（出どころ：家用カレンダー。家用には書かない）",
                                 None, [], 0, [e.id], when=_d(e.start)))

    for c in open_cases:
        if c.kind == "予定あり" and c.on_calendar and not c.missing:
            found.append(Finding("片付け", c.title, "もうカレンダーにある", "案件を閉じる（記録だけ）", None, [], 0, [c.id]))
            continue
        if c.due and c.due < now and c.kind not in ("返事待ち",) \
                and (c.next_review_at is None or c.next_review_at <= now):
            found.append(Finding("過ぎた", c.title, "期日を過ぎたのに片付いていない",
                                 "済んだか聞いて閉じる", "これは済みましたか？", ["済んだ", "まだ", "やめた"], 2, [c.id],
                                 when=_d(c.due)))
            continue
        if c.missing:
            if c.due is None:
                found.append(Finding("情報不足", c.title, "足りない：" + "・".join(c.missing) + "（期限も無い）",
                                     "調べ先を順に当たり、埋まらなければ朝のまとめで1回だけ聞く",
                                     None, [], 1, [c.id], when=_mention(c.title, now)))
            else:
                early = INFO_EARLY_HEAVY_DAYS if c.heavy else INFO_EARLY_DAYS
                if c.due - now <= timedelta(days=early):
                    found.append(Finding("情報不足", c.title, "足りない：" + "・".join(c.missing),
                                         "調べ先を順に当たり、埋まらなければ期限の前に1回だけ聞く",
                                         None, [], _score(now, c.due, 2 + (1 if c.heavy else 0)), [c.id],
                                         when=_mention(c.title, now) or _d(c.due)))
                else:
                    ask_from = (c.due - timedelta(days=early)).astimezone(JST)
                    found.append(Finding("情報不足", c.title, "足りない：" + "・".join(c.missing)
                                         + f"（まだ先。{ask_from.month}/{ask_from.day}ごろから朝に出す）",
                                         "記録だけ（今は出さない）", None, [], 0, [c.id], when=_d(c.due)))
        if c.kind in ("確認待ち", "返事待ち") and c.waiting_since and now - c.waiting_since > timedelta(days=2):
            found.append(Finding("返事待ち", c.title, f"{(now - c.waiting_since).days}日返事がない",
                                 "催促の文案を作る（送るのは本人のOKの後に1回）",
                                 "催促の文を作りますか？", ["作る", "もう少し待つ"], 2, [c.id]))
        if c.kind == "迷い" and not c.due:
            found.append(Finding("決め事", c.title, "決める期限が無いので、ずっと残る",
                                 "決める期限を1つ置き、空いている候補日を2つ出す",
                                 "いつまでに決めますか？", ["今週中", "今月中", "決めない"], 1, [c.id]))
        if c.kind == "締切" and c.due and c.due < heavy_horizon and not c.steps and c.heavy:
            steps = plan_steps(c, now, events)
            found.append(Finding("手順分け", c.title, f"締切 {fmt_md(c.due)}。落とすと響く",
                                 "手順を3つに分ける案：" + " → ".join(steps),
                                 "この手順で進めますか？", ["この手順で", "自分でやる"],
                                 _score(now, c.due, 3), [c.id], when=_d(c.due)))
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
                                     "準備の案を出しますか？", ["出して", "いらない"], 1, [e.id], when=_d(e.start)))

    if daily_load_limit_min:
        days = sorted({e.start.astimezone(JST).date() for e in future_events if not e.all_day})
        for d in days:
            load = day_load_minutes(representative_events([e for e in future_events if e.owner in ("self", "all")]), d)
            if load > daily_load_limit_min:
                found.append(Finding("作業量の目安", d.isoformat(), f"予定の合計 {load}分（目安 {daily_load_limit_min}分）",
                                     "1つを別の日に回す案を出す（管理はしない）", None, [], 1, []))

    shift_horizon = now + timedelta(days=max(SHIFT_SEARCH_STEPS))
    shift_pool = [e for e in events if e.end > now and e.start < shift_horizon]
    for f in found:
        _attach_shift_and_alts(f, shift_pool, now)

    found = _link(found)
    if goals:
        for f in found:
            if f.score > 0 and any(g and g in f.title for g in goals):
                f.score += 1
    for f in found:
        f.key = _key(f)
    out = _replan(found, now, previous, feedback, answers or {})
    if no_answer_days >= 3 and previous and (previous.get("morning") or previous.get("record_only")):
        out["notes"] = ["カードを出した後、3日続けて答えの記録がありません。カードの回答記録を確認してください。"]
    else:
        out["notes"] = []
    table_rows = [{"日付": f.get("when") or "日付なし", "件名": f["title"],
                   "何があるか": f["proposal"], "出どころ": "毎晩の台帳"}
                  for f in out["record_only"] if f.get("score", 0) > 0]
    out["second_message_markdown"] = format_second_message(table_rows) if table_rows else None
    return out


def _replan(found: list[Finding], now: datetime, previous: dict | None, feedback: dict | None, answers: dict | None = None) -> dict:
    prev_shown = {}
    prev_keys = set()
    if previous:
        for f in previous.get("morning", []) + previous.get("record_only", []):
            prev_shown[f["key"]] = f.get("shown", 1)
        for k in ("morning", "record_only", "weekly", "suppressed"):
            for f in previous.get(k, []):
                prev_keys.add(f["key"])
    fb = feedback or {}
    answers = answers or {}
    weekly, suppressed = [], []
    candidates = []
    for f in found:
        v = fb.get(f.key) or {}
        answer = answers.get(f.key)
        resurfaced_now = False
        if answer:
            if answer.get("answer") in {"当たり", "外れ", "まだ分からない"}:
                pass  # Preserve the existing feedback handling below.
            else:
            # Resurface occasion findings once inside the configured window; callers persist the marker.
                shown_after_answer = False
                if previous and previous.get("at") and answer.get("answered_at"):
                    try:
                        shown_after_answer = _p(previous["at"]) > _p(answer["answered_at"])
                    except (TypeError, ValueError):
                        shown_after_answer = True
                if f.kind != "機会" or answer.get("resurfaced") or shown_after_answer or not f.when:
                    continue
                try:
                    days_left = (date.fromisoformat(f.when) - now.astimezone(JST).date()).days
                except ValueError:
                    continue
                if not 0 <= days_left <= OCCASION_RESHOW_DAYS:
                    continue
                answer["resurfaced"] = True
                resurfaced_now = True
        if f.key in prev_keys:
            f.status = "続き"
        if v.get("verdict") == "外れ" and v.get("at") and now - _p(v["at"]) <= timedelta(days=MISS_SUPPRESS_DAYS):
            f.why += "（「外れ」と言われたので30日は朝に出さない）"
            suppressed.append(f)
            continue
        if answer and not resurfaced_now:
            continue
        n = prev_shown.get(f.key, 0)
        answered = bool(v.get("verdict")) or bool(v.get("answered"))
        if n >= MAX_SHOWN and not answered:
            f.shown = n
            f.why += f"（朝に{n}回出して返事が無いので、週次の一覧に回す）"
            weekly.append(f)
            continue
        if n >= 1 and not answered and f.score > 0:
            f.score += 1
            f.status = "上げた"
        candidates.append(f)
    candidates.sort(key=lambda f: (-f.score, f.when or "9999"))
    morning = [f for f in candidates if f.score > 0][:MAX_MORNING]
    rest = [f for f in candidates if f not in morning]
    for f in morning + rest:
        f.shown = prev_shown.get(f.key, 0) + 1
    now_keys = {f.key for f in found}
    resolved = sorted(k for k in prev_keys if k not in now_keys)
    out = {"at": fmt(now), "morning": [asdict(f) for f in morning], "record_only": [asdict(f) for f in rest],
           "weekly": [asdict(f) for f in weekly], "suppressed": [asdict(f) for f in suppressed],
           "diff": {"新しい": [f.title for f in morning + rest if f.status == "新しい"],
                    "続き": [f.title for f in morning + rest if f.status in ("続き", "上げた")],
                    "片付いた": resolved}}
    out["feedback_card"] = feedback_card(previous, fb)
    out["action_requests"] = [{"key": key, "requested": True} for key, value in answers.items()
                              if value.get("answer") == "出して"]
    return out


def feedback_card(previous: dict | None, feedback: dict) -> dict | None:
    """毎朝1枚だけ：昨日の朝に出した案のうち、まだ当たり外れが無い最初の1件を聞く。"""
    for f in (previous or {}).get("morning", []):
        if not (feedback.get(f["key"]) or {}).get("verdict"):
            return {"question": f"昨日の先回りの案「{f['title']}」は役に立ちましたか？",
                    "options": ["当たり", "外れ", "まだ分からない"], "key": f["key"]}
    return None


def _link(found: list[Finding]) -> list[Finding]:
    """同じ原因・つながりを1件にまとめる。まとめた元は linked に残す（何も捨てない）。"""
    # (1) 移動・重なりの指摘の日付を、案件の題名が「M/D」で指しているもの → 同じ原因
    by_date = [f for f in found if f.kind in ("移動", "重なり", "未確認の重なり") and f.when]
    cases_ = [f for f in found if f.kind in ("情報不足", "過ぎた", "返事待ち")]
    used = set()
    out = []
    for f in by_date:
        mates = [c for c in cases_ if id(c) not in used and _mentions_date(c.title, f.when)]
        if mates:
            for c in mates:
                used.add(id(c))
            f.linked = [asdict(c) for c in mates]
            f.kind = "まとめ"
            f.why = f.why + "／" + "／".join(f"{c.title}：{c.why}" for c in mates) + "（同じ原因の可能性）"
            f.proposal = "まとめて1回確かめる：" + f.proposal
            f.refs = sorted(set(f.refs + [r for c in mates for r in c.refs]))
            f.score = max([f.score] + [c.score for c in mates]) + 2
    found = [f for f in found if id(f) not in used]
    # (2) 2件にだけ出てくる語でつながる（決め事・機会・情報不足・手順分けのあいだ）
    linkable = [f for f in found if f.kind in ("決め事", "機会", "情報不足", "手順分け", "過ぎた") and f.score > 0]
    toks = {id(f): _tokens(f.title) for f in linkable}
    count: dict[str, int] = {}
    for f in linkable:
        for t in toks[id(f)]:
            count[t] = count.get(t, 0) + 1
    merged = set()
    for i, a in enumerate(linkable):
        for b in linkable[i + 1:]:
            if id(a) in merged or id(b) in merged or a.kind == b.kind:
                continue
            common = [t for t in toks[id(a)] & toks[id(b)] if count.get(t) == 2]
            if common:
                merged.add(id(b))
                a.linked.append(asdict(b))
                a.kind = "つながり"
                a.why = f"{a.why}／{b.title}（{b.why}）とつながる（共通：{common[0]}）"
                a.proposal = f"{a.proposal}。あわせて「{b.title}」も一緒に考える"
                a.refs = sorted(set(a.refs + b.refs))
                a.score = max(a.score, b.score) + 2
    out = [f for f in found if id(f) not in merged]
    return out


def _tokens(title: str) -> set[str]:
    ts = set(re.findall(r"[一-龥々]{2,}|[ァ-ヶー]{2,}", title or ""))
    return {t for t in ts if t not in _STOP}


_MD = re.compile(r"(\d{1,2})/(\d{1,2})")


def _mentions_date(title: str, when: str) -> bool:
    try:
        d = date.fromisoformat(when)
    except (TypeError, ValueError):
        return False
    return any(int(m) == d.month and int(dd) == d.day for m, dd in _MD.findall(title or ""))


def _mention(title: str, now: datetime) -> str | None:
    m = _MD.search(title or "")
    if not m:
        return None
    y = now.astimezone(JST).year
    try:
        d = date(y, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    if d < now.astimezone(JST).date() - timedelta(days=31):
        d = date(y + 1, d.month, d.day)
    return d.isoformat()


def _d(t: datetime) -> str:
    return t.astimezone(JST).date().isoformat()


def _p(s: str) -> datetime:
    from .calc import parse_time
    return parse_time(s)


def _score(now: datetime, t: datetime, weight: int) -> int:
    d = (t - now).total_seconds() / 86400
    near = 3 if d <= 2 else 2 if d <= 5 else 1 if d <= 10 else 0
    return weight + near


def plan_steps(c: Case, now: datetime, events: list[Event], slot_min: int = 60) -> list[str]:
    """重い締切を3つの手順に分け、それぞれに本人の予定と重ならない空き枠（9〜21時・60分）を当てる。"""
    names = ["残りを数えて、やる順を決める", "半分を終える", "見直して提出する"]
    start_day = (now + timedelta(days=1)).astimezone(JST).date()
    last_day = (c.due - timedelta(minutes=1)).astimezone(JST).date()
    days = []
    d = start_day
    while d <= last_day:
        days.append(d)
        d += timedelta(days=1)
    if not days:
        return [f"{n}（{fmt_md(c.due)}まで）" for n in names]
    picks = [days[0], days[len(days) // 2], days[-1]] if len(days) >= 3 else (days * 3)[:3]
    out = []
    for n, day in zip(names, picks):
        s = datetime(day.year, day.month, day.day, 9, tzinfo=JST)
        e = datetime(day.year, day.month, day.day, 21, tzinfo=JST)
        if day == last_day:
            e = min(e, c.due)
        slots = free_slots(events, s, e, slot_min) if e > s else []
        if slots:
            st = slots[0][0]
            out.append(f"{n}（{fmt_md(st)}〜 {slot_min}分の空き）")
        else:
            out.append(f"{n}（{day.month}/{day.day}。空きは未確認）")
    return out


def _is_occasion(title: str) -> bool:
    return any(k in title for k in ("誕生日", "記念日", "お祝い"))


def format_second_message(rows: list[dict]) -> str:
    """Format the morning follow-up as a strict Markdown table."""
    headers = ("日付", "件名", "何があるか", "出どころ")
    lines = ["| " + " | ".join(headers) + " |", "|---|---|---|---|"]
    for row in rows:
        cells = [str(row.get(k, "")).replace("|", "\\|").replace("\n", " ") for k in headers]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---- 毎日の当たり・外れの記録（箱の状態の置き場に1行ずつ足す。消さない） ----

def record_feedback(path: str, key: str, verdict: str, at: datetime, title: str = "") -> dict:
    if verdict not in ("当たり", "外れ", "まだ分からない"):
        raise ValueError("verdict は 当たり・外れ・まだ分からない のどれか")
    rec = {"key": key, "verdict": verdict, "at": fmt(at), "title": title[:60]}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load_feedback(path: str) -> dict:
    """key → いちばん新しい記録。「まだ分からない」は答えた扱い（同じ案を何度も聞かない）だが当たり外れには数えない。"""
    out: dict = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        v = r["verdict"]
        out[r["key"]] = {"verdict": v if v != "まだ分からない" else None, "answered": True, "at": r["at"]}
    return out


def hit_rate(path: str, now: datetime, days: int = 7) -> dict:
    p = Path(path)
    hit = miss = unknown = 0
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if now - _p(r["at"]) > timedelta(days=days):
                continue
            hit += r["verdict"] == "当たり"
            miss += r["verdict"] == "外れ"
            unknown += r["verdict"] == "まだ分からない"
    n = hit + miss
    return {"days": days, "当たり": hit, "外れ": miss, "まだ分からない": unknown,
            "当たり率": round(hit / n, 2) if n else None}
