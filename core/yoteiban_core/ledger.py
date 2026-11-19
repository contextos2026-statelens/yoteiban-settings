"""予定ばんの台帳書き込みと状態遷移。"""
from __future__ import annotations

import json
import hashlib
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

from .db import connect, migrate
from .refs import canonicalize_source, normalize_native_ref


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
        allowed = {"kind", "title", "summary", "cause_input_id", "status", "certainty", "urgency", "due_at", "event_start", "event_end", "next_review_at", "trigger_hint", "resume_note", "owner_role", "created_at", "updated_at", "location", "duration_min", "counterpart", "inferred_fields", "tentative", "reply_by", "extra"}
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

    @staticmethod
    def _digest(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _record_history(self, case_id, change, reason, now, actor="ボット", basis=""):
        self.conn.execute("INSERT INTO history(h_id,case_id,at,actor,change,reason,basis) VALUES(?,?,?,?,?,?,?)", (secrets.token_hex(12), case_id, now, actor, _json(change), reason, basis))

    def _existing_input(self, source, native_ref, input_id):
        """正規形で照合する。見つかっても保存済みの source / native_ref は書き換えない。"""
        if self.conn.execute("SELECT 1 FROM raw_inputs WHERE input_id=?", (input_id,)).fetchone():
            return input_id
        canon = canonicalize_source(source)
        for row in self.conn.execute("SELECT input_id, source, native_ref FROM raw_inputs"):
            if (canonicalize_source(row["source"]) == canon
                    and normalize_native_ref(row["source"], row["native_ref"]) == native_ref):
                return row["input_id"]
        return None

    def ingest(self, raw, now=None):

        if not isinstance(raw, dict):
            raise SchemaError("raw は辞書で指定してください")
        source, native_ref = raw.get("source"), raw.get("native_ref")
        received_at = raw.get("received_at")
        if not all(isinstance(x, str) and x for x in (source, native_ref, received_at)):
            raise SchemaError("source、native_ref、received_at が必要です")
        native_ref = normalize_native_ref(source, native_ref)
        if not native_ref:
            raise SchemaError("source、native_ref、received_at が必要です")
        input_id = self._digest(f"{source}|{native_ref}")[:24]
        with self.tx():
            existing = self._existing_input(source, native_ref, input_id)
            if existing:
                return existing, False
            days = self.get_setting("retention_days", 90)
            try:
                received = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise SchemaError("received_at はISO 8601で指定してください") from exc
            retention_until = (received + timedelta(days=int(days))).isoformat()
            values = {
                "input_id": input_id, "source": source, "native_ref": native_ref,
                "received_at": received_at, "body_text": raw.get("body_text", ""),
                "body_masked": raw.get("body_masked", ""), "summary_masked": raw.get("summary_masked", ""),
                "media_ref": raw.get("media_ref"), "sender_role": raw.get("sender_role", "不明"),
                "recv_state": raw.get("recv_state", "受け取り済み"), "read_level": raw.get("read_level"),
                "retention_until": raw.get("retention_until", retention_until),
            }
            for key in ("addressed_to", "is_direct", "named", "schedule_related", "mute_state", "extra"):
                if key in raw:
                    values[key] = raw[key]
            cols = list(values)
            self.conn.execute(f"INSERT INTO raw_inputs({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", [values[k] for k in cols])
            self._record_history("input:"+input_id, {"raw_input": {"before": None, "after": {"input_id": input_id, "source": source, "recv_state": values['recv_state']}}}, "入力を受け取り", now or received_at)
        return input_id, True

    def record_reading(self, input_id, items, read_level, now=None):
        if not isinstance(items, list):
            raise SchemaError("items はリストで指定してください")
        row = self.conn.execute("SELECT * FROM raw_inputs WHERE input_id=?", (input_id,)).fetchone()
        if row is None:
            raise KeyError(input_id)
        if not items:
            return []
        at = _now(now)
        made = []
        with self.tx():
            for index, item in enumerate(items):
                if not isinstance(item, dict) or not isinstance(item.get("kind"), str) or not isinstance(item.get("title"), str):
                    raise SchemaError("item には kind と title が必要です")
                case_id = self._digest(f"{input_id}|{index}")[:24]
                patch = {k: item.get(k) for k in ("kind", "title", "summary", "event_start", "event_end", "due_at", "certainty", "urgency", "location", "duration_min", "counterpart", "inferred_fields", "tentative", "reply_by") if item.get(k) is not None}
                patch.update(case_id=case_id, cause_input_id=input_id, status="未確定", created_at=at, updated_at=at)
                current = self.get_case(case_id)
                self.upsert_case(patch, expected_version=current["version"] if current else None, actor="ボット", reason="入力を読み取り", basis=input_id, now=at)
                for question in item.get("questions", []):
                    if not isinstance(question, dict) or not question.get("text") or not question.get("fill_method"):
                        raise SchemaError("questions の各項目には text と fill_method が必要です")
                    self.add_question(case_id, question["text"], question["fill_method"], now=at)
                made.append(case_id)
            self.conn.execute("UPDATE raw_inputs SET recv_state='読み取り済み',read_level=? WHERE input_id=?", (read_level, input_id))
            self._record_history("input:"+input_id, {"recv_state": {"before": row["recv_state"], "after": "読み取り済み"}, "read_level": {"before": row["read_level"], "after": read_level}}, "入力の読み取り完了", at)
        return made

    def add_plan(self, case_id, body, valid_until, now=None):
        if not isinstance(body, dict):
            raise SchemaError("plan body は辞書で指定してください")
        plan_id, at = secrets.token_hex(12), _now(now)
        with self.tx():
            self.conn.execute("INSERT INTO plans(plan_id,case_id,body,status,valid_until,created_at) VALUES(?,?,?,?,?,?)", (plan_id, case_id, _json(body), "作成中", valid_until, at))
            self._record_history(case_id, {"plan": {"before": None, "after": {"plan_id": plan_id, "body": body, "status": "作成中", "valid_until": valid_until}}}, "案を作成", at)
        return plan_id

    def set_plan_status(self, plan_id, status, now=None, actor="本人", reason="案の状態を更新"):
        allowed = {"作成中": {"提示", "却下", "古い"}, "提示": {"採用", "却下", "古い"}, "採用": {"古い"}, "却下": set(), "古い": set()}
        if status not in allowed:
            raise InvalidTransition(f"案状態が不正です: {status}")
        at = _now(now)
        with self.tx():
            row = self.conn.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
            if row is None:
                raise KeyError(plan_id)
            if status != row["status"] and status not in allowed[row["status"]]:
                raise InvalidTransition(f"{row['status']} から {status} へは移れません")
            if status != row["status"]:
                self.conn.execute("UPDATE plans SET status=? WHERE plan_id=?", (status, plan_id))
                self._record_history(row["case_id"], {"plan_status": {"before": row["status"], "after": status}}, reason, at, actor)

    def reserve_op(self, kind, target_id, case_id, payload_hash, approval_id=None, linked_op_id=None, now=None):
        if kind not in {"cal_personal", "cal_draft", "reminder", "notion", "send"}:
            raise SchemaError(f"操作種別が不正です: {kind}")
        op_id = self._digest(f"{kind}|{target_id}|{case_id}|{payload_hash}")[:24]
        at = _now(now)
        with self.tx():
            row = self.conn.execute("SELECT * FROM operations WHERE op_id=?", (op_id,)).fetchone()
            if row:
                return op_id, row["state"]
            if kind in {"cal_personal", "send"} and not approval_id:
                raise InvalidTransition("本人の承認が必要です")
            if approval_id:
                approval = self.conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
                if approval is None or approval["state"] != "有効" or approval["op_kind"] != kind or approval["payload_hash"] != payload_hash:
                    raise InvalidTransition("承認の種別または内容が一致しません")
                if datetime.fromisoformat(at.replace("Z", "+00:00")) >= datetime.fromisoformat(approval["expires_at"].replace("Z", "+00:00")):
                    self.conn.execute("UPDATE approvals SET state='期限切れ' WHERE approval_id=?", (approval_id,))
                    self._record_history("approval:"+approval_id, {"state": {"before": "有効", "after": "期限切れ"}}, "承認期限切れ", at)
                    raise InvalidTransition("承認の期限が切れています")
            self.conn.execute("INSERT INTO operations(op_id,kind,target_id,case_id,payload_hash,approval_id,linked_op_id,state,handoff_state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (op_id, kind, target_id, case_id, payload_hash, approval_id, linked_op_id, "予約", "渡し前（未送信）" if kind == "send" else None, at, at))
            self._record_history(case_id, {"operation": {"before": None, "after": {"op_id": op_id, "kind": kind, "state": "予約"}}}, "操作を予約", at)
        return op_id, None

    def set_op_state(self, op_id, state, handoff_state=None, result=None, now=None, actor="ボット", reason="操作状態を更新"):
        transitions = {"予約": {"実行中", "取消"}, "実行中": {"成功（読み戻し済み）", "失敗", "実行結果不明（要照合）"}, "実行結果不明（要照合）": {"成功（読み戻し済み）", "失敗"}, "成功（読み戻し済み）": set(), "失敗": set(), "取消": set()}
        handoffs = ["渡し前（未送信）", "渡し済み（未確認）", "送信完了", "返事あり"]
        at = _now(now)
        with self.tx():
            row = self.conn.execute("SELECT * FROM operations WHERE op_id=?", (op_id,)).fetchone()
            if row is None:
                raise KeyError(op_id)
            if state != row["state"] and state not in transitions.get(row["state"], set()):
                raise InvalidTransition(f"{row['state']} から {state} へは移れません")
            if state == "実行中" and row["kind"] in {"cal_personal", "send"}:
                approval = self.conn.execute("SELECT * FROM approvals WHERE approval_id=?", (row["approval_id"],)).fetchone() if row["approval_id"] else None
                if approval is None or approval["state"] != "使用済み" or approval["op_kind"] != row["kind"] or approval["payload_hash"] != row["payload_hash"]:
                    raise InvalidTransition("実行には内容が一致する使用済みの本人承認が必要です")
            old_handoff = row["handoff_state"]
            if handoff_state is not None:
                if row["kind"] != "send":
                    raise InvalidTransition("handoff_state は送信操作だけに使えます")
                if handoff_state not in handoffs or (old_handoff is not None and handoffs.index(handoff_state) not in {handoffs.index(old_handoff), handoffs.index(old_handoff)+1}):
                    raise InvalidTransition("handoff_state は決まった順にだけ進められます")
            encoded = _json(result) if result is not None else row["result"]
            self.conn.execute("UPDATE operations SET state=?,handoff_state=?,result=?,updated_at=? WHERE op_id=?", (state, handoff_state if handoff_state is not None else old_handoff, encoded, at, op_id))
            change = {"state": {"before": row["state"], "after": state}}
            if handoff_state is not None and handoff_state != old_handoff:
                change["handoff_state"] = {"before": old_handoff, "after": handoff_state}
            if result is not None:
                change["result"] = {"before": json.loads(row["result"]) if row["result"] else None, "after": result}
            self._record_history(row["case_id"], change, reason, at, actor)

    def get_op(self, op_id):
        row = self.conn.execute("SELECT * FROM operations WHERE op_id=?", (op_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["result"] = json.loads(result["result"]) if result["result"] is not None else None
        return result

    def list_ops(self, state_in=None):
        if state_in is None:
            rows = self.conn.execute("SELECT * FROM operations ORDER BY created_at,op_id").fetchall()
        else:
            states = list(state_in)
            if not states:
                return []
            rows = self.conn.execute(f"SELECT * FROM operations WHERE state IN ({','.join('?' for _ in states)}) ORDER BY created_at,op_id", states).fetchall()
        return [self.get_op(r["op_id"]) for r in rows]

    def grant_approval(self, op_kind, payload_hash, ttl_min, note="", now=None):
        if op_kind not in {"cal_personal", "cal_draft", "reminder", "notion", "send"} or int(ttl_min) <= 0:
            raise SchemaError("承認種別または期限が不正です")
        granted = datetime.fromisoformat(_now(now).replace("Z", "+00:00"))
        expires = (granted + timedelta(minutes=int(ttl_min))).isoformat()
        approval_id, at = secrets.token_hex(12), granted.isoformat()
        with self.tx():
            self.conn.execute("INSERT INTO approvals(approval_id,op_kind,payload_hash,granted_at,expires_at,state,note) VALUES(?,?,?,?,?,?,?)", (approval_id, op_kind, payload_hash, at, expires, "有効", note))
            self._record_history("approval:"+approval_id, {"approval": {"before": None, "after": {"op_kind": op_kind, "payload_hash": payload_hash, "expires_at": expires, "state": "有効"}}}, "承認を記録", at, "本人")
        return approval_id

    def get_approval(self, approval_id):
        row = self.conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        return dict(row) if row else None

    def use_approval(self, approval_id, now=None):
        at = _now(now)
        with self.tx():
            row = self.conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                raise KeyError(approval_id)
            if row["state"] != "有効":
                raise InvalidTransition(f"承認を使えません: {row['state']}")
            if datetime.fromisoformat(at.replace("Z", "+00:00")) >= datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")):
                self.conn.execute("UPDATE approvals SET state='期限切れ' WHERE approval_id=?", (approval_id,))
                self._record_history("approval:"+approval_id, {"state": {"before": "有効", "after": "期限切れ"}}, "承認期限切れ", at, "ボット")
                expired = True
            else:
                self.conn.execute("UPDATE approvals SET state='使用済み' WHERE approval_id=? AND state='有効'", (approval_id,))
                self._record_history("approval:"+approval_id, {"state": {"before": "有効", "after": "使用済み"}}, "承認を使用", at, "ボット")
                expired = False
        if expired:
            raise InvalidTransition("承認の期限が切れています")

    def source_seen(self, source_id, ok, at):
        state = "動いている" if ok else "止まっている"
        with self.tx():
            row = self.conn.execute("SELECT status,last_seen_at FROM sources WHERE source_id=?", (source_id,)).fetchone()
            if row is None:
                raise KeyError(source_id)
            self.conn.execute("UPDATE sources SET status=?,last_seen_at=? WHERE source_id=?", (state, at, source_id))
            self._record_history("source:"+source_id, {"source_status": {"before": row["status"], "after": state}, "last_seen_at": {"before": row["last_seen_at"], "after": at}}, "情報源の確認", at)

    def record_latency(self, req_id, stage, started_at, ended_at, version=None, ai_kind=None, cost_yen=0):
        if float(cost_yen) < 0:
            raise SchemaError("cost_yen は0以上です")
        with self.tx():
            old = self.conn.execute("SELECT * FROM latency WHERE req_id=? AND stage=?", (req_id, stage)).fetchone()
            vals = (started_at, ended_at, version, ai_kind, float(cost_yen), req_id, stage)
            self.conn.execute("INSERT INTO latency(started_at,ended_at,version,ai_kind,cost_yen,req_id,stage) VALUES(?,?,?,?,?,?,?) ON CONFLICT(req_id,stage) DO UPDATE SET started_at=excluded.started_at,ended_at=excluded.ended_at,version=excluded.version,ai_kind=excluded.ai_kind,cost_yen=excluded.cost_yen", vals)
            before = dict(old) if old else None
            after = {"req_id": req_id, "stage": stage, "started_at": started_at, "ended_at": ended_at, "version": version, "ai_kind": ai_kind, "cost_yen": float(cost_yen)}
            self._record_history("latency:"+req_id, {"latency": {"before": before, "after": after}}, "待ち時間を記録", started_at)
