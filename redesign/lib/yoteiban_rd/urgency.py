"""急ぎの3段階（今すぐ・今日中・朝一番）を決める。第3回の設計（Page★章）の実装。

考え方：次の朝のまとめまで待つと何かを失うか。見るのは「行動の締切」
（開始・期限から移動と準備を引いた時刻）。点数ではなく余裕で決める。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .calc import CalcError, JST, latest_action_time

TIERS = ("今すぐ", "今日中", "朝一番", "ふつう", "急ぎにしない", "過ぎていた")
HEAVY = {"体", "安全", "お金", "契約", "資格", "席", "他人を待たせる", "学業の締切"}
IRREVERSIBLE_HEAVY = {"体", "安全", "お金", "契約", "資格", "席"}


@dataclass
class Item:
    title: str
    for_owner: bool = True  # 本人か全員の予定・依頼か
    needs_action: bool = True  # 本人が決める・動く・返事をする必要があるか
    already_known: bool = False  # 本人がもう知っている（自分で入れた予約の確認など）
    family_emergency: bool = False  # 正本(c)
    body_safety: bool = False  # 体調の急変・安全
    time: datetime | None = None  # 開始か期限
    time_in_text: bool = True  # 時刻が文面にあるか（推測ではないか）
    travel_min: int | None = None
    prep_min: int | None = None
    impact: set = field(default_factory=set)
    irreversible: bool = False
    registered_source: bool = True  # 信頼する出どころか
    light: bool = False  # 軽い（念押し不要・ボタンなし）


@dataclass
class Context:
    now: datetime
    next_summary: datetime  # 次の朝のまとめの時刻
    quiet_start_hour: int = 22
    quiet_end_hour: int = 7
    night_setting: str = "urgent_only"  # urgent_only（朝まで待てないものも） / safety_only（家族の急変・体・安全だけ）
    remind: str = "day_once"  # day_once / none

    def is_quiet(self, t: datetime | None = None) -> bool:
        h = (t or self.now).astimezone(JST).hour
        if self.quiet_start_hour > self.quiet_end_hour:
            return h >= self.quiet_start_hour or h < self.quiet_end_hour
        return self.quiet_start_hour <= h < self.quiet_end_hour


@dataclass
class Decision:
    tier: str
    deliver: str  # now / morning_top / morning / log
    reasons: list
    action_deadline: datetime | None = None
    estimated: bool = False
    remind_at: datetime | None = None


def _down(tier: str) -> str:
    order = ["今すぐ", "今日中", "朝一番", "ふつう"]
    if tier in order and tier != "ふつう":
        return order[order.index(tier) + 1]
    return tier


def decide(item: Item, ctx: Context) -> Decision:
    if ctx.next_summary <= ctx.now:
        raise CalcError("次の朝のまとめの時刻が今以前です")
    reasons: list[str] = []
    # 1. 安全・命・家族の急変
    if item.family_emergency:
        return Decision("今すぐ", "now", ["家族・近い人の急変（正本の(c)）"])
    # 2. 本人の行動が要るか
    if not item.for_owner:
        return Decision("急ぎにしない", "log", ["本人あてではない"])
    if item.already_known:
        return Decision("急ぎにしない", "log", ["本人がもう知っている"])
    if item.body_safety:
        d = Decision("今すぐ", "now", ["体・安全"])
        return d
    if not item.needs_action:
        return Decision("急ぎにしない", "log", ["本人がすることが無い"])
    if item.time is None:
        return Decision("ふつう", "morning", ["時刻が無い（保留）"])
    # 3. 余裕を計算する
    deadline, est = latest_action_time(item.time, item.travel_min, item.prep_min)
    if est:
        reasons.append("所要は推定")
    if item.time <= ctx.now:
        return Decision("過ぎていた", "morning", ["見つけたときには過ぎていた（正本第18章）"], deadline, est)
    slack = deadline - ctx.now
    morning_limit = ctx.next_summary + timedelta(hours=2)
    if slack <= timedelta(hours=1):
        tier = "今すぐ"
        reasons.append("行動の締切まで1時間以内")
    elif deadline < morning_limit:
        tier = "今日中"
        reasons.append("朝のまとめ＋2時間より前に動く必要がある")
    elif item.irreversible and (item.impact & IRREVERSIBLE_HEAVY) and slack <= timedelta(hours=24):
        tier = "今日中"
        reasons.append("遅れるほど取り返せなくなる重いもの")
    elif deadline < ctx.next_summary + timedelta(days=1):
        tier = "朝一番"
        reasons.append("朝のまとめで間に合うが、その日のうちに動く")
    else:
        tier = "ふつう"
        reasons.append("余裕が十分")
    # 6. 確かさで調整（体・安全は上で返している）
    if not item.registered_source and not item.time_in_text:
        if tier in ("今すぐ", "今日中"):
            reasons.append("登録外で時刻が文面に無いので急ぎにしない（正本第19章の守り）")
            tier = "朝一番"
    elif not item.registered_source or not item.time_in_text:
        if tier in ("今すぐ", "今日中", "朝一番"):
            reasons.append("確かさが低いので1段下げた")
            tier = _down(tier)
    deliver = _deliver(tier, item, ctx, deadline)
    remind = None
    if (tier == "今すぐ" and ctx.remind == "day_once" and not item.light and deliver == "now"):
        r = deadline - timedelta(minutes=15)
        if r > ctx.now + timedelta(minutes=5) and not ctx.is_quiet(r):
            remind = r
    return Decision(tier, deliver, reasons, deadline, est, remind)


def _deliver(tier: str, item: Item, ctx: Context, deadline: datetime) -> str:
    if tier == "ふつう":
        return "morning"
    if tier == "朝一番":
        return "morning_top"
    if tier in ("急ぎにしない",):
        return "log"
    if not ctx.is_quiet():
        return "now"
    # 夜（静かな時間）
    cannot_wait = deadline < ctx.next_summary + timedelta(hours=2)
    if ctx.night_setting == "urgent_only" and cannot_wait:
        return "now"
    return "morning_top"


class Notifier:
    """知らせ方の共通の決まり：1件1回、10分以内の続報は1通、やることが変わったときだけもう1回。
    1日8件を超えたら、その日1回だけ「多いですか」と聞く（件数で止めない）。"""

    def __init__(self) -> None:
        self.sent: dict[str, dict] = {}
        self.day_counts: dict[str, int] = {}
        self.asked_days: set[str] = set()

    def offer(self, key: str, action: str, at: datetime) -> dict:
        prev = self.sent.get(key)
        if prev:
            if prev["action"] == action:
                return {"send": False, "why": "同じ件・同じやることは1回だけ"}
            if at - prev["at"] <= timedelta(minutes=10):
                prev.update(action=action, at=at)
                return {"send": False, "merge": True, "why": "10分以内の続報は前の1通にまとめる"}
        self.sent[key] = {"action": action, "at": at}
        day = at.astimezone(JST).date().isoformat()
        self.day_counts[day] = self.day_counts.get(day, 0) + 1
        ask = self.day_counts[day] > 8 and day not in self.asked_days
        if ask:
            self.asked_days.add(day)
        return {"send": True, "ask_too_many": ask}
