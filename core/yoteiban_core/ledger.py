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

    def set_movement_points(self, case_id, points, now=None):
        """Persist only anonymous point order/type/signal/status metadata."""
        if not isinstance(points, list) or len(points) > 100:
            return {"ok": False, "reason": "地点一覧の形が正しくありません"}
        safe, seen = [], set()
        allowed_status = {"まだ", "着いた", "済んだ", "飛ばした", "わからない"}
        for point in points:
            if not isinstance(point, dict) or set(point) - {"point_number", "kind", "group_number", "has_signal", "status"}:
                return {"ok": False, "reason": "地点一覧に保存できない情報があります"}
            number = point.get("point_number")
            group = point.get("group_number")
            if (isinstance(number, bool) or not isinstance(number, int) or number < 1 or number in seen
                    or point.get("kind") not in {"errand", "through"}
                    or (group is not None and (isinstance(group, bool) or not isinstance(group, int) or group < 1))
                    or not isinstance(point.get("has_signal"), bool)
                    or point.get("status") not in allowed_status):
                return {"ok": False, "reason": "地点の番号・種類・状態が正しくありません"}
            seen.add(number)
            safe.append({"point_number": number, "kind": point["kind"], "group_number": group,
                         "has_signal": point["has_signal"], "status": point["status"]})
        case = self.get_case(case_id)
        if not case:
            return {"ok": False, "reason": "移動予定がありません"}
        try:
            extra = json.loads(case.get("extra") or "{}")
        except (TypeError, ValueError):
            return {"ok": False, "reason": "移動予定の記録を読めません"}
        if not isinstance(extra, dict) or extra.get("movement") is not True:
            return {"ok": False, "reason": "移動予定と確認できません"}
        move = extra.get("move_status") if isinstance(extra.get("move_status"), dict) else {}
        move["points"] = safe
        extra["move_status"] = move
        at = now or datetime.now().astimezone().isoformat()
        with self.tx():
            self.conn.execute("UPDATE cases SET extra=?,version=version+1,updated_at=? WHERE case_id=?",
                              (_json(extra), at, case_id))
            self._record_history(case_id, {"move_points": {"count": len(safe), "point_numbers": sorted(seen)}},
                                 "移動地点を記録", at)
        return {"ok": True, "points": len(safe)}

    def record_movement_signal(self, short_number, signal_type, received_at, now=None, point_number=None):
        """Store only the linked case, departure/arrival kind, and time; never a place."""
        if not isinstance(short_number, str) or not short_number.isdigit() or not 1 <= len(short_number) <= 6:
            return {"ok": False, "reason": "案件番号が正しくありません"}
        if signal_type not in {"出発", "到着"}:
            return {"ok": False, "reason": "合図の種類が正しくありません"}
        if point_number is not None and (isinstance(point_number, bool) or not isinstance(point_number, int) or point_number < 1):
            return {"ok": False, "reason": "地点番号が正しくありません"}
        try:
            parsed = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
        except ValueError:
            return {"ok": False, "reason": "合図の時刻が正しくありません"}
        if parsed.tzinfo is None:
            return {"ok": False, "reason": "合図の時刻に時差がありません"}
        candidates = []
        for row in self.conn.execute("SELECT case_id,extra,status FROM cases"):
            try:
                extra = json.loads(row["extra"] or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(extra, dict) and extra.get("movement") is True and str(extra.get("short_number")) == short_number:
                candidates.append((row["case_id"], extra))
        if len(candidates) != 1:
            return {"ok": False, "reason": "案件番号に一致する移動予定を一意に確認できません"}
        case_id, extra = candidates[0]
        owned_reminder = any(
            op.get("kind") == "reminder" and op.get("case_id") == case_id
            and op.get("state") == "成功（読み戻し済み）"
            and isinstance(op.get("result"), dict)
            and op["result"].get("grounds") == "move_watch"
            and op["result"].get("signal_type") == signal_type
            and op["result"].get("point_number") == point_number
            and isinstance(op["result"].get("reminder_id"), str)
            and not any(op["result"].get(k) for k in ("deleted_at", "completed_at", "replaced_by_op_id"))
            for op in self.list_ops()
        )
        if not owned_reminder:
            return {"ok": False, "reason": "予定ばんが作った有効な場所リマインダーがありません"}
        at = now or parsed.isoformat()
        with self.tx():
            old = self.conn.execute("SELECT extra,version FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if old is None:
                return {"ok": False, "reason": "移動予定がありません"}
            latest = json.loads(old["extra"] or "{}")
            move = latest.get("move_status") if isinstance(latest.get("move_status"), dict) else {}
            signals = list(move.get("signals", [])) if isinstance(move.get("signals", []), list) else []
            signal = {"type": signal_type, "at": parsed.isoformat()}
            if point_number is not None:
                signal["point_number"] = point_number
            if signal in signals:
                return {"ok": True, "created": False, "case_id": case_id, "status": move.get("status")}
            signals.append(signal)
            signals = signals[-100:]
            state = "到着済み" if signal_type == "到着" else "出発済み"
            points = move.get("points") if isinstance(move.get("points"), list) else []
            if signal_type == "到着" and points:
                point_groups = {p.get("group_number") for p in points if isinstance(p, dict)
                                and p.get("kind") == "errand" and isinstance(p.get("group_number"), int)}
                if point_number is None and len(point_groups) > 1:
                    return {"ok": False, "reason": "複数の用事地点には地点番号が必要です"}
                matched = [p for p in points if isinstance(p, dict)
                           and (p.get("group_number") == point_number if point_number is not None
                                else p.get("kind") == "errand" and p.get("group_number") is not None)]
                if point_number is not None and not matched:
                    return {"ok": False, "reason": "地点番号が一覧にありません"}
                if point_number is None:
                    groups = sorted({p.get("group_number") for p in matched if isinstance(p.get("group_number"), int)})
                    if len(groups) == 1:
                        point_number = groups[0]
                        signal["point_number"] = point_number
                target_group = point_number
                target_order = min((p["point_number"] for p in matched if target_group is not None
                                    and p.get("group_number") == target_group), default=None)
                for point in points:
                    if not isinstance(point, dict):
                        continue
                    if target_group is not None and point.get("group_number") == target_group:
                        point["status"] = "着いた"
                    elif target_order is not None and point.get("point_number", 0) < target_order \
                            and point.get("kind") == "through" and point.get("status") == "まだ":
                        point["status"] = "済んだ"
                move["points"] = points
            latest["move_status"] = {"status": state, "signals": signals}
            if points:
                latest["move_status"]["points"] = points
            updated_at = at
            self.conn.execute("UPDATE cases SET extra=?,version=version+1,updated_at=? WHERE case_id=?",
                              (_json(latest), updated_at, case_id))
            self._record_history(case_id, {"move_status": {"before": move.get("status"),
                                    "after": {"status": state, "signal": signal_type, "at": parsed.isoformat()}}},
                                 "移動の合図を記録", updated_at)
        return {"ok": True, "created": True, "case_id": case_id, "status": state}

    def record_departure_notice(self, short_number, received_at, now=None):
        """Record only the time of an owned departure-time reminder notification."""
        try:
            parsed = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
        except ValueError:
            return {"ok": False, "reason": "合図の時刻が正しくありません"}
        if parsed.tzinfo is None:
            return {"ok": False, "reason": "合図の時刻に時差がありません"}
        matches = []
        for case in self.list_cases():
            try:
                extra = json.loads(case.get("extra") or "{}")
            except (TypeError, ValueError):
                continue
            if extra.get("movement") is True and str(extra.get("short_number")) == str(short_number):
                matches.append((case, extra))
        if len(matches) != 1:
            return {"ok": False, "reason": "移動予定を一意に確認できません"}
        case, extra = matches[0]
        owned = any(op.get("kind") == "reminder" and op.get("case_id") == case["case_id"]
                    and op.get("state") == "成功（読み戻し済み）"
                    and isinstance(op.get("result"), dict)
                    and op["result"].get("grounds") == "departure_notice"
                    and op["result"].get("reminder_title") == f"出発の時間：#{short_number}"
                    and not op["result"].get("deleted_at") for op in self.list_ops())
        if not owned:
            return {"ok": False, "reason": "予定ばんが作った出発の知らせではありません"}
        extra["departure_notice_received_at"] = parsed.isoformat()
        at = now or parsed.isoformat()
        with self.tx():
            self.conn.execute("UPDATE cases SET extra=?,version=version+1,updated_at=? WHERE case_id=?",
                              (_json(extra), at, case["case_id"]))
            self._record_history(case["case_id"], {"departure_notice_at": parsed.isoformat()},
                                 "出発の知らせを記録", at)
        return {"ok": True, "case_id": case["case_id"], "received_at": parsed.isoformat()}

    def record_done_check_outcome(self, case_id, outcome, answered_at, reason=None, method="本人の答え", now=None,
                                  actual_duration_min=None):
        allowed = {"できた", "一部できた", "できなかった", "わからない", "移動済み", "削除済み", "終了時刻修正"}
        reasons = {"時間が足りなかった", "体調", "忘れていた", "ほかの用事が入った", "その他"}
        if outcome not in allowed or method not in {"場所の合図", "本人の答え", "カレンダー変更", "自動判定"}:
            return {"ok": False, "reason": "結果の種類が正しくありません"}
        if reason is not None and reason not in reasons:
            return {"ok": False, "reason": "理由は選択肢から指定してください"}
        if actual_duration_min is not None and (isinstance(actual_duration_min, bool)
                or not isinstance(actual_duration_min, int) or actual_duration_min < 0
                or outcome != "終了時刻修正" or method != "カレンダー変更"):
            return {"ok": False, "reason": "所要の実績値が正しくありません"}
        try:
            parsed = datetime.fromisoformat(str(answered_at).replace("Z", "+00:00"))
        except ValueError:
            return {"ok": False, "reason": "回答時刻が正しくありません"}
        if parsed.tzinfo is None:
            return {"ok": False, "reason": "回答時刻に時差がありません"}
        case = self.get_case(case_id)
        if not case:
            return {"ok": False, "reason": "予定がありません"}
        try:
            extra = json.loads(case.get("extra") or "{}")
        except (TypeError, ValueError):
            return {"ok": False, "reason": "予定の記録を読めません"}
        if not isinstance(extra, dict):
            return {"ok": False, "reason": "予定の記録が正しくありません"}
        check = {"outcome": outcome, "method": method, "answered_at": parsed.isoformat()}
        if reason is not None:
            check["reason"] = reason
        if actual_duration_min is not None:
            check["actual_duration_min"] = actual_duration_min
        extra["done_check"] = check
        at = now or parsed.isoformat()
        with self.tx():
            self.conn.execute("UPDATE cases SET extra=?,version=version+1,updated_at=? WHERE case_id=?",
                              (_json(extra), at, case_id))
            self._record_history(case_id, {"done_check": check}, "予定の結果を記録", at)
        return {"ok": True, "case_id": case_id, "outcome": outcome}

    def record_done_check_fired(self, short_number, received_at, now=None):
        """Record the delivery time of an owned, generic end-check reminder."""
        try:
            parsed = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
        except ValueError:
            return {"ok": False, "reason": "通知時刻が正しくありません"}
        if parsed.tzinfo is None:
            return {"ok": False, "reason": "通知時刻に時差がありません"}
        candidates = []
        for case in self.list_cases():
            try:
                extra = json.loads(case.get("extra") or "{}")
            except (TypeError, ValueError):
                continue
            if str(extra.get("short_number")) == str(short_number):
                candidates.append((case, extra))
        if len(candidates) != 1:
            return {"ok": False, "reason": "予定を一意に確認できません"}
        case, extra = candidates[0]
        owned = any(op.get("kind") == "reminder" and op.get("case_id") == case["case_id"]
                    and op.get("state") == "成功（読み戻し済み）"
                    and isinstance(op.get("result"), dict)
                    and op["result"].get("grounds") == "done_check"
                    and op["result"].get("reminder_title") == f"終わった？：#{short_number}"
                    and not any(op["result"].get(k) for k in ("completed_at", "deleted_at"))
                    for op in self.list_ops())
        if not owned:
            return {"ok": False, "reason": "予定ばんが作った有効な終了確認リマインダーではありません"}
        done = extra.get("done_check") if isinstance(extra.get("done_check"), dict) else {}
        done["fired_at"] = parsed.isoformat()
        extra["done_check"] = done
        at = now or parsed.isoformat()
        with self.tx():
            self.conn.execute("UPDATE cases SET extra=?,version=version+1,updated_at=? WHERE case_id=?",
                              (_json(extra), at, case["case_id"]))
            self._record_history(case["case_id"], {"done_check_fired_at": parsed.isoformat()},
                                 "終了確認の通知を記録", at)
        return {"ok": True, "case_id": case["case_id"], "fired_at": parsed.isoformat()}

    def record_movement_evaluation(self, case_id, evaluation, now=None, cover_proposed=False):
        """Persist time/status calibration only; never accept location or free-form text."""
        if not isinstance(evaluation, dict) or not isinstance(case_id, str):
            return {"ok": False, "reason": "判定の形が正しくありません"}
        allowed = {"status", "departure_late_min", "arrival_check", "estimated_arrival",
                   "monitoring_started_at", "observation_only", "record_only",
                   "propose_adjustment_and_cover", "calibration"}
        if set(evaluation) - allowed:
            return {"ok": False, "reason": "判定に保存できない項目があります"}
        case = self.get_case(case_id)
        if not case:
            return {"ok": False, "reason": "移動予定がありません"}
        try:
            extra = json.loads(case.get("extra") or "{}")
        except (TypeError, ValueError):
            return {"ok": False, "reason": "移動予定の記録を読めません"}
        if not isinstance(extra, dict) or extra.get("movement") is not True:
            return {"ok": False, "reason": "台帳で移動予定と確認できません"}
        calibration = evaluation.get("calibration")
        if not isinstance(calibration, dict) or set(calibration) - {
                "planned_departure", "actual_departure", "actual_arrival", "predicted_arrival",
                "next_event_start", "arrival_check"}:
            return {"ok": False, "reason": "校正記録の形が正しくありません"}
        if evaluation.get("status") not in {"出発前", "出発済み", "到着済み", "出発遅れ", "予定失敗"}:
            return {"ok": False, "reason": "進み具合の値が正しくありません"}
        if evaluation.get("arrival_check") not in {"未確認", "間に合う", "間に合わない", "予定失敗"}:
            return {"ok": False, "reason": "到着見込みの値が正しくありません"}
        late = evaluation.get("departure_late_min")
        if isinstance(late, bool) or not isinstance(late, int) or late < 0:
            return {"ok": False, "reason": "出発遅れの分数が正しくありません"}
        for key in ("planned_departure", "actual_departure", "actual_arrival", "predicted_arrival", "next_event_start"):
            value = calibration.get(key)
            if value is not None:
                try:
                    parsed = datetime.fromisoformat(value)
                except (TypeError, ValueError):
                    return {"ok": False, "reason": "時刻の校正値が正しくありません"}
                if parsed.tzinfo is None:
                    return {"ok": False, "reason": "時刻の校正値に時差がありません"}
        if calibration.get("arrival_check") not in {"未確認", "間に合う", "間に合わない", "予定失敗"}:
            return {"ok": False, "reason": "校正結果が正しくありません"}
        at = now or datetime.now().astimezone().isoformat()
        move = extra.get("move_status") if isinstance(extra.get("move_status"), dict) else {}
        checks = list(move.get("checks", [])) if isinstance(move.get("checks", []), list) else []
        prior_cover = bool(move.get("cover_proposed_at"))
        record = {"at": at, "status": evaluation.get("status"),
                  "departure_late_min": evaluation.get("departure_late_min"),
                  "arrival_check": evaluation.get("arrival_check"), "calibration": calibration}
        checks.append(record)
        move["checks"] = checks[-100:]
        if cover_proposed and evaluation.get("propose_adjustment_and_cover") and not prior_cover:
            move["cover_proposed_at"] = at
        move["status"] = evaluation.get("status")
        extra["move_status"] = move
        with self.tx():
            old = self.conn.execute("SELECT version FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if old is None:
                return {"ok": False, "reason": "移動予定がありません"}
            self.conn.execute("UPDATE cases SET extra=?,version=version+1,updated_at=? WHERE case_id=?",
                              (_json(extra), at, case_id))
            self._record_history(case_id, {"move_evaluation": {"status": evaluation.get("status"),
                                    "departure_late_min": evaluation.get("departure_late_min"),
                                    "arrival_check": evaluation.get("arrival_check"), "at": at}},
                                 "移動の進み具合を記録", at)
        return {"ok": True, "cover_already_proposed": bool(move.get("cover_proposed_at")),
                "checks": len(move["checks"])}

    def movement_calibration(self, case_id):
        case = self.get_case(case_id)
        if not case:
            return {"ok": False, "reason": "移動予定がありません"}
        try:
            extra = json.loads(case.get("extra") or "{}")
        except (TypeError, ValueError):
            return {"ok": False, "reason": "移動予定の記録を読めません"}
        move = extra.get("move_status", {}) if isinstance(extra, dict) else {}
        checks = move.get("checks", []) if isinstance(move, dict) else []
        if not isinstance(checks, list):
            checks = []
        return {"ok": True, "checks": len(checks),
                "late_departures": sum((x.get("departure_late_min") or 0) > 0 for x in checks if isinstance(x, dict)),
                "forecast_misses": sum(x.get("arrival_check") in {"間に合わない", "予定失敗"}
                                        for x in checks if isinstance(x, dict)),
                "cover_proposed": bool(move.get("cover_proposed_at")) if isinstance(move, dict) else False}

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
