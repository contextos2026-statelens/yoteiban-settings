"""家用の予定の見分け（正本第22章）を道具でする。名前の照合はここで行い、最終判断は人（本人の答え）。

設定（記憶から渡す。Git には例だけ）：
  self_names：本人の呼ばれ方の一覧／roster：家族ロスターの呼び名の一覧／table：見分けの表 [{"word","class"}]
  （class は self・all・other）／learned：例外表の家用パターン [{"word","class"}]／self_emails：本人のアドレス。
区分：self（本人）・all（全員）・other（本人以外）・unknown（わからない＝空きとして数える）。
"""
from __future__ import annotations

import re

from .calc import Event, is_duplicate, norm_text

ALL_WORDS = ("全員", "みんな", "家族みんな", "家族で", "家族")
ESCORT_WORDS = ("送迎", "送り", "迎え", "付き添い", "引率", "同伴", "当番")
_HON = re.compile(r"(さん|ちゃん|くん|君|様|さま)")


def _n(t: str) -> str:
    return _HON.sub("", norm_text(t))


def _has(text: str, word: str) -> bool:
    w = _n(word)
    return bool(w) and w in _n(text)


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
    has_self = any(_has(text, n) for n in self_names) or any(
        a in (cfg.get("self_emails") or []) for a in (attendees or []))
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
    if any(_has(text, n) for n in roster):
        return "other", "他の人の名前だけ"
    return "unknown", "手がかりなし"


def is_escort(title: str, memo: str = "") -> bool:
    """本人以外の予定でも送迎・付き添いの語があるもの（空きのまま「あなたが動きますか？」を1回聞く）。"""
    t = f"{title or ''} {memo or ''}"
    return any(w in t for w in ESCORT_WORDS)
