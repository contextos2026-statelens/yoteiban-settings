"""家用の予定の見分け（正本第22章）を道具でする。名前の照合はここで行い、最終判断は人（本人の答え）。

設定（記憶から渡す。Git には例だけ）：
  self_names：本人の呼ばれ方の一覧／roster：家族ロスターの呼び名の一覧／table：見分けの表 [{"word","class"}]
  （class は self・all・other）／learned：例外表の家用パターン [{"word","class"}]／self_emails：本人のアドレス。
  self_name_rules / other_name_rules：短い呼ばれ方 [{"form","class"}]。件名の先頭または区切りのあとだけ。
  skip_copy：本人が写さないと決めた予定 [{"start","title_contains"}]。
区分：self（本人）・all（全員）・other（本人以外）・unknown（わからない＝空きとして数える）。
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from .calc import Event, is_duplicate, norm_text

ALL_WORDS = ("全員", "みんな", "家族みんな", "家族で", "家族")
ESCORT_WORDS = ("送迎", "送り", "迎え", "付き添い", "引率", "同伴", "当番")
_HON = re.compile(r"(さん|ちゃん|くん|君|様|さま)")
_DELIM = re.compile(r"[\s　、,，。．.・:：;；/／|｜\-\—\-ー=＝()（）\[\]【】「」『』<>〈〉《》]")
_HIRA = re.compile(r"[\u3040-\u309F]")
NEAR_START_MINUTES = 10


def _n(t: str) -> str:
    return _HON.sub("", norm_text(t))


def _has(text: str, word: str) -> bool:
    w = _n(word)
    return bool(w) and w in _n(text)


def name_at_boundary(text: str, form: str) -> bool:
    """短い呼ばれ方が、件名の先頭または区切りのあとにあり、そのあとが区切り・末尾、またはひらがな以外か。"""
    if not form or not text:
        return False
    sources = [text]
    nfkc = unicodedata.normalize("NFKC", text)
    if nfkc != text:
        sources.append(nfkc)
    n = len(form)
    for src in sources:
        start = 0
        while True:
            i = src.find(form, start)
            if i < 0:
                break
            before_ok = i == 0 or _DELIM.match(src[i - 1])
            after = src[i + n:i + n + 1]
            after_ok = after == "" or bool(_DELIM.match(after)) or not bool(_HIRA.match(after))
            if before_ok and after_ok:
                return True
            start = i + 1
    return False


def _rule_hit(text: str, rules) -> bool:
    for row in rules or []:
        if not isinstance(row, dict):
            continue
        form = row.get("form")
        if isinstance(form, str) and form and name_at_boundary(text, form):
            return True
    return False


def classify(title: str, memo: str = "", attendees: list | None = None, cfg: dict | None = None,
             personal: list[Event] | None = None, ev: Event | None = None) -> tuple[str, str]:
    """(区分, 使った手がかりの種類) を返す。優先の順は第22章の(0)〜(6)。"""
    cfg = cfg or {}
    text = f"{title or ''} {memo or ''}"
    self_names = [x for x in cfg.get("self_names", []) if x]
    roster = [x for x in cfg.get("roster", []) if x and x not in self_names]
    all_words = cfg.get("all_words") or ALL_WORDS
    # (0) 見分けの表（いちばん優先）
    for row in cfg.get("table", []):
        if _has(text, row.get("word", "")):
            return row["class"], "見分けの表"
    has_self = (
        any(_has(text, n) for n in self_names)
        or any(a in (cfg.get("self_emails") or []) for a in (attendees or []))
        or _rule_hit(text, cfg.get("self_name_rules"))
    )
    has_all = any(_has(text, w) for w in all_words)
    # (1) 学習したパターン。ただし本人の呼ばれ方・全員の語があれば埋まる側
    for row in cfg.get("learned", []):
        if _has(text, row.get("word", "")):
            if has_self:
                return "self", "本人の呼ばれ方（学習より優先）"
            if has_all:
                return "all", "全員の語（学習より優先）"
            return row["class"], "例外表の家用パターン"
    if has_self:
        return "self", "本人の呼ばれ方"
    if has_all:
        return "all", "全員の語"
    # (4) 個人用に同じ日時・同じ件名
    if ev is not None and personal and any(is_duplicate(ev, p) for p in personal):
        return "self", "個人用に同じ予定"
    if any(_has(text, n) for n in roster) or _rule_hit(text, cfg.get("other_name_rules")):
        return "other", "他の人の名前だけ"
    return "unknown", "手がかりなし"


def is_escort(title: str, memo: str = "") -> bool:
    """本人以外の予定でも送迎・付き添いの語があるもの（空きのまま「あなたが動きますか？」を1回聞く）。"""
    t = f"{title or ''} {memo or ''}"
    return any(w in t for w in ESCORT_WORDS)


def already_on_personal(ev: Event, personal: list[Event] | None, slack_minutes: int = NEAR_START_MINUTES) -> bool:
    """個人用に、開始が同じ、または約 slack 分以内のずれの予定があるか。件名は見ない（写しの道専用）。"""
    if ev.all_day or not personal:
        return False
    slack = timedelta(minutes=slack_minutes)
    for other in personal:
        if other.all_day:
            continue
        if abs(ev.start - other.start) <= slack:
            return True
    return False


def _parse_skip_start(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def skip_copy(ev: Event, cfg: dict | None = None) -> bool:
    """本人が写さないと決めた予定か（記憶の例外。開始と件名の一部で照合）。"""
    cfg = cfg or {}
    for row in cfg.get("skip_copy") or []:
        if not isinstance(row, dict):
            continue
        start_text = row.get("start")
        if isinstance(start_text, str) and start_text:
            start = _parse_skip_start(start_text)
            if start is None or ev.start != start:
                continue
        needle = row.get("title_contains")
        if isinstance(needle, str) and needle and needle not in (ev.title or ""):
            continue
        if not start_text and not needle:
            continue
        return True
    return False


def has_all_word(title: str, memo: str = "", cfg: dict | None = None) -> bool:
    cfg = cfg or {}
    text = f"{title or ''} {memo or ''}"
    return any(_has(text, w) for w in (cfg.get("all_words") or ALL_WORDS))


def copy_candidates(events: list[Event], personal: list[Event] | None, cfg: dict | None = None,
                    now: datetime | None = None) -> list[Event]:
    """個人用へ［自動］で写してよい家用・ファミリーの本人の予定。終日・ダブり・全員の語・写さない例外は除く。"""
    cfg = cfg or {}
    slack = cfg.get("near_start_minutes", NEAR_START_MINUTES)
    if not isinstance(slack, int) or isinstance(slack, bool) or slack < 0:
        slack = NEAR_START_MINUTES
    out: list[Event] = []
    for ev in events:
        if ev.all_day:
            continue
        if now is not None and ev.start < now:
            continue
        if ev.calendar not in ("household", "family"):
            continue
        memo = ""
        if isinstance(ev.meta, dict):
            memo = ev.meta.get("memo") or ev.meta.get("description") or ""
        cls, _why = classify(ev.title, memo, cfg=cfg, personal=personal, ev=ev)
        if cls != "self":
            continue
        if has_all_word(ev.title, memo, cfg):
            continue
        if already_on_personal(ev, personal, slack):
            continue
        if skip_copy(ev, cfg):
            continue
        out.append(ev)
    return out


def load_classify_config(path: str | Path) -> dict:
    """箱の見分け用設定を読む。Git の正本からは呼ばない。"""
    with Path(path).open(encoding="utf-8") as fh:
        value = json.load(fh)
    if not isinstance(value, dict):
        raise ValueError("見分け用設定はJSONオブジェクトである必要があります")
    return value
