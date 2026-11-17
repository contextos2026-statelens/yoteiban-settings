"""外部AIに渡す前の伏せ字。伝書鳩より狭く：本文と送り主の役割だけを渡し、
電話番号・メールアドレス・URL・郵便番号・長い番号（注文・口座・カード）・住所らしき部分・登録した名前を伏せる。"""
from __future__ import annotations

import re

_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "（メール）"),
    (re.compile(r"https?://\S+"), "（リンク）"),
    (re.compile(r"\+?\d[\d\- ]{8,}\d"), "（番号）"),  # 電話は郵便番号より先に伏せる
    (re.compile(r"〒?\s?\d{3}-\d{4}"), "（郵便番号）"),
    (re.compile(r"\d{6,}"), "（番号）"),
    (re.compile(r"[一-龥ぁ-んァ-ヶ]{1,6}[都道府県市区町村]\S{0,12}?\d+(?:[-−ー丁目番地号]\d*)+"), "（住所）"),
    (re.compile(r"\d+丁目\d+(?:[-−]\d+)*|\d+-\d+-\d+"), "（住所）"),
]


def mask(text: str, names: dict | None = None) -> str:
    """names: {表示名や本名: 役割} の辞書（設定から渡す）。長い名前から先に置き換える。"""
    if not text:
        return ""
    t = str(text)
    for name in sorted((names or {}), key=len, reverse=True):
        if name:
            t = t.replace(name, "（" + names[name] + "）")
    for pat, rep in _PATTERNS:
        t = pat.sub(rep, t)
    return t[:2000]
