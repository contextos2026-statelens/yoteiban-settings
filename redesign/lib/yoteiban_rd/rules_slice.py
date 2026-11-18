"""決まりを軽くする：仕事ごとに、正本の必要な章だけを切り出す。

正本（Git の rules/seihon.md、箱の写しは /home/box/agent-data/yoteiban-rules/seihon.md）は1つのまま。
ここは章番号の地図だけを持ち、本文はその都度正本から切り出す（二重管理しない）。
- 仕事の章（TASK_CHAPTERS）＋場面で足す章（--add）を出す。
- 切り出せないとき（章が見つからない・正本が読めない・知らない仕事）は止めずに、正本の全文を出して終了コード3を返す
  （予定ばんは全文を読んで続け、本人に1行で報告する）。
- uncovered_refs：スキル本文が参照する章のうち、仕事の章にも本文の「--add」にも無いもの（取りこぼしの照合）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_SEIHON = "/home/box/agent-data/yoteiban-rules/seihon.md"

# 仕事 → 正本の章番号。2（大原則）はいつも入れる。場面で要る章はスキル本文の「--add」で足す。
TASK_CHAPTERS: dict[str, list[int]] = {
    "intake": [2, 3, 4, 5, 6, 7, 8, 9, 13, 18, 19, 20, 25, 29],  # 届いたもの・急ぎの段階・過ぎた予定・入口が読めない・状態
    "infer": [2, 10, 11, 12, 13, 14],  # 欠けた日時の推測と確認文・取消し・保留
    "write": [2, 12, 15, 16, 17, 18, 20, 22, 25],  # 書く前の検算・強制追加・［仮］・過ぎた予定・家用の見分け・書き込み失敗
    "notify": [2, 12, 16, 17, 18, 19, 20, 22, 27],  # 急ぎ・夜・通知の型・朝のまとめ・家用の本人の予定・未設定の既定
    "adjust": [2, 9, 12, 13, 21, 22, 23],  # 予定調整・家用の見分け・暮らしの場面
    "nightly": [2, 10, 14, 18, 19, 20, 22, 23, 25],  # 毎晩の見直し（朝のまとめの載せ方・家用・読めない入口）
    "delegate": [2, 4, 11, 29, 30],  # 委任・送信の状態（4.3節）
    "interval": [2, 25, 26, 27],  # 確認の間隔
    "fix": [2, 27],  # 誤りの直し方
    "setup": [1, 2, 3, 4, 15, 17, 20, 22, 23, 24, 26, 27, 28, 30],  # 初回設定
}

_H = re.compile(r"^## (\d+)\. ", re.M)
_REF = re.compile(r"第(\d+)(?:\.\d+)?[章節]")
_ADD = re.compile(r"--add\s+([0-9][0-9.,、:：ぁ-んァ-ヶー一-龥々「」]*)")


def chapters(text: str) -> dict[int, str]:
    pos = [(int(m.group(1)), m.start()) for m in _H.finditer(text)]
    out: dict[int, str] = {}
    for i, (n, s) in enumerate(pos):
        e = pos[i + 1][1] if i + 1 < len(pos) else _end_of_numbered(text, s)
        out[n] = text[s:e].rstrip() + "\n"
    return out


def _end_of_numbered(text: str, start: int) -> int:
    m = re.compile(r"^## (?!\d)", re.M).search(text, start + 1)
    return m.start() if m else len(text)


def _part(ch: dict[int, str], spec: str) -> tuple[int, str, bool]:
    """足す章の1つ分。"21"＝章全部／"4.3"＝節だけ／"21:余裕のとり方"＝その見出し語で始まる段落だけ。
    節や段落が見つからなければ章全部に戻す（3つ目が False）。"""
    spec = spec.strip().replace("：", ":")
    m = re.match(r"^(\d+)(?:\.(\d+))?(?::(.+))?$", spec)
    if not m:
        raise ValueError(f"足す章の書き方が違います: {spec}")
    n = int(m.group(1))
    if n not in ch:
        return n, "", False
    body = ch[n]
    head = body.splitlines()[0] + "\n"
    if m.group(2):
        sec = re.search(rf"^### {n}\.{m.group(2)}[ 　].*?(?=^### |\Z)", body, re.M | re.S)
        return (n, head + sec.group(0).rstrip() + "\n", True) if sec else (n, body, False)
    if m.group(3):
        label = m.group(3)
        paras, on = [], False
        for p in body.split("\n\n"):
            q = p.lstrip()
            if q.startswith(label):
                on = True
            elif on and not re.match(r"^(\d+[.．]|[-・（(])", q):
                on = False  # 次の見出し語の段落で終わる。番号や「-」で始まる段落は続き
            if on:
                paras.append(p)
        return (n, head + "\n\n".join(paras).rstrip() + "\n", True) if paras else (n, body, False)
    return n, body, True


def slice_for(task: str, text: str, add: list | None = None) -> dict:
    """add：場面で足す章（番号・"4.3"・"21:見出し語"）。仕事の章のあとに、番号順・重複なしで足す。"""
    if task not in TASK_CHAPTERS:
        raise KeyError(f"知らない仕事です: {task}")
    ch = chapters(text)
    base = TASK_CHAPTERS[task]
    missing = [n for n in base if n not in ch]
    body = "".join(ch[n] for n in base if n in ch)
    extra: dict[int, list] = {}
    widened = []
    for a in add or []:
        n, part, exact = _part(ch, str(a))
        if n in base:
            continue
        if not part:
            missing.append(n)
            continue
        if not exact:
            widened.append(str(a))  # 節・段落が見つからず章全部にした
        extra.setdefault(n, [])
        if part not in extra[n]:
            extra[n].append(part)
    for n in sorted(extra):
        parts = extra[n]
        body += ch[n] if ch[n] in parts else "".join(dict.fromkeys(parts))
    want = base + sorted(extra)
    return {"task": task, "chapters": want, "missing": missing, "text": body, "widened": widened,
            "chars": len(body), "full_chars": len(text)}


def refs(text: str) -> set[int]:
    """本文が参照する章の番号（第N章・第N.N節）。"""
    return {int(m) for m in _REF.findall(text or "")}


def add_specs(text: str) -> list[str]:
    """本文の「--add 18,22」「--add 21:余裕のとり方」に書いてある足す章の指定。"""
    out: list[str] = []
    for m in _ADD.findall(text or ""):
        out += [x for x in re.split(r"[,、 ]+", m) if x.strip()]
    return out


def add_refs(text: str) -> set[int]:
    """本文の --add に書いてある章の番号。"""
    return {int(re.match(r"\d+", x).group(0)) for x in add_specs(text) if re.match(r"\d+", x)}


def uncovered_refs(task: str, skill_text: str) -> list[int]:
    """スキルが参照するのに、仕事の章にも本文の --add にも無い章（取りこぼし）。"""
    return sorted(refs(skill_text) - set(TASK_CHAPTERS[task]) - add_refs(skill_text))


def parse_add(s: str) -> list[str]:
    return [x for x in re.split(r"[,、 ]+", s or "") if x.strip()]


def main(argv=None) -> int:
    import argparse, json
    p = argparse.ArgumentParser(description="正本から仕事に要る章だけを出す（切り出せなければ全文を出して終了コード3）")
    p.add_argument("--seihon", default=DEFAULT_SEIHON)
    p.add_argument("--task", required=True)
    p.add_argument("--add", default="", help="足す章（例：18,22／節だけ 4.3／段落だけ 21:余裕のとり方）")
    p.add_argument("--stats", action="store_true", help="文字数だけを出す")
    a = p.parse_args(argv)
    try:
        text = Path(a.seihon).read_text(encoding="utf-8")
    except OSError as e:
        print(json.dumps({"error": "正本を読めません。正本の場所を確かめ、本人に1行で報告する", "detail": str(e)},
                         ensure_ascii=False))
        return 2
    try:
        add = parse_add(a.add)
        r = slice_for(a.task, text, add)
        problem = None if not r["missing"] else f"章が見つかりません: {r['missing']}"
    except (KeyError, ValueError) as e:
        r, problem = None, str(e)
    if problem:
        # 止めない：全文に戻る。予定ばんは全文を読んで続け、本人に1行で報告する
        print(json.dumps({"warning": "切り出せないので正本の全文を出します（全文を読んで続け、本人に1行で報告）",
                          "detail": problem}, ensure_ascii=False), file=sys.stderr)
        if a.stats:
            print(json.dumps({"task": a.task, "fallback_full": True, "chars": len(text)}, ensure_ascii=False))
        else:
            print(text, end="")
        return 3
    if a.stats:
        r.pop("text")
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(r["text"], end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
