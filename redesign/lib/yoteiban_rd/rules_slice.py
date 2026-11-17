"""決まりを軽くする：仕事ごとに、正本の必要な章だけを切り出す。

正本（Git の seihon.md）は1つのまま。二重管理しないため、ここは章番号の地図だけを持ち、
本文はその都度正本から切り出す。章の見出しが変わったら missing に出して止める。
"""
from __future__ import annotations

import re
from pathlib import Path

# 仕事 → 正本の章番号。2（大原則）はいつも入れる。
TASK_CHAPTERS: dict[str, list[int]] = {
    "intake": [2, 3, 5, 6, 7, 8, 9, 12, 13, 25],  # 届いたものの振り分け
    "infer": [2, 10, 11, 12, 14],  # 欠けた日時の推測と確認文
    "write": [2, 15, 16, 17, 18, 22],  # カレンダーへ書く・検算
    "notify": [2, 19, 20],  # 急ぎ・夜・朝のまとめ
    "adjust": [2, 21, 22, 23, 24],  # 予定調整・家用の見分け・暮らしの場面
    "nightly": [2, 10, 14, 19, 21, 23],  # 毎晩の見直し
    "delegate": [2, 11, 29, 30],  # 委任・送信の状態
    "fix": [2, 27],  # 誤りの直し方
    "setup": [1, 2, 3, 4, 26, 27],
}

_H = re.compile(r"^## (\d+)\. ", re.M)


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


def slice_for(task: str, text: str) -> dict:
    if task not in TASK_CHAPTERS:
        raise KeyError(f"知らない仕事です: {task}")
    ch = chapters(text)
    want = TASK_CHAPTERS[task]
    missing = [n for n in want if n not in ch]
    body = "".join(ch[n] for n in want if n in ch)
    return {"task": task, "chapters": want, "missing": missing, "text": body,
            "chars": len(body), "full_chars": len(text)}


def main(argv=None) -> int:
    import argparse, json
    p = argparse.ArgumentParser(description="正本から仕事に要る章だけを出す")
    p.add_argument("--seihon", required=True)
    p.add_argument("--task", required=True, choices=sorted(TASK_CHAPTERS))
    p.add_argument("--stats", action="store_true", help="文字数だけを出す")
    a = p.parse_args(argv)
    r = slice_for(a.task, Path(a.seihon).read_text(encoding="utf-8"))
    if r["missing"]:
        print(json.dumps({"error": "章が見つかりません", "missing": r["missing"]}, ensure_ascii=False))
        return 2
    if a.stats:
        r.pop("text")
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(r["text"], end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
