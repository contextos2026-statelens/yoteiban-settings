"""並べて試す（第5段）：今の仕組みと新しい仕組みを同じ入力で比べる。
新しい仕組みは影で動かす。知らせ・カレンダー・送信の出口は ShadowSink に差し替え、何か出そうとしたら止める。

比べる指標：
- 取りこぼし：今の仕組みが「予定関連」としたのに、新しい仕組みが拾わなかった件数（0が合格）
- 余計な起こし：新しい仕組みが起こしたのに、今の仕組みで「予定なし」だった件数（少ないほど良い。枠の消費）
- 一致率：予定関連かどうかの一致
- 気づくまでの時間：受け取り → 判断（中央値・最大）
- 急ぎの段階の一致（両方にあるときだけ）
- 影の出口に出そうとした回数（0が合格）
"""
from __future__ import annotations

import statistics
from datetime import datetime


class ShadowViolation(Exception):
    pass


class ShadowSink:
    """影の実行で使う出口。呼ばれたら記録して止める。"""

    def __init__(self) -> None:
        self.attempts: list = []

    def notify(self, *a, **k):
        self.attempts.append(("notify", a, k))
        raise ShadowViolation("影の実行では知らせません")

    def write_calendar(self, *a, **k):
        self.attempts.append(("calendar", a, k))
        raise ShadowViolation("影の実行ではカレンダーに書きません")

    def send(self, *a, **k):
        self.attempts.append(("send", a, k))
        raise ShadowViolation("影の実行では送りません")


def _minutes(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60


def compare(old: list[dict], new: list[dict], sink_attempts: int = 0) -> dict:
    """old: {ref, related(bool), decided_at, received_at, tier?}
    new: {ref, decision(起こす/朝へ/何もしない), related(bool|None), decided_at, received_at, tier?}"""
    o = {r["ref"]: r for r in old}
    n = {r["ref"]: r for r in new}
    refs = sorted(set(o) | set(n))
    missed, extra, agree, both, tier_agree, tier_both = [], [], 0, 0, 0, 0
    lat_old, lat_new = [], []
    for ref in refs:
        a, b = o.get(ref), n.get(ref)
        if a and a.get("related") and (not b or (b.get("decision") == "何もしない")):
            missed.append(ref)
        if b and b.get("decision") == "起こす" and a and a.get("related") is False:
            extra.append(ref)
        if a and b:
            both += 1
            nb = b.get("related")
            if nb is None:
                nb = b.get("decision") != "何もしない"
            agree += int(bool(a.get("related")) == bool(nb))
            if a.get("tier") and b.get("tier"):
                tier_both += 1
                tier_agree += int(a["tier"] == b["tier"])
        for r, lst in ((a, lat_old), (b, lat_new)):
            if r:
                m = _minutes(r.get("received_at"), r.get("decided_at"))
                if m is not None:
                    lst.append(m)
    def summ(xs):
        return {"median_min": round(statistics.median(xs), 2), "max_min": round(max(xs), 2)} if xs else None
    res = {
        "inputs_old": len(o), "inputs_new": len(n), "missed_by_new": missed, "extra_wakes": extra,
        "agreement": round(agree / both, 3) if both else None,
        "tier_agreement": round(tier_agree / tier_both, 3) if tier_both else None,
        "latency_old": summ(lat_old), "latency_new": summ(lat_new),
        "shadow_output_attempts": sink_attempts,
    }
    res["pass"] = (not missed) and sink_attempts == 0 and (res["agreement"] is None or res["agreement"] >= 0.9)
    return res
