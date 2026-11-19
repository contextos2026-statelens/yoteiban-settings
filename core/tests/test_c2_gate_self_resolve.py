import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import os as _os
import tempfile
import unittest

from yoteiban_core.gate import check, close_tentative, payload_hash, result
from yoteiban_core.ledger import Ledger


T1 = "2026-11-19T10:00:00+09:00"
T2 = "2026-11-19T10:05:00+09:00"
T3 = "2026-11-19T10:10:00+09:00"


class GateSelfResolveTests(unittest.TestCase):
    """予定ばんが自分で置いた［仮］の重なり・ダブりだけ、聞かずに動かす・消す。ほかは全部拒否。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(_os.path.join(self.tmp.name, "self_resolve.sqlite"))
        for i in range(8):
            self.ledger.upsert_case({"case_id": f"case-{i}", "kind": "予定", "title": "用事", "status": "決まった"}, now=T1)
        self.cfg = {"cal_personal_id": "calendar-personal", "cal_readonly_ids": ["calendar-household"],
                    "reminder_list_id": "reminder-dedicated", "reminder_readonly_list_ids": ["reminder-shared"],
                    "notion_db_ids": ["notion-db"], "trusted_source_ids": ["source-school"]}

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    # 下ごしらえ：例外の［仮］を置いて読み戻しまで済ませる
    def place(self, case, event_id, at, title="［仮］部屋の片付け", start="2026-11-22T10:00:00+09:00",
              end="2026-11-22T11:00:00+09:00", input_ref="input-1"):
        op = {"kind": "cal_personal", "target_id": "calendar-personal", "case_id": case, "grounds": "tentative",
              "payload": {"title": title, "event_start": start, "event_end": end},
              "basis": {"requested_by_owner": True, "owner_words": "週末に片付けしたい", "from_received": False,
                        "other_request": False, "needs_others": False, "needs_reservation": False,
                        "notify_style": "標準", "input_ref": input_ref}}
        out = check(self.ledger, self.cfg, op, at)
        self.assertTrue(out["allow"], out)
        result(self.ledger, out["op_id"], "ok", True, at, event_id=event_id)
        return out["op_id"]

    def move(self, origin, event_id="ev-1", case="case-5", title="［仮］部屋の片付け",
             start="2026-11-22T14:00:00+09:00", end="2026-11-22T15:00:00+09:00", action="move", **extra):
        value = {"kind": "cal_personal", "target_id": "calendar-personal", "case_id": case, "action": action,
                 "grounds": "self_resolve",
                 "payload": {"event_id": event_id, "title": title, "event_start": start, "event_end": end},
                 "basis": {"kind": "tentative_move", "origin_op_id": origin, "notify_style": "標準"}}
        value.update(extra)
        return value

    def dup(self, delete_op, keep_op, event_id="ev-2", case="case-6", **extra):
        value = {"kind": "cal_personal", "target_id": "calendar-personal", "case_id": case, "action": "delete",
                 "grounds": "self_resolve", "payload": {"event_id": event_id},
                 "basis": {"kind": "duplicate_delete", "origin_op_id": delete_op, "keep_op_id": keep_op,
                           "notify_style": "標準"}}
        value.update(extra)
        return value

    def denied(self, op, why=None, cfg=None, at=T3):
        out = check(self.ledger, cfg or self.cfg, op, at)
        self.assertFalse(out["allow"], out)
        if why:
            self.assertIn(why, out["reason"])
        return out

    # 通る
    def test_move_own_tentative_without_approval_records_before_after(self):
        origin = self.place("case-0", "ev-1", T1)
        out = check(self.ledger, self.cfg, self.move(origin), T2)
        self.assertTrue(out["allow"], out)
        self.assertEqual(out["grounds"], "self_resolve")
        self.assertEqual(out["notify_level"], "1行")
        self.assertEqual(out["before"]["event_start"], "2026-11-22T10:00:00+09:00")
        self.assertEqual(out["after"]["event_start"], "2026-11-22T14:00:00+09:00")
        row = self.ledger.get_op(out["op_id"])
        self.assertIsNone(row["approval_id"])
        self.assertEqual(row["result"]["before"]["event_id"], "ev-1")
        result(self.ledger, out["op_id"], "ok", True, T2)
        moved = self.ledger.get_op(origin)["result"]
        self.assertEqual(moved["current_start"], "2026-11-22T14:00:00+09:00")
        # 2回目の移動は、動かした後の時刻を「前」として記録する
        again = check(self.ledger, self.cfg, self.move(origin, start="2026-11-23T09:00:00+09:00",
                                                       end="2026-11-23T10:00:00+09:00"), T3)
        self.assertTrue(again["allow"], again)
        self.assertEqual(again["before"]["event_start"], "2026-11-22T14:00:00+09:00")

    def test_update_action_also_allowed_for_own_tentative(self):
        origin = self.place("case-0", "ev-1", T1)
        self.assertTrue(check(self.ledger, self.cfg, self.move(origin, action="update"), T2)["allow"])

    def test_duplicate_delete_later_one_from_same_input(self):
        first = self.place("case-0", "ev-1", T1)
        later = self.place("case-1", "ev-2", T2)
        out = check(self.ledger, self.cfg, self.dup(later, first), T3)
        self.assertTrue(out["allow"], out)
        self.assertIsNone(out["after"])
        self.assertEqual(out["before"]["event_id"], "ev-2")
        result(self.ledger, out["op_id"], "ok", True, T3)
        closed = self.ledger.get_op(later)["result"]
        self.assertTrue(closed["tentative_closed"])
        self.assertEqual(closed["closed_how"], "削除")
        self.assertFalse(self.ledger.get_op(first)["result"].get("tentative_closed", False))

    # 拒否
    def test_refuses_earlier_one_of_duplicate(self):
        first = self.place("case-0", "ev-1", T1)
        later = self.place("case-1", "ev-2", T2)
        self.denied(self.dup(first, later, event_id="ev-1"), why="後から置いた方")

    def test_refuses_duplicate_from_different_input(self):
        first = self.place("case-0", "ev-1", T1, input_ref="input-1")
        later = self.place("case-1", "ev-2", T2, input_ref="input-2")
        self.denied(self.dup(later, first), why="同じ入力")

    def test_refuses_duplicate_when_not_exact(self):
        first = self.place("case-0", "ev-1", T1)
        later = self.place("case-1", "ev-2", T2, start="2026-11-22T10:30:00+09:00", end="2026-11-22T11:30:00+09:00")
        self.denied(self.dup(later, first), why="まったく同じ")

    def test_refuses_approved_event(self):
        payload = {"title": "［仮］歯医者", "event_start": "2026-11-22T10:00:00+09:00"}
        appr = self.ledger.grant_approval("cal_personal", payload_hash(payload), 30, now=T1)
        out = check(self.ledger, self.cfg, {"kind": "cal_personal", "target_id": "calendar-personal",
                                            "case_id": "case-2", "payload": payload, "approval_id": appr}, T1)
        self.assertTrue(out["allow"], out)
        result(self.ledger, out["op_id"], "ok", True, T1, event_id="ev-9")
        self.denied(self.move(out["op_id"], event_id="ev-9"), why="予定ばんが置いた［仮］ではない")

    def test_refuses_forced_add_event(self):
        op = {"kind": "cal_personal", "target_id": "calendar-personal", "case_id": "case-3", "grounds": "forced_add",
              "payload": {"title": "［自動］保護者会", "event_start": "2026-11-25T14:00:00+09:00"},
              "basis": {"source_id": "source-school", "datetime_in_text": True, "guessed": False, "forwarded": False,
                        "group": False, "has_instruction": False, "notify_style": "標準"}}
        out = check(self.ledger, self.cfg, op, T1)
        self.assertTrue(out["allow"], out)
        result(self.ledger, out["op_id"], "ok", True, T1, event_id="ev-8")
        self.denied(self.move(out["op_id"], event_id="ev-8", title="［自動］保護者会"), why="予定ばんが置いた［仮］ではない")

    def test_refuses_event_id_mismatch_and_unknown_origin(self):
        origin = self.place("case-0", "ev-1", T1)
        self.denied(self.move(origin, event_id="ev-other"), why="一致しません")
        self.denied(self.move("no-such-op"), why="台帳にありません")
        self.denied(self.move(None), why="origin_op_id")

    def test_refuses_title_change_away_from_tentative(self):
        origin = self.place("case-0", "ev-1", T1)
        self.denied(self.move(origin, title="部屋の片付け"), why="［仮］")
        self.denied(self.move(origin, title="［自動］部屋の片付け"), why="［仮］")

    def test_refuses_other_calendars_and_family(self):
        origin = self.place("case-0", "ev-1", T1)
        self.denied(self.move(origin, target_id="calendar-household"), why="読み取り専用")
        self.denied(self.move(origin, target_id="calendar-other"), why="一致しません")
        self.denied(self.move(origin, kind="reminder", target_id="reminder-dedicated"))
        self.denied(self.move(origin, kind="cal_draft"), why="下書き")

    def test_refuses_invites_approval_mix_and_all_confirm(self):
        origin = self.place("case-0", "ev-1", T1)
        op = self.move(origin)
        op["payload"]["attendees"] = ["someone@example.com"]
        self.denied(op, why="招待")
        self.denied(self.move(origin, approval_id="appr-x"), why="一緒に使えません")
        op = self.move(origin)
        op["basis"]["notify_style"] = "全部確認"
        self.denied(op, why="全部確認")

    def test_refuses_wrong_action_and_unknown_kind(self):
        origin = self.place("case-0", "ev-1", T1)
        self.denied(self.move(origin, action="delete"), why="move か update")
        self.denied(self.move(origin, action="add"), why="move か update")
        op = self.move(origin)
        op["basis"]["kind"] = "anything"
        self.denied(op, why="basis.kind")
        later = self.place("case-1", "ev-2", T2)
        dup = self.dup(later, origin)
        dup["action"] = "move"
        self.denied(dup, why="delete だけ")

    def test_refuses_closed_or_unverified_tentative(self):
        origin = self.place("case-0", "ev-1", T1)
        close_tentative(self.ledger, origin, "確定", T2)
        self.denied(self.move(origin), why="閉じています")
        # 読み戻し前（実行中）の［仮］も聞かずには直さない
        op = {"kind": "cal_personal", "target_id": "calendar-personal", "case_id": "case-4", "grounds": "tentative",
              "payload": {"title": "［仮］買い物", "event_start": "2026-11-24T10:00:00+09:00"},
              "basis": {"requested_by_owner": True, "owner_words": "買い物に行きたい", "from_received": False,
                        "other_request": False, "needs_others": False, "needs_reservation": False,
                        "notify_style": "標準"}}
        running = check(self.ledger, self.cfg, op, T2)["op_id"]
        self.denied(self.move(running), why="確かめ済み")

    def test_refuses_without_recorded_event_id(self):
        op = {"kind": "cal_personal", "target_id": "calendar-personal", "case_id": "case-4", "grounds": "tentative",
              "payload": {"title": "［仮］買い物", "event_start": "2026-11-24T10:00:00+09:00"},
              "basis": {"requested_by_owner": True, "owner_words": "買い物に行きたい", "from_received": False,
                        "other_request": False, "needs_others": False, "needs_reservation": False,
                        "notify_style": "標準"}}
        out = check(self.ledger, self.cfg, op, T1)
        result(self.ledger, out["op_id"], "ok", True, T1)
        self.denied(self.move(out["op_id"]), why="予定IDが記録されていません")

    def test_bad_times_refused(self):
        origin = self.place("case-0", "ev-1", T1)
        self.denied(self.move(origin, start="2026-11-22"), why="日付と時刻")
        self.denied(self.move(origin, end="2026-11-22T13:00:00+09:00"), why="終わり")


if __name__ == "__main__":
    unittest.main()
