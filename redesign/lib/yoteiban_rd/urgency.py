"""急ぎの3段階（今すぐ・今日中・朝一番）を決める。第3回の設計（Page★章）と DESIGN.md 4章の実装。

考え方：次の朝のまとめまで待つと何かを失うか。
  行動の締切 A = 開始・期限 − 移動 − 準備
  待った場合に本人が見る時刻 W = 次の朝のまとめ
  余裕 S = A − W
- S ≤ 1時間（0以上）           → 今すぐ（待つと朝に1時間も残らない）
- A が W より前（S < 0）       → 今からの余裕が1時間以内なら今すぐ、それ以外は今日中（朝より前に動く）
- 1時間 < S < 2時間            → 今日中（夜は第19章の「待てない」なので夜でも1回）
- 取り返せない重いもので24時間以内 → 今日中
- A が W から1日以内           → 朝一番、それより先 → ふつう
第19章の重要度（緊急・高・中・低、待てない）が渡されたら、時刻の段階と比べて高い方を採る。
段階と知らせ方（deliver）は必ず対応させる：今すぐ→now／今日中→now か next_delivery／朝一番→morning_top。
夜の設定や通知の型で出せないときは、段階のほうを下げて理由に書く（名前と知らせ方をずらさない）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .calc import CalcError, JST, latest_action_time

TIERS = ("今すぐ", "今日中", "朝一番", "ふつう", "急ぎにしない", "過ぎていた", "過ぎた予定の急ぎ")
TIER_DELIVER = {"今すぐ": {"now"}, "今日中": {"now", "next_delivery"}, "朝一番": {"morning_top"},
                "ふつう": {"morning"}, "急ぎにしない": {"log"}, "過ぎていた": {"morning_top"},
                "過ぎた予定の急ぎ": {"now"}}
PAST_B_HOURS = 6  # 第18章(b)「動ける期限が数時間以内」の上限
UNKNOWN_SENDER_NOTE = "知らない相手です。本当か確かめてください"
LEVELS = ("緊急", "高", "中", "低")
HEAVY = {"体", "安全", "お金", "契約", "資格", "席", "他人を待たせる", "学業の締切"}
IRREVERSIBLE_HEAVY = {"体", "安全", "お金", "契約", "資格", "席"}
DEFAULT_TRAVEL_MIN = 0  # 移動が書かれていないとき。行動の締切は「遅くともこの時刻」になるので推定の印を付ける
DEFAULT_PREP_MIN = 0  # 準備が書かれていないとき。同上。勝手に時間を足して締切を早めない
_ORDER = ["今すぐ", "今日中", "朝一番", "ふつう"]


def level_from_score(score: int) -> str:
    """第19章：9点以上＝緊急／6〜8＝高／3〜5＝中／0〜2＝低。"""
    if score >= 9:
        return "緊急"
    if score >= 6:
        return "高"
    if score >= 3:
        return "中"
    return "低"


@dataclass
class Item:
    title: str
    related: bool = True  # 予定に関係あるか。False（予定なし・記録だけ）は急ぎにしない。本来は urgency にかけない
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
    importance: str | None = None  # 第19章のレベル（緊急・高・中・低）。点より優先
    importance_score: int | None = None  # 第19章の点。importance が無いときにレベルへ直す
    cannot_wait: bool = False  # 第19章の「待てない」を予定ばんが判断したとき
    # 第18章 過ぎた予定の(a)(b)を表す入力（開始・期限がもう過ぎているときだけ使う）
    end: datetime | None = None  # 終わりの時刻。今が終わりより前なら「途中から出られる」(a)①
    late_ok: bool = False  # 文面に「遅れても受け付ける・連絡すれば間に合う」とある (a)②
    preventable: bool = False  # 期限後の費用・失効を防げる手続きがあると文面から読める (a)③
    recover_by: datetime | None = None  # いま動けば挽回・軽減できる期限 (b)
    night_now: bool | None = None  # 補正表の「夜の即時」（True＝する／False＝しない）。夜は鳴らさないの人には True は効かない


@dataclass
class Context:
    now: datetime
    next_summary: datetime  # 次の朝のまとめの時刻
    quiet_start_hour: int = 22
    quiet_end_hour: int = 7
    night_setting: str = "urgent_only"  # urgent_only（急ぎだけ夜に知らせる：待てない緊急だけ夜に1回） / safety_only（夜は鳴らさない：夜に鳴るのは(c)だけ）
    remind: str = "day_once"  # day_once / none
    notify_type: int = 3  # 第20章の通知の型（1〜6。未設定の既定は3）
    next_delivery: datetime | None = None  # 型2・3の昼の次の配信時刻（無ければ記憶の値で配る）

    def is_quiet(self, t: datetime | None = None) -> bool:
        h = (t or self.now).astimezone(JST).hour
        if self.quiet_start_hour > self.quiet_end_hour:
            return h >= self.quiet_start_hour or h < self.quiet_end_hour
        return self.quiet_start_hour <= h < self.quiet_end_hour


@dataclass
class Decision:
    tier: str
    deliver: str  # now / next_delivery / morning_top / morning / log
    reasons: list
    action_deadline: datetime | None = None
    estimated: bool = False
    remind_at: datetime | None = None
    slack_min: int | None = None  # 余裕 S（行動の締切 − 次の朝のまとめ）の分
    level: str | None = None  # 使った第19章のレベル
    assumed: dict = field(default_factory=dict)  # 既定で埋めた所要
    notes: list = field(default_factory=list)  # 知らせに添える1行（登録外の警告など）


def _down(tier: str) -> str:
    if tier in _ORDER and tier != "ふつう":
        return _ORDER[_ORDER.index(tier) + 1]
    return tier


def _higher(a: str, b: str) -> str:
    return a if _ORDER.index(a) <= _ORDER.index(b) else b


def _level(item: Item) -> str | None:
    if item.importance is not None:
        if item.importance not in LEVELS:
            raise CalcError(f"importance は {'・'.join(LEVELS)} のどれかにしてください")
        return item.importance
    if item.importance_score is not None:
        if item.importance_score < 0:
            raise CalcError("importance_score が負です")
        return level_from_score(item.importance_score)
    return None


def _validate(item: Item, ctx: Context) -> None:
    if ctx.next_summary <= ctx.now:
        raise CalcError("次の朝のまとめの時刻が今以前です")
    if ctx.notify_type not in range(1, 7):
        raise CalcError("notify_type は1〜6にしてください")
    if ctx.night_setting not in ("urgent_only", "safety_only"):
        raise CalcError("night_setting は urgent_only か safety_only にしてください")
    for k in ("travel_min", "prep_min"):
        v = getattr(item, k)
        if v is not None and v < 0:
            raise CalcError("所要が負です")


def decide(item: Item, ctx: Context) -> Decision:
    _validate(item, ctx)
    level = _level(item)
    notes: list[str] = []
    if not item.registered_source:
        notes.append(UNKNOWN_SENDER_NOTE)
    # 1. 家族・近い人の急変（正本(c)）。どの設定でも、夜も1回。時刻が無くても、何時間前の連絡でも
    if item.family_emergency:
        return Decision("今すぐ", "now", ["家族・近い人の急変（正本の(c)）"], level=level, notes=notes)
    # 2. 本人の行動が要るか
    if not item.related:
        return Decision("急ぎにしない", "log", ["予定なし（記録だけ。urgency にかけるものではない）"], level=level)
    if not item.for_owner:
        return Decision("急ぎにしない", "log", ["本人あてではない"], level=level)
    if item.already_known:
        return Decision("急ぎにしない", "log", ["本人がもう知っている"], level=level)
    reasons: list[str] = []
    body_cannot_wait = False
    if item.body_safety:
        if item.registered_source:
            # 第19章「待てない」：信頼する出どころ・家族ロスターの人からの体調の急変・事故など
            body_cannot_wait = True
            reasons.append("信頼する出どころからの体・安全の連絡（第19章の待てない）")
        else:
            reasons.append("体・安全の連絡だが登録外なので、待てないにはしない（第19章の守り(1)）")
    elif not item.needs_action:
        return Decision("急ぎにしない", "log", ["本人がすることが無い"], level=level)
    deadline = None
    est = False
    assumed: dict = {}
    slack_min = None
    # 3. 時刻からの段階
    if item.time is None:
        t_tier = "朝一番" if item.body_safety else "ふつう"
        reasons.append("時刻が無い（保留）")
    else:
        if item.travel_min is None:
            assumed["travel_min"] = DEFAULT_TRAVEL_MIN
        if item.prep_min is None:
            assumed["prep_min"] = DEFAULT_PREP_MIN
        deadline, _ = latest_action_time(item.time, item.travel_min if item.travel_min is not None else DEFAULT_TRAVEL_MIN,
                                         item.prep_min if item.prep_min is not None else DEFAULT_PREP_MIN)
        if assumed:
            est = True
            names = "・".join({"travel_min": "移動", "prep_min": "準備"}[k] for k in assumed)
            reasons.append(f"{names}の時間が書かれていないので0分で計算した（推定。行動の締切はこれより早くなりうる）")
        if item.time <= ctx.now:
            return _past(item, ctx, level, deadline, est, assumed, notes)
        slack = deadline - ctx.next_summary
        slack_min = int(slack.total_seconds() // 60)
        from_now = deadline - ctx.now
        if timedelta(0) <= slack <= timedelta(hours=1):
            t_tier = "今すぐ"
            reasons.append(f"朝のまとめまで待つと、行動の締切まで{slack_min}分しか残らない")
        elif slack < timedelta(0):
            if from_now <= timedelta(hours=1):
                t_tier = "今すぐ"
                reasons.append("行動の締切まで1時間以内")
            else:
                t_tier = "今日中"
                reasons.append("行動の締切が次の朝のまとめより前")
        elif slack < timedelta(hours=2):
            t_tier = "今日中"
            reasons.append("朝のまとめ＋2時間より前に動く必要がある")
        elif item.irreversible and (item.impact & IRREVERSIBLE_HEAVY) and from_now <= timedelta(hours=24):
            t_tier = "今日中"
            reasons.append("遅れるほど取り返せなくなる重いもの")
        elif deadline < ctx.next_summary + timedelta(days=1):
            t_tier = "朝一番"
            reasons.append("朝のまとめで間に合うが、その日のうちに動く")
        else:
            t_tier = "ふつう"
            reasons.append("余裕が十分")
        # 確かさで調整（時刻からの段階だけ。信頼する出どころの体・安全は下で今すぐにする）
        if not item.registered_source and not item.time_in_text:
            if t_tier in ("今すぐ", "今日中"):
                reasons.append("登録外で時刻が文面に無いので急ぎにしない（正本第19章の守り）")
                t_tier = "朝一番"
        elif not item.registered_source or not item.time_in_text:
            if t_tier in ("今すぐ", "今日中", "朝一番"):
                reasons.append("確かさが低いので1段下げた")
                t_tier = _down(t_tier)
    # 4. 第19章の重要度からの段階（高い方を採る）
    cannot_wait = item.cannot_wait or body_cannot_wait
    if cannot_wait and not item.registered_source and not item.time_in_text:
        cannot_wait = False
        reasons.append("登録外で時刻が文面に無いので「待てない」にしない（第19章の守り(1)）")
    i_tier = None
    if level == "緊急" or cannot_wait:
        i_tier = "今すぐ"
    elif level == "高":
        i_tier = "今日中"
    elif level == "中" and ctx.notify_type in (1, 2, 3, 5, 6):
        i_tier = "今日中"
    tier = t_tier
    from_importance = False
    if i_tier and _higher(i_tier, t_tier) != t_tier:
        tier = i_tier
        from_importance = True
        reasons.append(f"第19章の重要度（{level or '待てない'}{'・待てない' if cannot_wait and level else ''}）が時刻の段階より高いので採った")
    # 5. 知らせ方（段階と必ず対応させる）
    tier, deliver = _deliver(tier, from_importance, level, cannot_wait, slack_min, item, ctx, reasons)
    remind = None
    if tier == "今すぐ" and ctx.remind == "day_once" and not item.light and deadline is not None:
        r = deadline - timedelta(minutes=15)
        if r > ctx.now + timedelta(minutes=5) and not ctx.is_quiet(r):
            remind = r
    if deliver not in TIER_DELIVER[tier]:
        raise CalcError(f"段階と知らせ方が合いません: {tier} / {deliver}")
    if deliver == "now" and ctx.is_quiet() and not item.registered_source:
        notes.append("信頼する出どころに入れる？を1回聞く（第19章の守り(2)）")
    return Decision(tier, deliver, reasons, deadline, est, remind, slack_min, level, assumed, notes)


def _past(item: Item, ctx: Context, level, deadline, est, assumed, notes) -> Decision:
    """第18章 過ぎた予定。(a)(b)だけ「過ぎた予定の急ぎ」としてすぐ1回。夜は設定に従う。それ以外は朝のまとめの先頭。"""
    reasons = ["見つけたときには過ぎていた（正本第18章）"]
    kinds = []
    if item.time_in_text:
        mid = level in ("緊急", "高", "中")
        high = level in ("緊急", "高")
        if mid and item.end is not None and ctx.now < item.end:
            kinds.append("(a)① 開始は過ぎたが終わりはまだ（途中から出られる）")
        if mid and item.late_ok:
            kinds.append("(a)② 遅れても受け付ける・連絡すれば間に合うと文面にある")
        if mid and item.preventable:
            kinds.append("(a)③ 期限後でも費用・失効を防げる手続きがある")
        if high and item.recover_by is not None and ctx.now < item.recover_by <= ctx.now + timedelta(hours=PAST_B_HOURS):
            kinds.append("(b) いますぐ動かないと挽回・軽減できない")
    else:
        reasons.append("開始の時刻が文面に無いので(a)(b)は当てない（第18章）")
    if not kinds:
        return Decision("過ぎていた", "morning_top", reasons, deadline, est, level=level, assumed=assumed, notes=notes)
    reasons += kinds
    if ctx.is_quiet():
        if ctx.night_setting == "safety_only" or item.night_now is False:
            reasons.append("夜は鳴らさない設定（または補正で夜の即時：しない）なので朝のまとめの先頭に回す（第18章）")
            return Decision("過ぎていた", "morning_top", reasons, deadline, est, level=level, assumed=assumed, notes=notes)
        reasons.append("急ぎだけ夜に知らせる設定なので、夜でも1回（第18章）")
        if not item.registered_source:
            notes.append("信頼する出どころに入れる？を1回聞く（第19章の守り(2)）")
    return Decision("過ぎた予定の急ぎ", "now", reasons, deadline, est, level=level, assumed=assumed, notes=notes)


def _deliver(tier, from_importance, level, cannot_wait, slack_min, item, ctx, reasons):
    if tier == "ふつう":
        return tier, "morning"
    if tier == "朝一番":
        return tier, "morning_top"
    quiet = ctx.is_quiet()
    # 夜：第19章。urgent_only は「待てない」緊急だけ夜に1回。safety_only は(c)だけで、(c)は上で返している
    night_cannot_wait = cannot_wait or (slack_min is not None and slack_min < 120)
    if quiet:
        if ctx.night_setting == "urgent_only" and item.night_now is False:
            reasons.append("補正で「夜の即時：しない」なので朝のまとめの先頭に回す")
            return "朝一番", "morning_top"
        if ctx.night_setting == "urgent_only" and item.night_now is True:
            reasons.append("補正で「夜の即時：する」なので夜でも1回")
            return tier, "now"
        if ctx.night_setting == "urgent_only" and night_cannot_wait:
            if tier == "今日中":
                reasons.append("夜は次に届くのが朝のまとめで、余裕が2時間未満（第19章の待てない）なので夜でも1回")
            return tier, "now"
        reasons.append("夜は設定により朝のまとめの先頭に回す" if ctx.night_setting == "safety_only"
                       else "夜で、朝まで待てるので朝のまとめの先頭に回す（第19章）")
        return "朝一番", "morning_top"
    # 昼：第20章の型
    if tier == "今すぐ":  # 時刻から・緊急・待てない のどれか。どの型でも即時
        return tier, "now"
    # 今日中
    if not from_importance:  # 時刻から：朝のまとめより前に動く必要がある＝待てない
        return tier, "now"
    if ctx.notify_type == 4:
        reasons.append("通知の型4は即時を緊急だけにするので朝のまとめの先頭に回す")
        return "朝一番", "morning_top"
    if ctx.notify_type == 2 or (level == "中" and ctx.notify_type in (3, 5, 6)):
        reasons.append("通知の型により次の配信時刻に回す（第20章）")
        return tier, "next_delivery"
    return tier, "now"


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
