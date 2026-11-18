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

# 仕事 → 正本の章。2（大原則）はいつも入れる。場面で要る章はスキル本文の「--add」で足す。
# "20.1" のような文字は節だけを読む（正本の第20・21章には節の見出しがある）。節が見つからなければ章全部を読む。
TASK_CHAPTERS: dict[str, list] = {
    "intake": [2, 3, 4, 5, 6, 7, 8, 9, 13, 18, 19, "20.1", "20.2", "20.3", "20.6", 25, 29],  # 届いたもの・急ぎの段階・過ぎた予定・入口が読めない・状態
    "infer": [2, 10, 11, 12, 13, 14],  # 欠けた日時の推測と確認文・取消し・保留
    "write": [2, 12, 15, 16, 17, 18, "20.1", "20.2", "20.3", "20.5", "20.6", 22, 25],  # 書く前の検算・強制追加・［仮］・過ぎた予定・型による自動追加（20.1〜20.3）・直前の知らせ（20.5）・静かな時間（20.6）
    "notify": [2, 12, 16, 17, 18, 19, 20, 22, 27],  # 急ぎ・夜・通知の型・朝のまとめ・家用の本人の予定・未設定の既定
    "adjust": [2, 9, 12, 13, 21, 22, 23],  # 予定調整・家用の見分け・暮らしの場面
    "nightly": [2, 10, 14, 18, 19, "20.1", "20.3", "20.4", "20.6", "20.8", 22, 23, 25],  # 毎晩の見直し（朝のまとめの載せ方・家用・読めない入口）
    "delegate": [2, 4, 11, 29, 30],  # 委任・送信の状態（4.3節）
    "interval": [2, 25, 26, 27],  # 確認の間隔
    "fix": [2, 27],  # 誤りの直し方
    "chat": [2],  # 本人が待っている対話の早道（大原則だけ。場面の章は運用ルールの【対話の早道】の --add で足す）
    "setup": [1, 2, 3, 4, 15, 17, 20, 22, 23, 24, 26, 27, 28, 30],  # 初回設定
}

# 節だけを読むときも必ず一緒に読む節（第21章の冒頭：「調整の考え方」が章のほかの文に優先する）。
ALWAYS_WITH: dict[int, list[str]] = {21: ["21.2"]}


def chapter_no(spec) -> int:
    return int(re.match(r"\d+", str(spec)).group(0))


def task_chapter_nos(task: str) -> list[int]:
    return list(dict.fromkeys(chapter_no(x) for x in TASK_CHAPTERS[task]))


_H = re.compile(r"^## (\d+)\. ", re.M)
_REF = re.compile(r"第(\d+)(?:\.\d+)?[章節]")
_ADD = re.compile(r"--add\s+([0-9][0-9.,、:：ぁ-んァ-ヶー・一-龥々「」]*)")


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


def _split(body: str) -> tuple[str, dict[str, str]]:
    """章を「見出し行＋前書き」と節（"20.1" → 本文）に分ける。"""
    secs = list(re.finditer(r"^### (\d+\.\d+)[ 　].*$", body, re.M))
    if secs:
        pre = body[: secs[0].start()]
    else:  # 節の無い章は、見出し行と「出典」の段落（章の前提）だけ
        ps = body.split("\n\n")
        pre = ps[0] + ("\n\n" + ps[1] if len(ps) > 1 and ps[1].lstrip().startswith("出典") else "")
    out = {}
    for i, m in enumerate(secs):
        e = secs[i + 1].start() if i + 1 < len(secs) else len(body)
        out[m.group(1)] = body[m.start():e].rstrip() + "\n\n"
    return pre.rstrip() + "\n\n", out


def _label_paras(body: str, label: str) -> list[str]:
    paras, on = [], False
    for p in body.split("\n\n"):
        q = p.lstrip()
        if q.startswith(label):
            on = True
        elif on and not re.match(r"^(\d+[.．]|[-・（(])", q):
            on = False  # 次の見出し語の段落で終わる。番号や「-」で始まる段落は続き
        if on:
            paras.append(p)
    return paras


def _compose(ch: dict[int, str], specs: list) -> tuple[str, list[int], list[str], list[int]]:
    """指定（12・"4.3"・"21:見出し語"）から本文を作る。戻り：本文・読んだ章・章全部に戻した指定・無い章。"""
    want: dict[int, dict] = {}
    widened, missing = [], []
    for spec in specs:
        sp = str(spec).strip().replace("：", ":")
        m = re.match(r"^(\d+)(?:\.(\d+))?(?::(.+))?$", sp)
        if not m:
            raise ValueError(f"足す章の書き方が違います: {sp}")
        n = int(m.group(1))
        if n not in ch:
            if n not in missing:
                missing.append(n)
            continue
        w = want.setdefault(n, {"full": False, "secs": [], "labels": []})
        pre, secs = _split(ch[n])
        if m.group(2):
            key = f"{n}.{m.group(2)}"
            if key in secs:
                w["secs"].append(key)
                w["secs"] += [k for k in ALWAYS_WITH.get(n, []) if k in secs]
            else:
                w["full"] = True; widened.append(sp)
        elif m.group(3):
            if _label_paras(ch[n], m.group(3)):
                w["labels"].append(m.group(3))
                w["secs"] += [k for k in ALWAYS_WITH.get(n, []) if k in secs]
            else:
                w["full"] = True; widened.append(sp)
        else:
            w["full"] = True
    body = ""
    for n in sorted(want):
        w = want[n]
        if w["full"]:
            body += ch[n]
            continue
        pre, secs = _split(ch[n])
        part = pre + "".join(secs[k] for k in sorted(set(w["secs"]), key=lambda k: int(k.split(".")[1])))
        for lb in w["labels"]:
            for p in _label_paras(ch[n], lb):
                if p not in part:
                    part += p.rstrip() + "\n\n"
        body += part.rstrip() + "\n"
    return body, sorted(want), widened, missing


def slice_for(task: str, text: str, add: list | None = None) -> dict:
    """仕事の章（節の指定も可）＋場面で足す章（番号・"4.3"・"21:見出し語"）。章の番号順・重複なし。"""
    if task not in TASK_CHAPTERS:
        raise KeyError(f"知らない仕事です: {task}")
    ch = chapters(text)
    base = TASK_CHAPTERS[task]
    base_full = {x for x in base if isinstance(x, int)}
    extra = [a for a in (add or []) if chapter_no(a) not in base_full]
    body, got, widened, missing = _compose(ch, list(base) + extra)
    return {"task": task, "chapters": got, "missing": missing, "text": body, "widened": widened,
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
    return sorted(refs(skill_text) - set(task_chapter_nos(task)) - add_refs(skill_text))


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
