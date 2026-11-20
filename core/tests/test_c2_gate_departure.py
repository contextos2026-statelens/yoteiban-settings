import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yoteiban_core.gate import check, payload_hash, result
from yoteiban_core.ledger import Ledger

NOW = "2026-11-19T08:00:00+09:00"


class DepartureGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(os.path.join(self.tmp.name, "departure.sqlite"))
        self.ledger.upsert_case({"case_id": "case-move", "kind": "予定", "title": "架空の訪問",
                                 "status": "決まった", "event_start": "2026-11-21T10:00:00+09:00",
                                 "extra": json.dumps({"movement": True, "short_number": "42"})}, now=NOW)
        self.cfg = {"cal_personal_id": "calendar-personal", "cal_readonly_ids": [],
                    "reminder_list_id": "reminder-default", "move_reminder_list": "reminder-move",
                    "reminder_readonly_list_ids": ["reminder-shared"], "departure_notice": "reminder"}
        self.calendar_op = self._calendar_write("2026-11-21T10:00:00+09:00")

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def _calendar_write(self, start, case_id="case-move"):
        payload = {"title": "架空の訪問", "event_start": start}
        approval = self.ledger.grant_approval("cal_personal", payload_hash(payload), 60, now=NOW)
        checked = check(self.ledger, self.cfg, {"kind": "cal_personal", "target_id": "calendar-personal",
                                                "case_id": case_id, "payload": payload,
                                                "approval_id": approval}, NOW)
        self.assertTrue(checked["allow"], checked)
        result(self.ledger, checked["op_id"], "ok", True, NOW)
        return checked["op_id"]

    def _reminder(self, action="add", target="reminder-move", due="2026-11-21T09:20:00+09:00",
                  linked=None, origin=None, reminder_id=None, notes=None, case_id="case-move"):
        payload = {"operation": action, "title": "出発の時間：#42", "due": due,
                   "notes": notes or "架空の訪問\nhttps://www.google.com/maps/dir/?api=1&destination=%E6%9E%B6%E7%A9%BA",
                   "reminder_id": reminder_id}
        if reminder_id is None:
            payload.pop("reminder_id")
        basis = {"travel_confirmed": True, "travel_minutes": 30, "prep_minutes": 10}
        if origin:
            basis["origin_reminder_op_id"] = origin
        return {"kind": "reminder", "target_id": target, "case_id": case_id,
                "linked_op_id": linked or self.calendar_op, "grounds": "departure_notice",
                "action": action, "basis": basis, "payload": payload}

    def test_confirmed_readback_departure_add_passes_without_per_item_approval(self):
        op = self._reminder()
        checked = check(self.ledger, self.cfg, op, NOW)
        self.assertTrue(checked["allow"], checked)
        self.assertEqual(checked["grounds"], "departure_notice")
        self.assertIsNone(self.ledger.get_op(checked["op_id"])["approval_id"])
        self.assertEqual(op["payload"]["title"], "出発の時間：#42")
        result(self.ledger, checked["op_id"], "ok", True, NOW, event_id="reminder-id-a")
        again = check(self.ledger, self.cfg, op, NOW)
        self.assertFalse(again["allow"])
        self.assertIn("成功済み", again["reason"])

    def test_undecided_or_unreadback_event_is_rejected(self):
        self.ledger.upsert_case({"case_id": "case-pending", "kind": "予定", "title": "架空の訪問",
                                 "status": "承認待ち", "event_start": "2026-11-21T10:00:00+09:00",
                                 "extra": json.dumps({"movement": True, "short_number": "43"})}, now=NOW)
        linked_pending = self._calendar_write("2026-11-21T10:00:00+09:00", case_id="case-pending")
        pending_op = self._reminder(linked=linked_pending, case_id="case-pending")
        pending_op["payload"]["title"] = "出発の時間：#43"
        self.assertFalse(check(self.ledger, self.cfg, pending_op, NOW)["allow"])
        self.assertFalse(check(self.ledger, self.cfg,
                              self._reminder(linked="missing-calendar-op"), NOW)["allow"])

    def test_shared_list_and_duplicate_reminder_are_rejected(self):
        shared = check(self.ledger, self.cfg, self._reminder(target="reminder-shared"), NOW)
        self.assertFalse(shared["allow"])
        first = check(self.ledger, self.cfg, self._reminder(), NOW)
        self.assertTrue(first["allow"], first)
        duplicate = self._reminder(notes="架空の訪問・更新\nhttps://www.google.com/maps/dir/?api=1")
        second = check(self.ledger, self.cfg, duplicate, NOW)
        self.assertFalse(second["allow"])
        self.assertIn("すでに", second["reason"])

    def test_title_cannot_contain_location_or_other_extra_text(self):
        op = self._reminder()
        op["payload"]["title"] = "出発の時間：#42 架空駅"
        checked = check(self.ledger, self.cfg, op, NOW)
        self.assertFalse(checked["allow"])
        self.assertIn("件名", checked["reason"])

    def test_update_and_cancellation_delete_use_gate_and_mark_old_reminder(self):
        add = check(self.ledger, self.cfg, self._reminder(), NOW)
        self.assertTrue(add["allow"], add)
        result(self.ledger, add["op_id"], "ok", True, NOW, event_id="reminder-id-a")
        self.ledger.upsert_case({"case_id": "case-move", "event_start": "2026-11-21T11:00:00+09:00"},
                                expected_version=1, now=NOW)
        linked_update = self._calendar_write("2026-11-21T11:00:00+09:00")
        update = check(self.ledger, self.cfg,
                       self._reminder("update", due="2026-11-21T10:20:00+09:00", linked=linked_update,
                                      origin=add["op_id"], reminder_id="reminder-id-a"), NOW)
        self.assertTrue(update["allow"], update)
        result(self.ledger, update["op_id"], "ok", True, NOW, event_id="reminder-id-b")
        self.assertEqual(self.ledger.get_op(add["op_id"])["result"]["replaced_by_op_id"], update["op_id"])
        self.ledger.upsert_case({"case_id": "case-move", "status": "取消"}, expected_version=2, now=NOW)
        delete = check(self.ledger, self.cfg,
                       self._reminder("delete", linked=linked_update, origin=update["op_id"],
                                      reminder_id="reminder-id-b"), NOW)
        self.assertTrue(delete["allow"], delete)
        result(self.ledger, delete["op_id"], "ok", True, NOW)
        self.assertIn("deleted_at", self.ledger.get_op(update["op_id"])["result"])


if __name__ == "__main__":
    unittest.main()
