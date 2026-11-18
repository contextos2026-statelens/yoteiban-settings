"""外部AIに渡す前の伏せ字。伝書鳩より狭く：本文と送り主の役割だけを渡し、
電話番号・メールアドレス・URL・郵便番号・長い番号（注文・口座・カード）・住所らしき部分・
登録した名前・登録していない人の名前（〇〇先生・〇〇さん・〇〇くん・〇〇ちゃん・〇〇様・〇〇氏 など）を伏せる。
日付と時刻は伏せない（外部AIが急ぎを判断する材料なので。日付だけでは人を特定できない）。"""
from __future__ import annotations

import re

# 日付・時刻（先に退避して、番号の伏せ字に巻き込まれないようにする）
_DATE_TIME = re.compile(
    r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?(?:[ T　]?\d{1,2}[:：]\d{2}(?::\d{2})?)?"  # 2026-11-19 10:00 / 2026年11月19日
    r"|\d{1,2}[/月]\d{1,2}日?(?:\s?[(（][月火水木金土日][)）])?"  # 11/19・11月19日(木)
    r"|\d{1,2}[:：]\d{2}(?:[〜~\-－]\d{1,2}[:：]\d{2})?"  # 10:00・10:00〜11:00
    r"|\d{1,2}時(?:\d{1,2}分|半)?")
_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "（メール）"),
    (re.compile(r"https?://\S+"), "（リンク）"),
    (re.compile(r"\+?\d[\d\- ]{8,}\d"), "（番号）"),  # 電話は郵便番号より先に伏せる
    (re.compile(r"〒?\s?\d{3}-\d{4}"), "（郵便番号）"),
    (re.compile(r"\d{6,}"), "（番号）"),
    (re.compile(r"[一-龥ぁ-んァ-ヶ]{1,6}[都道府県市区町村]\S{0,12}?\d+(?:[-−ー丁目番地号]\d*)+"), "（住所）"),
    (re.compile(r"\d+丁目\d+(?:[-−]\d+)*|\d+-\d+-\d+"), "（住所）"),
]
# 登録外の人の名前：敬称・肩書きの直前の名前らしい語を（人）にする。
# 敬称の位置から前へ、同じ種類の文字（漢字・カナ・英字のまとまり、またはひらがなのまとまり）を最大8字さかのぼる。
# 前の敬称・伏せ字・句読点ではさかのぼりを止める。ひらがなの名前は、頭の助詞（と・や・の 等）を残す。
# 「お客様」「皆さん」「お父さん」「担任の先生」「おじいちゃん」など、人を特定しない語は残す。
_TITLES = sorted(["先生", "さん", "さま", "様", "くん", "君", "ちゃん", "氏", "殿", "先輩", "コーチ", "監督",
                  "部長", "課長", "社長", "師匠"], key=len, reverse=True)
_TITLE_RE = re.compile("|".join(_TITLES))
_KEEP = {"客", "皆", "父", "母", "兄", "姉", "祖父", "祖母", "医", "歯医者", "保護者", "生徒", "担任", "担当",
         "皆様", "叔父", "叔母", "伯父", "伯母", "親御", "奥", "旦那", "先方", "学校の", "教", "校長", "主治医"}
_KEEP_HIRA = ("みな", "おじい", "おばあ", "おとう", "おかあ", "おにい", "おねえ", "おきゃく", "あかち", "おこ", "おじ",
              "おば", "おくさ", "だんな", "かかりつけ", "おいしゃ")
_PARTICLES = ("から", "より", "と", "や", "の", "に", "は", "が", "も", "を", "へ", "で")
_SENTINEL = "\uE000{}\uE001"


def _kind(c: str) -> str:
    if "ぁ" <= c <= "ゖ":
        return "hira"
    if "一" <= c <= "龥" or c == "々" or "ァ" <= c <= "ヶ" or c == "ー" or c.isascii() and c.isalpha():
        return "name"
    return ""


def _mask_unregistered(t: str) -> str:
    out = []
    last = 0  # ここより前へはさかのぼらない（前の敬称の終わり）
    for m in _TITLE_RE.finditer(t):
        s0 = m.start()
        if s0 < last:
            continue
        i = s0
        k = _kind(t[i - 1]) if i > last else ""
        while i > last and _kind(t[i - 1]) == k and k and s0 - i < 8:
            i -= 1
        name = t[i:s0]
        keep_prefix = ""
        if k == "hira":
            for p in _PARTICLES:
                if name.startswith(p) and len(name) - len(p) >= 2:
                    keep_prefix, name = p, name[len(p):]
                    break
            hira_ok = len(name) >= 2 and m.group(0) in ("さん", "くん", "ちゃん") \
                and not any(name.startswith(x) for x in _KEEP_HIRA)
            # 「お父」など漢字の直後のひらがなは名前ではない
            if not hira_ok or (i > 0 and _kind(t[i - 1]) == "name"):
                name = ""
        elif k == "name":
            if name in _KEEP or any(name.endswith(x) for x in _KEEP if len(x) >= 2) or \
                    (i > 0 and t[i - 1] in "おご" and name in ("父", "母", "兄", "姉", "客", "子")):
                name = ""
            if name.startswith("お") or name.endswith("の"):
                name = ""
        if not name:
            out.append(t[last:m.end()])
        else:
            out.append(t[last:s0 - len(name)] + "（人）" + m.group(0))
        last = m.end()
    out.append(t[last:])
    return "".join(out)


def mask(text: str, names: dict | None = None) -> str:
    """names: {表示名や本名: 役割} の辞書（設定から渡す）。長い名前から先に置き換える。
    そのあと登録していない人の名前（敬称つき）を（人）に、番号・連絡先・住所を伏せる。日付・時刻は残す。"""
    if not text:
        return ""
    t = str(text)
    for name in sorted((names or {}), key=len, reverse=True):
        if name:
            t = t.replace(name, "（" + names[name] + "）")
    kept: list[str] = []

    def keep(m: re.Match) -> str:
        kept.append(m.group(0))
        return _SENTINEL.format(len(kept) - 1)
    t = _DATE_TIME.sub(keep, t)
    t = _mask_unregistered(t)
    for pat, rep in _PATTERNS:
        t = pat.sub(rep, t)
    t = re.sub("\uE000(\\d+)\uE001", lambda m: kept[int(m.group(1))], t)
    return t[:2000]
