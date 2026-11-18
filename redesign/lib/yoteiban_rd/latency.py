"""本人が待つ対話の待ち時間を数えて、速さの4点の基準に照らす（本番で測る用。読むだけ・計算だけ）。

入力：1件ごとに {"asked_at": 本人の発言の時刻, "replied_at": 予定ばんの返事の時刻, "scene": 場面, "adjustments": 調整の数}
  scene：simple（単発で簡単）／dialog（本人が待つ対話：答え・要望・チャットの頼み・調整文の相談・口頭の定期確認など）
         ／multi（複数の頼みを一度に）。ルーチン（本人が待たない定期確認）は入れない。
基準（2026-11-18 本人）：
  simple：30秒前後 → 中央値30秒以下、9割が45秒以下
  dialog：1つの調整につき基本1〜3分、最大5分 → 1調整あたりの中央値180秒以下、全件300秒以下
  multi：合計で1〜3分、最大5分 → 中央値180秒以下、全件300秒以下
判定に要る件数：7日以上、合計20件以上、そのうち simple 5件以上・dialog 10件以上（足りなければ14日まで延ばす）。
"""
from __future__ import annotations

import statistics

from .calc import parse_time

RULES = {"simple": {"median": 30, "p90": 45}, "dialog": {"median": 180, "max": 300}, "multi": {"median": 180, "max": 300}}
NEED = {"days": 7, "total": 20, "simple": 5, "dialog": 10}


def _p90(xs: list[float]) -> float:
    s = sorted(xs)
    return s[max(0, int(round(0.9 * len(s))) - 1)]


def judge(rows: list[dict]) -> dict:
    by: dict[str, list[float]] = {"simple": [], "dialog": [], "multi": []}
    days = set()
    bad = []
    for r in rows:
        a, b = parse_time(r["asked_at"]), parse_time(r["replied_at"])
        sec = (b - a).total_seconds()
        if sec < 0:
            bad.append(r)
            continue
        sc = r.get("scene", "dialog")
        if sc not in by:
            bad.append(r)
            continue
        if sc == "dialog":
            sec = sec / max(1, int(r.get("adjustments", 1)))  # 1つの調整あたり
        by[sc].append(sec)
        days.add(a.date())
    stats, ok = {}, True
    for sc, xs in by.items():
        if not xs:
            stats[sc] = {"件数": 0}
            continue
        st = {"件数": len(xs), "中央値秒": round(statistics.median(xs), 1), "最大秒": round(max(xs), 1),
              "9割秒": round(_p90(xs), 1)}
        rule = RULES[sc]
        st["基準内"] = (st["中央値秒"] <= rule["median"] and
                      (st["9割秒"] <= rule["p90"] if "p90" in rule else st["最大秒"] <= rule["max"]))
        ok = ok and st["基準内"]
        stats[sc] = st
    total = sum(len(v) for v in by.values())
    enough = (len(days) >= NEED["days"] and total >= NEED["total"] and len(by["simple"]) >= NEED["simple"]
              and len(by["dialog"]) >= NEED["dialog"])
    verdict = "件数が足りない（測り続ける）" if not enough else ("4点（基準内）" if ok else "基準外（手順を短くする）")
    return {"判定": verdict, "日数": len(days), "合計件数": total, "場面ごと": stats, "読めない行": len(bad)}
