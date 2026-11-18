"""予定ばんの台帳書き込みと状態遷移。"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .db import connect, migrate


class SchemaError(ValueError):
    pass


class InvalidTransition(ValueError):
    pass


class VersionConflict(RuntimeError):
    def __init__(self, current_version: int, diff: dict | None = None):
        self.current_version = current_version
        self.diff = diff or {}
        super().__init__(f"版が一致しません（現在 {current_version}）")


CASE_TRANSITIONS = {
    "提案前": {"未確定", "見送り", "取消"},
    "未確定": {"保留", "確認中", "見送り", "取消"},
    "保留": {"未確定", "見送り", "取消"},
    "確認中": {"承認待ち", "返事なし", "見送り", "取消"},
    "返事なし": {"確認中", "承認待ち", "見送り", "取消"},
    "承認待ち": {"承認済み", "返事なし", "見送り", "取消"},
    "承認済み": {"実行中", "見送り", "取消"},
    "実行中": {"決まった", "実行結果不明（要照合）", "見送り", "取消"},
    "実行結果不明（要照合）": {"決まった", "取消", "承認済み"},
    "決まった": {"準備中", "見送り", "取消"},
    "準備中": {"済んだ", "見送り", "取消"},
    "済んだ": {"見送り", "取消"}, "見送り": {"取消"}, "取消": {"見送り"},
}
CASE_STATUSES = set(CASE_TRANSITIONS)
QUESTION_STATUSES = {"未", "調べ中", "聞き中", "埋まった", "埋められない"}
FILL_METHODS = {"台帳", "調べる", "相手に聞く", "本人に聞く", "待つ"}
GOAL_STATES = {"確定", "推測", "休み中", "達成", "やめた"}
PREF_STATES = {"確定", "推測", "古い"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now(value=None):
    return value or datetime.now().astimezone().isoformat()


class Ledger:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self.conn = connect(db_path)
        migrate(self.conn)
        self._lock = threading.RLock()

    def close(self):
        self.conn.close()

    @contextmanager
    def tx(self):
        with self._lock:
            nested = self.conn.in_transaction
            name = "sp_" + secrets.token_hex(4)
            self.conn.execute(f"SAVEPOINT {name}" if nested else "BEGIN IMMEDIATE")
            try:
                yield self
                self.conn.execute(f"RELEASE SAVEPOINT {name}" if nested else "COMMIT")
            except Exception:
                self.conn.execute(f"ROLLBACK TO SAVEPOINT {name}" if nested else "ROLLBACK")
                if nested:
                    self.conn.execute(f"RELEASE SAVEPOINT {name}")
                raise

    def _history(self, case_id, actor, change, reason, basis, at):
        self.conn.execute("INSERT INTO history VALUES(?,?,?,?,?,?,?)", (secrets.token_hex(12), case_id, at, actor, _json(change), reason, basis))

    def upsert_case(self, patch, expected_version=None, actor="本人", reason="更新", basis="", now=None):
        if not isinstance(patch, dict):
            raise SchemaError("patch は辞書で指定してください")
        case_id = patch.get("case_id")
        if not case_id:
            raise SchemaError("case_id が必要です")
        at = _now(now or patch.get("updated_at"))
        allowed = {"kind", "title", "summary", "cause_input_id", "status", "certainty", "urgency", "due_at", "event_start", "event_end", "next_review_at", "trigger_hint", "resume_note", "owner_role", "created_at", "updated_at"}
        unknown = set(patch) - (allowed | {"case_id"})
        if unknown:
            raise SchemaError("未対応の項目: " + ", ".join(sorted(unknown)))
        with self.tx():
            old = self.conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if old is None:
                if expected_version not in (None, 0):
                    raise VersionConflict(0, {"case_id": {"before": None, "after": patch}})
                status = patch.get("status", "提案前")
                if status not in CASE_STATUSES:
                    raise SchemaError(f"状態が不正です: {status}")
                required = ("kind", "title")
                if any(not patch.get(k) for k in required):
                    raise SchemaError("新しい案件には kind と title が必要です")
                data = {k: v for k, v in patch.items() if k in allowed}
                data.update(case_id=case_id, status=status, version=1, created_at=data.get("created_at", at), updated_at=at)
                cols = list(data)
                self.conn.execute(f"INSERT INTO cases({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", [data[k] for k in cols])
                change = {k: {"before": None, "after": v} for k, v in data.items() if k not in {"version", "created_at", "updated_at"}}
            else:
                current = dict(old)
                if expected_version is None or expected_version != current["version"]:
                    proposed = {k: v for k, v in patch.items() if k in allowed}
                    diff = {k: {"before": current.get(k), "after": v} for k, v in proposed.items() if current.get(k) != v}
                    raise VersionConflict(current["version"], diff)
                data = {k: v for k, v in patch.items() if k in allowed}
                if "status" in data and data["status"] != current["status"] and data["status"] not in CASE_TRANSITIONS.get(current["status"], set()):
                    raise InvalidTransition(f"{current['status']} から {data['status']} へは移れません")
                change = {k: {"before": current.get(k), "after": v} for k, v in data.items() if current.get(k) != v}
                if not change:
                    return current["version"]
                data["version"] = current["version"] + 1
                data["updated_at"] = at
                assignments = ",".join(f"{k}=?" for k in data)
                cur = self.conn.execute(f"UPDATE cases SET {assignments} WHERE case_id=? AND version=?", [*data.values(), case_id, current["version"]])
                if cur.rowcount != 1:
                    row = self.conn.execute("SELECT version FROM cases WHERE case_id=?", (case_id,)).fetchone()
                    raise VersionConflict(row[0] if row else 0, change)
            self._history(case_id, actor, change, reason, basis, at)
            return data.get("version", 1)

    def get_case(self, case_id):
        row = self.conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        return dict(row) if row else None

    def list_cases(self, status_in=None):
        if status_in is None:
            rows = self.conn.execute("SELECT * FROM cases ORDER BY created_at,case_id").fetchall()
        else:
            statuses = list(status_in)
            if not statuses:
                return []
            rows = self.conn.execute(f"SELECT * FROM cases WHERE status IN ({','.join('?' for _ in statuses)}) ORDER BY created_at,case_id", statuses).fetchall()
        return [dict(r) for r in rows]

    def list_due(self, now):
        rows = self.conn.execute("SELECT * FROM cases WHERE due_at IS NOT NULL AND due_at<=? AND status NOT IN ('済んだ','見送り','取消') ORDER BY due_at,case_id", (now,)).fetchall()
        return [dict(r) for r in rows]

    def add_question(self, case_id, text, fill_method, now=None):
        if fill_method not in FILL_METHODS:
            raise SchemaError(f"fill_method が不正です: {fill_method}")
        q_id, at = secrets.token_hex(12), _now(now)
        with self.tx():
            self.conn.execute("INSERT INTO questions(q_id,case_id,text,fill_method,status,updated_at) VALUES(?,?,?,?,?,?)", (q_id, case_id, text, fill_method, "未", at))
            self._history(case_id, "本人", {"question": {"before": None, "after": {"q_id": q_id, "text": text, "fill_method": fill_method, "status": "未"}}}, "質問を追加", "", at)
        return q_id

    def set_question(self, q_id, status, answer=None, now=None, actor="本人", reason="質問を更新"):
        if status not in QUESTION_STATUSES:
            raise InvalidTransition(f"質問状態が不正です: {status}")
        at = _now(now)
        with self.tx():
            row = self.conn.execute("SELECT * FROM questions WHERE q_id=?", (q_id,)).fetchone()
            if row is None:
                raise KeyError(q_id)
            old = dict(row)
            allowed = {"未": {"未", "調べ中", "聞き中", "埋まった", "埋められない"}, "調べ中": QUESTION_STATUSES, "聞き中": QUESTION_STATUSES, "埋まった": {"埋まった", "未"}, "埋められない": {"埋められない", "未"}}
            if status not in allowed[old["status"]]:
                raise InvalidTransition(f"{old['status']} から {status} へは移れません")
            changes = {"status": status, "answer": answer, "updated_at": at}
            self.conn.execute("UPDATE questions SET status=?,answer=?,updated_at=? WHERE q_id=?", (status, answer, at, q_id))
            self._history(old["case_id"], actor, {k: {"before": old.get(k), "after": v} for k, v in changes.items() if old.get(k) != v}, reason, "", at)

    def link_cases(self, a, b, kind, now=None):
        at = _now(now)
        with self.tx():
            self.conn.execute("INSERT OR IGNORE INTO case_links VALUES(?,?,?)", (a, b, kind))
            if self.conn.execute("SELECT changes()").fetchone()[0]:
                change = {"link": {"before": None, "after": {"from_case": a, "to_case": b, "kind": kind}}}
                self._history(a, "本人", change, "案件を関連付け", "", at)
                self._history(b, "本人", change, "案件を関連付け", "", at)

    def add_goal(self, parent_id, kind, text, state, now=None):
        if state not in GOAL_STATES:
            raise SchemaError(f"目的状態が不正です: {state}")
        node_id, at = secrets.token_hex(12), _now(now)
        with self.tx():
            self.conn.execute("INSERT INTO goal_nodes(node_id,parent_id,kind,text,state,updated_at) VALUES(?,?,?,?,?,?)", (node_id, parent_id, kind, text, state, at))
            self.conn.execute("INSERT INTO history(h_id,case_id,at,actor,change,reason,basis) VALUES(?,?,?,?,?,?,?)", (secrets.token_hex(12), "goal:"+node_id, at, "本人", _json({"goal_node": {"before": None, "after": {"node_id": node_id, "parent_id": parent_id, "kind": kind, "text": text, "state": state}}}), "目的を追加", ""))
        return node_id

    def set_pref(self, topic, value, state, now=None):
        if state not in PREF_STATES:
            raise SchemaError(f"好み状態が不正です: {state}")
        at = _now(now)
        with self.tx():
            row = self.conn.execute("SELECT * FROM twin_prefs WHERE topic=? ORDER BY rowid DESC LIMIT 1", (topic,)).fetchone()
            before = dict(row) if row else None
            pref_id = before["pref_id"] if before else secrets.token_hex(12)
            if before:
                self.conn.execute("UPDATE twin_prefs SET value=?,state=? WHERE pref_id=?", (value, state, pref_id))
            else:
                self.conn.execute("INSERT INTO twin_prefs(pref_id,topic,value,state) VALUES(?,?,?,?)", (pref_id, topic, value, state))
            self.conn.execute("INSERT INTO history(h_id,case_id,at,actor,change,reason,basis) VALUES(?,?,?,?,?,?,?)", (secrets.token_hex(12), "pref:"+pref_id, at, "本人", _json({"twin_pref": {"before": before, "after": {"pref_id": pref_id, "topic": topic, "value": value, "state": state}}}), "好みを更新", ""))
        return pref_id

    def get_setting(self, key, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value, now=None, actor="本人"):
        at = _now(now)
        with self.tx():
            row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            before = json.loads(row[0]) if row else None
            self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, _json(value)))
            self.conn.execute("INSERT INTO history(h_id,case_id,at,actor,change,reason,basis) VALUES(?,?,?,?,?,?,?)", (secrets.token_hex(12), "setting:"+key, at, actor, _json({"setting": {"before": before, "after": value}}), "設定を更新", ""))
