"""読む先の一覧と「読めた」の記録、朝のまとめの今日の予定。

- 読む先の一覧（記憶の『読む先の一覧』）：個人用カレンダー・家用カレンダー（primary）・ファミリーカレンダーなど。
  1つずつ「読めた（件数・時刻）／読めない（理由）」を残す。全部読めていなければ「予定なし」「新着なし」と書かない（正本25章）。
- 今日の予定：個人用＋家用・ファミリーで本人・全員に分けたもの（第20・22章）。本人以外は載せない（記録だけ）。
  わからないは「✅確認してね」に回し、誰の予定かを聞くカードは1日1枚まで（第22章）。
- 朝のまとめの書き方（2026-11-14・11-15 本人承認）：見出し（🌤天気・📅予定・✅確認してね）、見出しのあいだは1行あける、
  1件1行（日時・件名・場所・出どころだけ）、件名は元のまま、無かったものは書かない、問いは本文に書かずカードで聞く、
  「✅確認してね」には出どころを書く。何も載せるものが無い日（全部読めたときだけ）は「今日の知らせはありません」。
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
        check_lines.append(f"・{c['text']}（{c['source']}）")
    for l in cov["lines"]:
        check_lines.append("・" + l)
    parts = []
    if weather:
        parts.append("🌤天気\n" + "\n".join(weather[:2]))
    if plan:
        parts.append("📅予定\n" + "\n".join(_line(e, labels) for e, labels in plan))
    if check_lines:
        parts.append("✅確認してね\n" + "\n".join(check_lines))
    if notices:
        parts.append("".join(notices[:2]))
    if not plan and not check_lines and not notices:
        body = "今日の知らせはありません" if cov["can_say_none"] else "✅確認してね\n" + "\n".join("・" + l for l in cov["lines"])
        if weather:
            body = parts[0] + "\n\n" + body
    else:
        body = "\n\n".join(parts)
    return {"body": body, "cards": cards, "coverage": cov,
            "plan": [{"title": e.title, "start": e.start.isoformat(), "sources": labels} for e, labels in plan],
            "unknown": [e.title for e in unknown], "excluded_count": len(excluded)}
