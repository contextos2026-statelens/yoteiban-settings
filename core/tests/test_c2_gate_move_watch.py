import json
import os
import tempfile
import unittest
from pathlib import Path

from yoteiban_core.gate import check, payload_hash, result
from yoteiban_core.ledger import Ledger

NOW = "2026-11-20T09:00:00+09:00"


class MoveWatchGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "fixture.sqlite")
        self.ledger.upsert_case({"case_id": "case-fixture", "kind": "予定", "title": "架空会場の面談",
                                 "status": "決まった", "event_start": "2026-11-21T10:00:00+09:00",
                                 "event_end": "2026-11-21T11:00:00+09:00",
                                 "extra": json.dumps({"movement": True, "short_number": "12"})}, now=NOW)
        self.cfg = {"cal_personal_id": "calendar-personal", "cal_readonly_ids": [],
                    "move_reminder_list": "reminder-fixture-list", "reminder_list_id": "reminder-default",
                    "reminder_readonly_list_ids": ["reminder-shared"], "movement_monitoring": "使う",
                    "done_check": "all", "departure_notice": "reminder"}
        cal = {"title": "架空会場の面談", "event_start": "2026-11-21T10:00:00+09:00"}
        approval = self.ledger.grant_approval("cal_personal", payload_hash(cal), 60, now=NOW)
        checked = check(self.ledger, self.cfg, {"kind": "cal_personal", "target_id": "calendar-personal",
                                               "case_id": "case-fixture", "payload": cal,
                                               "approval_id": approval}, NOW)
        self.assertTrue(checked["allow"], checked)
        result(self.ledger, checked["op_id"], "ok", True, NOW)
        self.calendar_op = checked["op_id"]

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def op(self, signal="出発", action="add", origin=None, target="reminder-fixture-list"):
        title = ("出発：#12" if signal == "出発" else "到着：#12")
        payload = {"operation": action, "signal_type": signal, "title": title}
        basis = {}
        runtime = None
        if action in {"add", "update"}:
            runtime = {"location": "架空駅A" if signal == "出発" else {"lat": 35.0, "lon": 139.0},
                       "trigger": "leave" if signal == "出発" else "arrive",
                       "notes": "架空会場の面談\nhttps://www.google.com/maps/dir/?api=1&destination=%E6%9E%B6%E7%A9%BA"}
        if origin:
            basis["origin_reminder_op_id"] = origin
            payload["reminder_id"] = (self.ledger.get_op(origin).get("result") or {}).get("reminder_id")
        op = {"kind": "reminder", "grounds": "move_watch", "target_id": target,
              "case_id": "case-fixture", "linked_op_id": self.calendar_op,
              "action": action, "basis": basis, "payload": payload}
        if runtime is not None:
            op["runtime_input"] = runtime
        return op

    def create(self, signal="出発"):
        checked = check(self.ledger, self.cfg, self.op(signal), NOW)
        self.assertTrue(checked["allow"], checked)
        op = self.ledger.get_op(checked["op_id"])
        self.assertNotIn("架空駅", json.dumps(op.get("result"), ensure_ascii=False))
        self.assertNotIn("139.0", json.dumps(op.get("result"), ensure_ascii=False))
        result(self.ledger, checked["op_id"], "ok", True, NOW, event_id="reminder-fixture-id")
        return checked["op_id"]

    def test_departure_and_arrival_adds_are_allowed_but_duplicates_are_not(self):
        for kind in ("出発", "到着"):
            with self.subTest(kind=kind):
                op = self.op(kind)
                checked = check(self.ledger, self.cfg, op, NOW)
                self.assertTrue(checked["allow"], checked)
                self.assertEqual(checked["grounds"], "move_watch")
                result(self.ledger, checked["op_id"], "ok", True, NOW, event_id="reminder-fixture-id-" + kind)
                self.assertFalse(check(self.ledger, self.cfg, op, NOW)["allow"])

    def test_missing_conditions_shared_list_and_location_in_persisted_payload_are_denied(self):
        self.assertFalse(check(self.ledger, dict(self.cfg, movement_monitoring="使わない"), self.op(), NOW)["allow"])
        self.assertFalse(check(self.ledger, self.cfg, self.op(target="reminder-shared"), NOW)["allow"])
        bad = self.op()
        bad["payload"]["location"] = "架空駅A"
        self.assertFalse(check(self.ledger, self.cfg, bad, NOW)["allow"])
        bad_case = {"case_id": "case-not-decided", "kind": "予定", "title": "予定", "status": "承認待ち",
                    "extra": json.dumps({"movement": True, "short_number": "13"})}
        self.ledger.upsert_case(bad_case, now=NOW)
        op = self.op()
        op["case_id"] = "case-not-decided"
        op["payload"]["title"] = "出発：#13"
        self.assertFalse(check(self.ledger, self.cfg, op, NOW)["allow"])

    def test_only_own_reminder_can_be_completed_and_repeat_is_rejected(self):
        origin = self.create("出発")
        complete = check(self.ledger, self.cfg, self.op("出発", "complete", origin), NOW)
        self.assertFalse(complete["allow"])
        self.ledger.record_movement_signal("12", "出発", NOW, NOW)
        complete = check(self.ledger, self.cfg, self.op("出発", "complete", origin), NOW)
        self.assertTrue(complete["allow"], complete)
        result(self.ledger, complete["op_id"], "ok", True, NOW)
        again = self.op("出発", "complete", origin)
        self.assertFalse(check(self.ledger, self.cfg, again, NOW)["allow"])
        foreign = self.op("到着", "complete", origin)
        self.assertFalse(check(self.ledger, self.cfg, foreign, NOW)["allow"])

    def test_cancel_delete_requires_owned_successful_reminder(self):
        origin = self.create("到着")
        case = self.ledger.get_case("case-fixture")
        self.ledger.upsert_case({"case_id": "case-fixture", "status": "取消"}, expected_version=case["version"], now=NOW)
        deleted = check(self.ledger, self.cfg, self.op("到着", "delete", origin), NOW)
        self.assertTrue(deleted["allow"], deleted)
        result(self.ledger, deleted["op_id"], "ok", True, NOW)
        self.assertIn("deleted_at", self.ledger.get_op(origin)["result"])

    def test_signal_records_only_case_kind_and_timestamp_for_owned_reminder(self):
        self.assertFalse(self.ledger.record_movement_signal("12", "出発", NOW, NOW)["ok"])
        self.create("出発")
        recorded = self.ledger.record_movement_signal("12", "出発", NOW, NOW)
        self.assertTrue(recorded["created"])
        self.assertFalse(self.ledger.record_movement_signal("12", "出発", NOW, NOW)["created"])
        case = self.ledger.get_case("case-fixture")
        extra = json.loads(case["extra"])
        self.assertEqual(extra["move_status"]["signals"], [{"type": "出発", "at": NOW}])
        self.assertNotIn("架空駅", case["extra"])
        self.assertNotIn("139.0", case["extra"])

    def test_evaluation_audit_is_sanitized_and_one_cover_proposal_is_recorded(self):
        evaluation = {"status": "出発遅れ", "departure_late_min": 5,
                      "arrival_check": "間に合わない", "estimated_arrival": "2026-11-20T09:40:00+09:00",
                      "monitoring_started_at": "2026-11-01T00:00:00+09:00", "observation_only": False,
                      "record_only": False, "propose_adjustment_and_cover": True,
                      "calibration": {"planned_departure": "2026-11-20T09:00:00+09:00",
                                      "actual_departure": None, "actual_arrival": None,
                                      "predicted_arrival": "2026-11-20T09:40:00+09:00",
                                      "next_event_start": "2026-11-20T09:30:00+09:00",
                                      "arrival_check": "間に合わない"}}
        saved = self.ledger.record_movement_evaluation("case-fixture", evaluation, NOW, cover_proposed=True)
        self.assertTrue(saved["ok"])
        self.assertTrue(self.ledger.movement_calibration("case-fixture")["cover_proposed"])
        again = self.ledger.record_movement_evaluation("case-fixture", evaluation, NOW, cover_proposed=True)
        self.assertTrue(again["cover_already_proposed"])
        self.assertEqual(again["checks"], 2)
        case = self.ledger.get_case("case-fixture")
        self.assertNotIn("架空駅", case["extra"])
        self.assertNotIn("139.0", case["extra"])
        bad = dict(evaluation, status="遅れ：架空駅")
        self.assertFalse(self.ledger.record_movement_evaluation("case-fixture", bad, NOW)["ok"])

    def test_point_number_arrival_and_current_location_departure_after_notice(self):
        self.assertTrue(self.ledger.set_movement_points("case-fixture", [
            {"point_number": 1, "kind": "through", "group_number": None, "has_signal": False, "status": "まだ"},
            {"point_number": 2, "kind": "errand", "group_number": 1, "has_signal": True, "status": "まだ"}], NOW)["ok"])
        op = self.op("到着")
        op["payload"].update(point_number=2, title="到着：#12-2")
        self.assertFalse(check(self.ledger, self.cfg, op, NOW)["allow"])
        op["payload"].update(point_number=1, title="到着：#12-1")
        self.ledger.set_movement_points("case-fixture", [
            {"point_number": 1, "kind": "errand", "group_number": 1, "has_signal": True, "status": "まだ"}], NOW)
        checked = check(self.ledger, self.cfg, op, NOW)
        self.assertTrue(checked["allow"], checked)
        self.assertEqual(checked["point_number"], 1)
        result(self.ledger, checked["op_id"], "ok", True, NOW, event_id="point-reminder-id")
        signal = self.ledger.record_movement_signal("12", "到着", NOW, NOW, point_number=1)
        self.assertTrue(signal["ok"])
        points = json.loads(self.ledger.get_case("case-fixture")["extra"])["move_status"]["points"]
        self.assertEqual(points[0]["status"], "着いた")

        no_notice = self.op("出発")
        no_notice["runtime_input"] = {"current_location": True, "trigger": "leave",
                                       "notes": "架空会場の面談\nhttps://www.google.com/maps/dir/?api=1"}
        self.assertFalse(check(self.ledger, self.cfg, no_notice, NOW)["allow"])
        departure_op, _ = self.ledger.reserve_op("reminder", "reminder-fixture-list", "case-fixture",
                                                  "departure-notice-fixture", linked_op_id=self.calendar_op, now=NOW)
        self.ledger.set_op_state(departure_op, "実行中", now=NOW)
        self.ledger.set_op_state(departure_op, "成功（読み戻し済み）",
            result={"grounds": "departure_notice", "action": "add",
                    "reminder_title": "出発の時間：#12", "reminder_id": "departure-time-fixture"}, now=NOW)
        self.assertTrue(self.ledger.record_departure_notice("12", NOW, NOW)["ok"])
        with_notice = check(self.ledger, self.cfg, no_notice, NOW)
        self.assertTrue(with_notice["allow"], with_notice)

    def test_end_check_gate_uses_only_owned_readback_reminder_and_minimal_title(self):
        payload = {"operation": "add", "title": "終わった？：#12", "due": "2026-11-21T11:00:00+09:00"}
        op = {"kind": "reminder", "grounds": "done_check", "target_id": "reminder-fixture-list",
              "case_id": "case-fixture", "linked_op_id": self.calendar_op, "action": "add", "payload": payload}
        checked = check(self.ledger, self.cfg, op, NOW)
        self.assertTrue(checked["allow"], checked)
        result(self.ledger, checked["op_id"], "ok", True, NOW, event_id="done-fixture-id")
        self.assertFalse(check(self.ledger, self.cfg, op, NOW)["allow"])
        raw = json.dumps(self.ledger.get_op(checked["op_id"])["result"], ensure_ascii=False)
        self.assertNotIn("架空会場", raw)
        self.assertNotIn("座標", raw)

        self.assertTrue(self.ledger.record_done_check_fired("12", NOW, NOW)["ok"])
        self.assertTrue(self.ledger.record_done_check_outcome("case-fixture", "できなかった", NOW,
                                                              "時間が足りなかった")["ok"])
        complete = {"kind": "reminder", "grounds": "done_check", "target_id": "reminder-fixture-list",
                    "case_id": "case-fixture", "action": "complete",
                    "basis": {"origin_reminder_op_id": checked["op_id"]},
                    "payload": {"operation": "complete", "title": "終わった？：#12", "reminder_id": "done-fixture-id"}}
        self.assertTrue(check(self.ledger, self.cfg, complete, NOW)["allow"])
        self.assertFalse(check(self.ledger, self.cfg, dict(complete, target_id="reminder-shared"), NOW)["allow"])


if __name__ == "__main__":
    unittest.main()
