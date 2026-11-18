"""道具の置き場と正本の確かめ。python3 -m yoteiban_rd.selfcheck [--seihon <正本>] [--lib-home <置き場>]
何も書き換えない。問題があれば終了コード1と、本人に伝える1行を返す。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .rules_slice import DEFAULT_SEIHON, TASK_CHAPTERS, slice_for

LIB_HOME = "/home/box/agent-data/yoteiban-redesign/lib"


def check(seihon: str = DEFAULT_SEIHON, lib_home: str = LIB_HOME) -> dict:
    problems = []
    here = str(Path(__file__).resolve().parent.parent)
    if os.path.realpath(here) != os.path.realpath(lib_home):
        problems.append(f"道具が決まった置き場（{lib_home}）ではなく {here} から動いています")
    try:
        text = Path(seihon).read_text(encoding="utf-8")
    except OSError:
        problems.append(f"正本が読めません（{seihon}）")
        text = None
    slices = {}
    if text is not None:
        for t in TASK_CHAPTERS:
            r = slice_for(t, text)
            slices[t] = {"chars": r["chars"], "missing": r["missing"]}
            if r["missing"]:
                problems.append(f"{t} の章が見つかりません: {r['missing']}（全文に戻って続ける）")
    return {"ok": not problems, "lib": here, "seihon": seihon, "slices": slices, "problems": problems,
            "line": "道具と正本は使える状態です" if not problems else "予定ばんの道具の確認で問題：" + problems[0]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seihon", default=DEFAULT_SEIHON)
    p.add_argument("--lib-home", default=LIB_HOME)
    a = p.parse_args(argv)
    r = check(a.seihon, a.lib_home)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
