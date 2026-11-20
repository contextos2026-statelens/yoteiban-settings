import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "redesign" / "lib"))
from yoteiban_rd.move_watch import build_location_reminders, evaluate_move_status
from yoteiban_rd.cli import COMMANDS


class MoveWatchTests(unittest.TestCase):
    def input(self, **overrides):
        data = {"case_status": "決まった", "calendar": "personal", "personal_calendar_readback": True,
                "movement_monitoring": "使う", "move_reminder_list": "fixture-list", "already_exists": False,
                "case_id": "case-fixture", "short_number": "12", "event_title": "架空会場の予定",
                "google_maps_url": "https://www.google.com/maps/dir/?api=1&destination=%E6%9E%B6%E7%A9%BA",
                "origin_location": "架空駅A", "destination_location": {"lat": 35.0, "lon": 139.0},
                "calendar_op_id": "calendar-op-fixture"}
        data.update(overrides)
        return data

    def test_builds_exactly_two_minimal_titles_and_keeps_place_only_in_runtime_input(self):
        result = build_location_reminders(self.input())
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["reminders"]), 2)
        depart, arrive = result["reminders"]
        self.assertEqual((depart["payload"]["title"], arrive["payload"]["title"]),
                         ("出発：#12", "到着：#12"))
        self.assertEqual((depart["runtime_input"]["trigger"], arrive["runtime_input"]["trigger"]),
                         ("leave", "arrive"))
        self.assertEqual(depart["runtime_input"]["location"], "架空駅A")
        self.assertEqual(arrive["runtime_input"]["location"], {"lat": 35.0, "lon": 139.0})
        self.assertIn("架空会場の予定", depart["runtime_input"]["notes"])
        self.assertIn("https://www.google.com/maps/dir/?api=1", depart["runtime_input"]["notes"])
        self.assertNotIn("架空駅", json.dumps(depart["payload"], ensure_ascii=False))
        self.assertNotIn("139.0", json.dumps(result.get("ledger", {})))

    def test_does_not_build_if_any_required_condition_is_missing(self):
        for changes in ({"case_status": "承認待ち"}, {"calendar": "household"},
                        {"personal_calendar_readback": False}, {"movement_monitoring": "使わない"},
                        {"already_exists": True}, {"move_reminder_list": ""}):
            with self.subTest(changes=changes):
                result = build_location_reminders(self.input(**changes))
                self.assertFalse(result["create"])
                self.assertEqual(result["reminders"], [])
                self.assertEqual(result["notify_owner"], "場所のリマインダーを作れませんでした")

    def test_calculator_reports_lateness_feasibility_and_trial_window(self):
        base = {"now": "2026-11-20T09:20:00+09:00", "scheduled_departure": "2026-11-20T09:00:00+09:00",
                "scheduled_start": "2026-11-20T09:40:00+09:00", "travel_confirmed": True,
                "travel_minutes": 30, "next_event_start": "2026-11-20T09:40:00+09:00",
                "monitoring_started_at": "2026-11-01T00:00:00+09:00", "event_key": "fixture-case"}
        delayed = evaluate_move_status(base)
        self.assertEqual(delayed["departure_late_min"], 20)
        self.assertEqual(delayed["arrival_check"], "間に合わない")
        self.assertTrue(delayed["propose_adjustment_and_cover"])
        self.assertEqual(delayed["proposal_key"], "fixture-case")
        on_time = evaluate_move_status(dict(base, departure_signal_at="2026-11-20T09:05:00+09:00",
                                            arrival_signal_at="2026-11-20T09:30:00+09:00"))
        self.assertEqual(on_time["arrival_check"], "間に合う")

    def test_first_two_weeks_are_record_only_then_only_one_cover_proposal(self):
        data = {"now": "2026-11-10T09:30:00+09:00", "scheduled_departure": "2026-11-10T09:00:00+09:00",
                "scheduled_start": "2026-11-10T10:00:00+09:00", "travel_confirmed": True,
                "travel_minutes": 30, "next_event_start": "2026-11-10T09:20:00+09:00",
                "monitoring_started_at": "2026-11-01T00:00:00+09:00", "event_key": "fixture-case"}
        first = evaluate_move_status(data)
        self.assertTrue(first["record_only"])
        self.assertFalse(first["propose_adjustment_and_cover"])
        data.update(now="2026-11-16T00:00:00+09:00")
        after = evaluate_move_status(data)
        self.assertFalse(after["record_only"])
        self.assertTrue(after["propose_adjustment_and_cover"])
        repeated = evaluate_move_status(dict(data, cover_already_proposed=True))
        self.assertFalse(repeated["propose_adjustment_and_cover"])

    def test_cli_command_is_registered_and_offline(self):
        self.assertIn("move-watch", COMMANDS)
        out = COMMANDS["move-watch"]({"task": "status", "now": "2026-11-20T09:20:00+09:00",
                "scheduled_departure": "2026-11-20T09:00:00+09:00", "scheduled_start": "2026-11-20T10:00:00+09:00",
                "travel_confirmed": False, "travel_minutes": None})
        self.assertEqual(out["arrival_check"], "未確認")
        self.assertFalse(out["propose_adjustment_and_cover"])
