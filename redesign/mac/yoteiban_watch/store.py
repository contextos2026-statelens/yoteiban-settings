"""受け取りの記録（Mac mini 上の自分の記録だけ）。sqlite。依頼IDで重複を除く。
状態：受け取り済み → 仕分け済み → 起こし済み／起こし失敗／起こし結果不明／朝へ／何もしない。
「起こし済み」は「処理が終わった」ではない（処理の成否は箱の台帳が正）。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

STATES = ("受け取り済み", "仕分け済み", "起こし済み", "起こし失敗", "起こし結果不明", "朝へ", "何もしない")


def request_id(source: str, native_id: str) -> str:
    return "rq-" + hashlib.sha256(f"{source}\0{native_id}".encode()).hexdigest()[:16]


class Store:
    def __init__(self, path: str | Path) -> None:
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.execute("pragma journal_mode=wal")
        self.db.execute(
            "create table if not exists items(rid text primary key, source text, native_id text,"
            " received_at real, state text, role text, sort_json text, wake_status text, attempts int default 0,"
            " updated_at real)")
        self.db.execute("create table if not exists cursors(source text primary key, value text)")
        self.db.execute("create table if not exists meter(month text primary key, yen real)")
        self.db.commit()

    def add(self, source: str, native_id: str, role: str) -> tuple[str, bool]:
        rid = request_id(source, native_id)
        cur = self.db.execute(
            "insert or ignore into items(rid, source, native_id, received_at, state, role, updated_at)"
            " values(?,?,?,?,?,?,?)", (rid, source, native_id, time.time(), "受け取り済み", role, time.time()))
        self.db.commit()
        return rid, cur.rowcount == 1

    def set_state(self, rid: str, state: str, **kw) -> None:
        if state not in STATES:
            raise ValueError("知らない状態: " + state)
        row = self.get(rid)
        if row is None:
            raise KeyError(rid)
        if row["state"] in ("起こし済み", "起こし結果不明") and state in ("起こし済み", "起こし失敗"):
            raise ValueError("起こした後・結果不明の後は、起こし直さない")
        sets, vals = ["state=?", "updated_at=?"], [state, time.time()]
        for k in ("sort_json", "wake_status"):
            if k in kw:
                sets.append(k + "=?")
                vals.append(kw[k] if isinstance(kw[k], str) else json.dumps(kw[k], ensure_ascii=False))
        if kw.get("attempt"):
            sets.append("attempts=attempts+1")
        self.db.execute("update items set " + ",".join(sets) + " where rid=?", (*vals, rid))
        self.db.commit()

    def get(self, rid: str) -> dict | None:
        cur = self.db.execute("select * from items where rid=?", (rid,))
        r = cur.fetchone()
        if not r:
            return None
        return dict(zip([c[0] for c in cur.description], r))

    def pending(self) -> list[dict]:
        cur = self.db.execute("select * from items where state in ('受け取り済み','仕分け済み','起こし失敗') order by received_at")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def cursor(self, source: str) -> str | None:
        r = self.db.execute("select value from cursors where source=?", (source,)).fetchone()
        return r[0] if r else None

    def set_cursor(self, source: str, value: str) -> None:
        self.db.execute("insert into cursors(source, value) values(?,?) on conflict(source) do update set value=excluded.value",
                        (source, value))
        self.db.commit()

    def add_cost(self, month: str, yen: float) -> float:
        self.db.execute("insert into meter(month, yen) values(?,?) on conflict(month) do update set yen=yen+excluded.yen",
                        (month, yen))
        self.db.commit()
        return self.month_cost(month)

    def month_cost(self, month: str) -> float:
        r = self.db.execute("select yen from meter where month=?", (month,)).fetchone()
        return float(r[0]) if r else 0.0
