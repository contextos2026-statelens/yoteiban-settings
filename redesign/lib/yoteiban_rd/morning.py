"""読む先の一覧と「読めた」の記録、朝のまとめの今日の予定。

- 読む先の一覧（記憶の『読む先の一覧』）：個人用カレンダー・家用カレンダー（primary）・ファミリーカレンダーなど。
  1つずつ「読めた（件数・時刻）／読めない（理由）」を残す。全部読めていなければ「予定なし」「新着なし」と書かない（正本25章）。
- 今日の予定：個人用＋家用・ファミリーで本人・全員に分けたもの（第20・22章）。本人以外は載せない（記録だけ）。
  わからないは「✅確認してね」に回し、誰の予定かを聞くカードは1日1枚まで（第22章）。
- 朝のまとめは毎回天気を確認し、📅予定の先頭に天気・最高／最低気温・雨の時間帯を1行で載せる。
  天気が読めなければ「天気は未確認」。雨の日に外出予定があれば、その行の下に「↳ 傘」を載せる。
  変化の無い入口や中身の無い欄は出さず、読めないものの「未確認」は残す。
  何も載せるものが無い日（全部読めたときだけ）は「今日の知らせはありません」の1行に、読めた天気を添える。
道具は文を作るだけで、送らない・書かない。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .calc import JST, Event, event_from_google, fmt_md, is_duplicate
from .household import classify, is_escort

ROLES = ("personal", "household", "family")


@dataclass
class Source:
    key: str
    label: str  # 本人に見せる名前（個人用カレンダー・家用カレンダー・ファミリーカレンダーなど）
    role: str  # personal / household / family
    required: bool = True


def coverage(sources: list[Source], reads: dict) -> dict:
    """reads: key → {"ok": bool, "count": int, "at": "...", "error": "..."}。一覧に無い key を読んだものは数えない。"""
    lines, unread, ok_list = [], [], []
    for s in sources:
        r = reads.get(s.key)
        if r and r.get("ok"):
            ok_list.append(s.label)
        else:
            why = (r or {}).get("error") or "読んでいない"
            if s.required:
                unread.append(s.label)
            lines.append(f"{s.label}は未確認（{why}）")
    return {"all_ok": not unread, "read": ok_list, "unread": unread, "lines": lines,
            "can_say_none": not unread}


def _events(src: Source, raw: list, cfg: dict, personal: list[Event]) -> list[Event]:
    out = []
    for g in raw or []:
        ev = g if isinstance(g, Event) else event_from_google(g, src.role, "self" if src.role == "personal" else "unknown")
        if ev is None:
            continue
        ev.calendar = src.role
        ev.meta["source_label"] = src.label
        if src.role == "personal":
            ev.owner = "self"
        else:
            memo = (g.get("description") if isinstance(g, dict) else "") or ""
            att = [a.get("email") for a in (g.get("attendees") or [])] if isinstance(g, dict) else []
            ev.owner, ev.meta["clue"] = classify(ev.title, memo, att, cfg, personal, ev)
            ev.meta["escort"] = ev.owner == "other" and is_escort(ev.title, memo)
        out.append(ev)
    return out


def _line(ev: Event, labels: list[str]) -> str:
    if ev.all_day:
        when = "終日"
    else:
        s, e = ev.start.astimezone(JST), ev.end.astimezone(JST)
        when = f"{s:%H:%M}〜{e:%H:%M}" if e > s else f"{s:%H:%M}"
    where = f" ＠{ev.location}" if ev.location else ""
    return f"・{when} {ev.title}{where}（{'・'.join(labels)}）"


def _weather_text(weather: list[str] | None) -> tuple[str, bool]:
    values = [str(value).strip() for value in (weather or []) if str(value).strip()]
    if not values or any("未確認" in value for value in values):
        return "天気は未確認", False
    return "、".join(values), True


def _rain_expected(text: str) -> bool:
    if "雨" not in text:
        return False
    no_rain = ("雨なし", "雨はなし", "雨はありません", "雨の時間帯なし", "雨の時間帯はなし",
               "雨の時間帯はありません", "降らない", "降水なし", "降水確率0%")
    return not any(marker in text for marker in no_rain)


def _outdoor_event(event: Event) -> bool:
    if event.meta.get("outdoor") is not None:
        return bool(event.meta["outdoor"])
    location = (event.location or "").strip().casefold()
    title = event.title.casefold()
    indoors_or_remote = ("自宅", "自分の家", "オンライン", "zoom", "google meet", "teams", "電話", "通話")
    if any(marker in f"{title} {location}" for marker in indoors_or_remote):
        return False
    if location:
        return True
    outdoor_terms = ("通院", "病院", "受診", "買い物", "買物", "公園", "遠足", "登校", "通学", "出勤",
                     "送迎", "迎え", "駅", "外出", "旅行", "面談", "面接", "会場", "集合", "散歩", "出発", "訪問")
    return any(term in title for term in outdoor_terms)


def _visible_notices(notices: list[str] | None) -> list[str]:
    visible = []
    for value in notices or []:
        text = str(value).strip()
        empty_headings = {"新着", "返事待ち", "夜の間の自動追加", "夜の間の［自動］追加"}
        if not text or text in empty_headings or ("未確認" not in text and (
            ("新着" in text and any(word in text for word in ("なし", "ありません")))
            or ("返事待ち" in text and any(word in text for word in ("なし", "ありません")))
            or ("夜" in text and "自動" in text and "追加" in text and any(word in text for word in ("なし", "ありません")))
            or ("家用" in text and "個人用" in text and "写" in text)
        )):
            continue
        visible.append(text)
    return visible


def build_morning(day: date, sources: list[Source], reads: dict, owner_cfg: dict | None = None,
                  weather: list[str] | None = None, confirm: list[dict] | None = None,
                  notices: list[str] | None = None) -> dict:
    """朝のまとめの本文と、本文の後にカードで聞くこと（1つずつ）を作る。
    confirm：[{"text": "...", "source": "Gmail（届いたアドレス）"}]。notices：予定ではない短い文（箇条書きにしない）。"""
    owner_cfg = owner_cfg or {}
    cov = coverage(sources, reads)
    d0 = datetime(day.year, day.month, day.day, tzinfo=JST)
    d1 = d0 + timedelta(days=1)
    by_role: dict[str, list[Event]] = {r: [] for r in ROLES}
    psrc = [s for s in sources if s.role == "personal"]
    personal: list[Event] = []
    for s in psrc:
        r = reads.get(s.key) or {}
        if r.get("ok"):
            personal += _events(s, r.get("events"), owner_cfg, [])
    for s in sources:
        r = reads.get(s.key) or {}
        if not r.get("ok"):
            continue
        evs = personal if s.role == "personal" else _events(s, r.get("events"), owner_cfg, personal)
        if s.role != "personal":
            by_role[s.role] += evs
    today = lambda e: e.end > d0 and e.start < d1 if not e.all_day else (e.start <= d0 < e.end or e.start == d0)
    plan: list[tuple[Event, list[str]]] = []
    for e in sorted([e for e in personal if today(e)], key=lambda e: e.start):
        plan.append((e, [e.meta.get("source_label", "個人用カレンダー")]))
    unknown, excluded, cards = [], [], []
    for e in sorted(by_role["household"] + by_role["family"], key=lambda e: e.start):
        if not today(e):
            continue
        if e.owner in ("self", "all"):
            same = next((p for p in plan if is_duplicate(p[0], e)), None)
            if same:
                if e.meta["source_label"] not in same[1]:
                    same[1].append(e.meta["source_label"])
            else:
                plan.append((e, [e.meta["source_label"]]))
        elif e.owner == "unknown":
            unknown.append(e)
        else:
            excluded.append({"title": e.title, "clue": e.meta.get("clue"), "escort": e.meta.get("escort", False)})
            if e.meta.get("escort"):
                cards.append({"question": f"「{e.title}」は、あなたが動きますか？", "options": ["動く", "動かない"],
                              "why": "送迎・付き添いの語（第22章）"})
    plan.sort(key=lambda p: (not p[0].all_day, p[0].start))
    owner_card_used = False
    check_lines = []
    for e in unknown:
        check_lines.append(_line(e, [e.meta["source_label"]])[:-1] + "：誰の予定か未確認）")
        if not owner_card_used:
            cards.insert(0, {"question": f"「{e.title}」（{fmt_md(e.start)}）は誰の予定ですか？",
                             "options": ["自分の予定", "家族全員の予定", "自分以外の予定"],
                             "why": "家用の予定の見分け（第22章。1日1件まで）"})
            owner_card_used = True
    for c in confirm or []:
        text = str(c.get("text", "")).strip()
        source = str(c.get("source", "")).strip()
        if text and _visible_notices([text]):
            check_lines.append(f"・{text}（{source}）" if source else f"・{text}")
    for l in cov["lines"]:
        check_lines.append("・" + l)
    weather_summary, weather_read = _weather_text(weather)
    rain = _rain_expected(weather_summary)
    plan_lines = [f"・天気：{weather_summary}"]
    for event, labels in plan:
        plan_lines.append(_line(event, labels))
        if rain and _outdoor_event(event):
            plan_lines.append("　↳ 傘")
    visible_notices = _visible_notices(notices)
    parts = ["📅予定\n" + "\n".join(plan_lines)]
    if check_lines:
        parts.append("✅確認してね\n" + "\n".join(check_lines))
    if visible_notices:
        parts.append("\n".join(visible_notices[:2]))
    if not plan and not check_lines and not visible_notices and cov["can_say_none"]:
        weather_suffix = f"（天気：{weather_summary}）" if weather_read else "（天気は未確認）"
        body = "今日の知らせはありません" + weather_suffix
    elif not plan and not check_lines and not visible_notices:
        body = "\n\n".join(parts + ["✅確認してね\n" + "\n".join("・" + l for l in cov["lines"])])
    else:
        body = "\n\n".join(parts)
    return {"body": body, "cards": cards, "coverage": cov,
            "plan": [{"title": e.title, "start": e.start.isoformat(), "sources": labels} for e, labels in plan],
            "unknown": [e.title for e in unknown], "excluded_count": len(excluded)}
