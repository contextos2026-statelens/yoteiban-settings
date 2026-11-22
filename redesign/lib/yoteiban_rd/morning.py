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
from itertools import combinations

from .calc import (JST, Event, event_from_google, fmt_md,
                   relate_events, RELATION_CONFLICT)
from .household import classify, is_escort
from .message_check import validate_morning_send

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


def _line(ev: Event, labels: list[str] | None = None) -> str:
    if ev.all_day:
        when = "終日"
    else:
        s, e = ev.start.astimezone(JST), ev.end.astimezone(JST)
        when = f"{s:%H:%M}〜{e:%H:%M}" if e > s else f"{s:%H:%M}"
    where = f" ＠{ev.location}" if ev.location else ""
    source = f"（{'・'.join(labels)}）" if labels else ""
    return f"・{when} {ev.title}{where}{source}"


def _weather_text(weather) -> tuple[str, str]:
    if isinstance(weather, dict):
        value = str(weather.get("value", "")).strip()
        method = str(weather.get("source", "unavailable"))
        if not value or method not in ("settings_location", "web"):
            return "天気は未確認", "unavailable"
        return value + ("（Web）" if method == "web" else ""), method
    values = [str(value).strip() for value in (weather or []) if str(value).strip()]
    if not values or any("未確認" in value for value in values):
        return "天気は未確認", "unavailable"
    return "、".join(values), "settings"


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
        text = str(value.get("text", "") if isinstance(value, dict) else value).strip()
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
            for event in evs:
                if event.owner == "unknown":
                    relations = [relate_events(event, p) for p in personal]
                    if any(r != "別件" for r in relations):
                        event.owner = "self"
                        event.meta["relation_conflict"] = RELATION_CONFLICT in relations
            by_role[s.role] += evs
    today = lambda e: e.end > d0 and e.start < d1 if not e.all_day else (e.start <= d0 < e.end or e.start == d0)
    plan: list[tuple[Event, list[str]]] = []
    for e in sorted([e for e in personal if today(e)], key=lambda e: e.start):
        plan.append((e, [e.meta.get("source_label", "個人用カレンダー")]))
    unknown, excluded, cards = [], [], []
    conflicts = {}
    conflicted_household_ids = set()
    for h in by_role["household"] + by_role["family"]:
        if not today(h):
            continue
        for p in personal:
            if today(p) and relate_events(h, p) == RELATION_CONFLICT:
                conflicts.setdefault(id(h), (h, []))[1].append(p)
                conflicted_household_ids.add(id(h))
    for e in sorted(by_role["household"] + by_role["family"], key=lambda e: e.start):
        if not today(e):
            continue
        if id(e) in conflicted_household_ids:
            continue
        if e.owner in ("self", "all"):
            same = next((p for p in plan if relate_events(p[0], e) == "同一"), None)
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
    household_ids = {id(e) for e in by_role["household"] + by_role["family"]}
    personal_ids = {id(e) for e in personal}
    def plan_sources(event, labels):
        return labels if id(event) in household_ids and id(event) not in personal_ids else []
    owner_card_used = False
    check_lines = []
    check_values = {}
    conflict_rows = {}
    for household_event, personal_events in conflicts.values():
        hs = f"{household_event.start.astimezone(JST):%H:%M}〜{household_event.end.astimezone(JST):%H:%M}"
        personal_times = sorted({f"{p.start.astimezone(JST):%H:%M}〜{p.end.astimezone(JST):%H:%M}" for p in personal_events})
        place = ""
        if any(p.location and household_event.location and p.location != household_event.location for p in personal_events):
            personal_places = "、".join(sorted({p.location for p in personal_events if p.location}))
            place = f"、場所 家用 {household_event.location}／個人用 {personal_places}"
        household_source = household_event.meta.get("source_label", "家用カレンダー")
        line = f"・食い違い：{household_event.title}（{household_source} {hs}／個人用カレンダー {'、'.join(personal_times)}{place}）"
        check_lines.append(line)
        check_values[line] = {"household": {"title": household_event.title, "start": household_event.start.isoformat(), "end": household_event.end.isoformat(), "location": household_event.location, "source": household_event.meta.get("source_label")},
                              "personal": [{"title": p.title, "start": p.start.isoformat(), "end": p.end.isoformat(), "location": p.location, "source": "個人用カレンダー"} for p in personal_events]}
        conflict_rows[id(household_event)] = (household_event, personal_events, line)
    for e in unknown:
        line = _line(e, [e.meta["source_label"]])[:-1] + "：誰の予定か未確認）"
        check_lines.append(line)
        check_values[line] = {"title": e.title, "start": e.start.isoformat(), "end": e.end.isoformat(), "location": e.location, "source": e.meta.get("source_label")}
        if not owner_card_used:
            cards.insert(0, {"question": f"「{e.title}」（{fmt_md(e.start)}）は誰の予定ですか？",
                             "options": ["自分の予定", "家族全員の予定", "自分以外の予定"],
                             "why": "家用の予定の見分け（第22章。1日1件まで）"})
            owner_card_used = True
    for c in confirm or []:
        text = str(c.get("text", "")).strip()
        source = str(c.get("source", "")).strip()
        if text and _visible_notices([text]):
            line = f"・{text}（{source}）" if source else f"・{text}"
            check_lines.append(line)
            check_values[line] = c.get("values", {"text": text, "source": source})
    for l in cov["lines"]:
        line = "・" + l
        check_lines.append(line)
        check_values[line] = {"source": next((s.label for s in sources if s.label in l), "カレンダー"), "status": "未確認"}
    weather_summary, weather_source = _weather_text(weather)
    rain = _rain_expected(weather_summary)
    weather_line = "・天気は未確認" if weather_source == "unavailable" else f"・天気：{weather_summary}"
    plan_lines = [weather_line]
    for event, labels in plan:
        plan_lines.append(_line(event, plan_sources(event, labels)))
        if rain and _outdoor_event(event):
            plan_lines.append("　↳ 傘")
    visible_notices = _visible_notices([x for x in notices or [] if isinstance(x, dict) and x.get("values")])
    parts = ["📅予定\n" + "\n".join(plan_lines)]
    if check_lines:
        parts.append("✅確認してね\n" + "\n".join(check_lines))
    if visible_notices:
        parts.append("\n".join(visible_notices))
    if not plan and not check_lines and not visible_notices and cov["can_say_none"]:
        body = "今日の知らせはありません" + (f"（天気：{weather_summary}）" if weather_source != "unavailable" else "（天気は未確認）")
    elif not plan and not check_lines and not visible_notices:
        body = "\n\n".join(parts + ["✅確認してね\n" + "\n".join("・" + l for l in cov["lines"])])
    else:
        body = "\n\n".join(parts)
    items = []
    for event, labels in plan:
        # A source is shown for household-only events; personal-only and copies stay quiet.
        show_labels = plan_sources(event, labels)
        items.append({"type": "予定", "text": _line(event, show_labels),
                      "values": {"id": event.id, "title": event.title, "start": event.start.isoformat(), "end": event.end.isoformat(),
                                 "location": event.location, "calendar": event.calendar, "owner": event.owner, "sources": list(labels)}})
        if rain and _outdoor_event(event):
            items.append({"type": "予定", "text": "　↳ 傘", "values": {"event": event.title, "weather": weather_summary}})
    weather_item_text = body if body.startswith("今日の知らせはありません") else weather_line
    items.append({"type": "確認すること", "text": weather_item_text,
                  "values": {"weather": weather_summary, "source": weather_source}})
    for h, ps, line in conflict_rows.values():
        items.append({"type": "食い違い", "text": line,
                      "values": check_values[line]})
    for text in check_lines:
        if "食い違い" not in text:
            items.append({"type": "取れなかったもの" if "未確認" in text else "確認すること", "text": text,
                          "values": check_values.get(text, {"text": text})})
    for card in cards:
        items.append({"type": "カードで聞くこと", "text": card["question"], "values": dict(card)})
    for text in visible_notices:
        source_record = next(x for x in notices if isinstance(x, dict) and x.get("text") == text)
        items.append({"type": "確認すること", "text": text, "values": source_record["values"], "source": source_record.get("source")})
    ledger_items = [{"type": "記録だけ", "reason": "同一の写し" if relate_events(a, b) == "同一" else "包含関係",
                     "values": [{"id": a.id, "title": a.title, "start": a.start.isoformat(), "end": a.end.isoformat(), "location": a.location, "source": a.meta.get("source_label")},
                                {"id": b.id, "title": b.title, "start": b.start.isoformat(), "end": b.end.isoformat(), "location": b.location, "source": b.meta.get("source_label")}]} \
                    for a, b in combinations(personal + by_role["household"] + by_role["family"], 2)
                    if relate_events(a, b) in ("同一", "包含")]
    send_check = validate_morning_send(body, items, ledger_items)
    return {"body": body, "items": items, "ledger_items": ledger_items, "send_check": send_check,
            "cards": cards, "coverage": cov,
            "plan": [{"title": e.title, "start": e.start.isoformat(), "sources": labels} for e, labels in plan],
            "unknown": [e.title for e in unknown], "excluded_count": len(excluded)}
