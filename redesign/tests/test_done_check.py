import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "redesign" / "lib"))
from yoteiban_rd.done_check import (plan_points, select_departure_origin,
    build_current_departure_reminder, prioritize_reminders, update_point_progress,
    mark_point_skipped, evaluate_done_check, build_end_check_reminder,
    group_due_checks, cover_plan)
from yoteiban_rd.cli import COMMANDS
from yoteiban_rd.calc import CalcError


class DoneCheckTests(unittest.TestCase):
    def plan_input(self, **overrides):
        value = {"case_status": "決まった", "calendar": "personal", "personal_calendar_readback": True,
                 "movement_monitoring": "使う", "short_number": "12", "event_title": "架空用事",
                 "case_id": "fixture-case", "calendar_op_id": "fixture-calendar-op",
                 "move_reminder_list": "fixture-list",
                 "google_maps_url": "https://www.google.com/maps/dir/?api=1&destination=%E6%9E%B6%E7%A9%BA",
                 "departure_notice": "reminder", "place_group_meters": 200,
                 "points": [
                     {"order": 1, "name": "架空窓口A", "kind": "errand", "completion_inferable": True,
                      "location": {"lat": 0, "lon": 0}},
                     {"order": 2, "name": "架空乗換駅", "kind": "through", "location": {"lat": 1, "lon": 1}},
                     {"order": 3, "name": "架空窓口B", "kind": "errand", "completion_inferable": True,
                      "location": {"lat": 0, "lon": 0.0003}},
                     {"order": 4, "name": "架空会場", "kind": "errand", "completion_inferable": True,
                      "location": {"lat": 1, "lon": 1.01}},
                 ]}
        value.update(overrides)
        return value

    def test_point_plan_groups_only_close_errands_and_keeps_names_out_of_registry(self):
        result = plan_points(self.plan_input(previous_event={"same_day": True, "ends_before_departure": True,
                                                              "location": "架空直前地点"}))
        self.assertTrue(result["ok"])
        self.assertEqual([p["kind"] for p in result["point_registry"]], ["errand", "through", "errand", "errand"])
        self.assertEqual([p["group_number"] for p in result["point_registry"]], [1, None, 1, 2])
        arrivals = [r for r in result["reminders"] if r["payload"]["signal_type"] == "到着"]
        self.assertEqual([r["payload"]["title"] for r in arrivals], ["到着：#12-1", "到着：#12-2"])
        departures = [r for r in result["reminders"] if r["payload"]["signal_type"] == "出発"]
        self.assertEqual([r["payload"]["title"] for r in departures],
                         ["出発：#12", "出発：#12-1", "出発：#12-2"])
        for operation in result["reminders"]:
            self.assertNotIn("架空窓口", json.dumps(operation["payload"], ensure_ascii=False))
        self.assertFalse(any("架空" in json.dumps(p, ensure_ascii=False) for p in result["point_registry"]))
        self.assertIn("架空窓口A", arrivals[0]["runtime_input"]["notes"])
        self.assertEqual(result["reminders"][0]["origin_source"], "previous_event")

    def test_departure_origin_precedence_and_current_location_is_ephemeral(self):
        self.assertEqual(select_departure_origin({"previous_event": {"same_day": True,
            "ends_before_departure": True, "location": "架空前"}, "home_event_at_time": True,
            "home_location": "架空家"}), ("架空前", "previous_event"))
        self.assertEqual(select_departure_origin({"home_event_at_time": True,
            "home_location": "架空家"}), ("架空家", "home"))
        plan = plan_points(self.plan_input())
        self.assertTrue(plan["departure_after_notice"])
        self.assertFalse(any(r["payload"]["title"] == "出発：#12" for r in plan["reminders"]))
        live = build_current_departure_reminder({"departure_notice_received": True,
            "departure_notice": "reminder", "case_status": "決まった",
            "personal_calendar_readback": True, "movement_monitoring": "使う",
            "short_number": "12", "case_id": "fixture-case", "calendar_op_id": "fixture-op",
            "move_reminder_list": "fixture-list", "event_title": "架空用事", "google_maps_url": "fixture"})
        self.assertTrue(live["create"])
        runtime = live["operation"]["runtime_input"]
        self.assertEqual(runtime["current_location"], True)
        self.assertNotIn("location", runtime)
        self.assertFalse(build_current_departure_reminder({"short_number": "12"})["create"])

    def test_departure_fallback_failure_notifies_only_once(self):
        first = plan_points(self.plan_input(departure_notice="off"))
        second = plan_points(self.plan_input(departure_notice="off", departure_failure_notified=True))
        self.assertEqual(first["departure_notice_text"], "出発の合図は置けなかったので、着いた時の合図で見ます")
        self.assertIsNone(second["departure_notice_text"])
        self.assertTrue(second["departure_unavailable"])

    def test_only_errand_points_arrive_and_skipped_errand_stays_pending(self):
        points = plan_points(self.plan_input())["point_registry"]
        moved = update_point_progress(points, 2)
        self.assertEqual(moved[0]["status"], "まだ")
        self.assertEqual(moved[1]["status"], "済んだ")
        self.assertEqual(moved[2]["status"], "まだ")
        self.assertEqual(moved[3]["status"], "着いた")
        self.assertFalse(next(p for p in points if p["kind"] == "through")["has_signal"])
        self.assertEqual(mark_point_skipped(points, 1)[0]["status"], "飛ばした")
        with self.assertRaises(CalcError):
            update_point_progress(points, 9)

    def test_nearest_event_reminder_capacity_defers_the_rest_to_prior_night(self):
        events = [{"case_id": "later", "event_start": "2026-11-22T09:00:00+09:00",
                   "reminders": [{"point_number": 1}, {"point_number": 2}]},
                  {"case_id": "nearer", "event_start": "2026-11-21T09:00:00+09:00",
                   "reminders": [{"point_number": 1}, {"point_number": 2}]}]
        selected = prioritize_reminders(events, 2)
        self.assertEqual([x["case_id"] for x in selected["planned"]], ["nearer", "nearer"])
        self.assertEqual([x["case_id"] for x in selected["deferred_to_previous_night"]], ["later", "later"])

    def test_done_outcomes_known_without_asking_and_unknown_asks_only_remaining(self):
        base = {"done_check": "all", "owner": "self", "event_end": "2026-11-20T17:00:00+09:00",
                "end_check_due": True, "event_name": "架空用事"}
        arrived = evaluate_done_check(dict(base, arrived_at="2026-11-20T15:00:00+09:00",
             left_at="2026-11-20T17:10:00+09:00", completion_inferable=True))
        self.assertEqual((arrived["status"], arrived["ask"]), ("できた", False))
        from_signals = evaluate_done_check(dict(base, completion_inferable=True,
             points=[{"point_number": 1, "group_number": 1, "kind": "errand", "status": "着いた"}],
             move_signals=[{"type": "到着", "point_number": 1, "at": "2026-11-20T15:00:00+09:00"},
                           {"type": "出発", "point_number": 1, "at": "2026-11-20T17:10:00+09:00"}]))
        self.assertEqual((from_signals["status"], from_signals["ask"]), ("できた", False))
        shop = evaluate_done_check(dict(base, arrived_at="2026-11-20T15:00:00+09:00",
             left_at="2026-11-20T17:10:00+09:00", completion_inferable=False))
        self.assertTrue(shop["ask"])
        chat = evaluate_done_check(dict(base, chat_answer="できなかった"))
        self.assertEqual(chat["status"], "できなかった")
        changed_end = evaluate_done_check(dict(base, end_time_corrected=True,
            scheduled_start="2026-11-20T15:00:00+09:00", corrected_end="2026-11-20T16:20:00+09:00"))
        self.assertEqual(changed_end["actual_duration_min"], 80)
        self.assertTrue(changed_end["learn_duration"])
        skipped = evaluate_done_check(dict(base, points=[{"point_number": 1, "kind": "errand", "status": "まだ"},
            {"point_number": 2, "kind": "errand", "status": "済んだ", "display_name": "架空窓口B"}]))
        self.assertEqual(skipped["grouped_points"], [1])

    def test_excluded_events_important_mode_night_retry_and_unknown_close(self):
        base = {"done_check": "all", "owner": "self", "event_end": "2026-11-20T22:00:00+09:00",
                "end_check_due": True, "now": "2026-11-20T22:00:00+09:00",
                "is_quiet_now": True, "next_morning_at": "2026-11-21T08:00:00+09:00"}
        self.assertFalse(evaluate_done_check(dict(base, all_day=True))["eligible"])
        self.assertFalse(evaluate_done_check(dict(base, owner="other"))["eligible"])
        self.assertFalse(evaluate_done_check(dict(base, reply_or_check=True))["eligible"])
        self.assertFalse(evaluate_done_check(dict(base, cancelled=True))["eligible"])
        self.assertEqual(evaluate_done_check(dict(base, done_check="important", importance="低"))["status"], "対象外")
        self.assertIn("08:00", evaluate_done_check(base)["ask_at"])
        retry = evaluate_done_check(dict(base, asked_once=True))
        self.assertTrue(retry["final_prompt"])
        self.assertTrue(evaluate_done_check(dict(base, second_prompt_sent=True))["close"])
        self.assertEqual(evaluate_done_check(dict(base, calendar_moved=True))["status"], "移動済み")

    def test_group_due_checks_and_cover_proposal_are_one_shot_and_approval_gated(self):
        items = [{"due_at": "2026-11-20T17:14:00+09:00"}, {"due_at": "2026-11-20T17:00:00+09:00"},
                 {"due_at": "2026-11-20T17:30:00+09:00"}]
        self.assertEqual([len(x) for x in group_due_checks(items)], [2, 1])
        proposal = cover_plan({"status": "一部できた"})
        self.assertTrue(proposal["reason_question"])
        self.assertTrue(proposal["write_requires_approval"])
        self.assertFalse(proposal["send"])
        self.assertFalse(cover_plan({"status": "できなかった", "cover_already_proposed": True})["propose"])
        self.assertIn("done-check", COMMANDS)
        self.assertEqual(COMMANDS["done-check"]({"task": "cover", "status": "できなかった"})["send"], False)

    def test_end_check_reminder_uses_generic_title_and_rejects_excluded_cases(self):
        data = {"done_check": "all", "calendar": "personal", "personal_calendar_readback": True,
                "case_status": "決まった", "owner": "self", "short_number": "12",
                "case_id": "fixture-case", "calendar_op_id": "fixture-op",
                "move_reminder_list": "fixture-list", "event_end": "2026-11-20T17:00:00+09:00",
                "event_name": "架空窓口での受取"}
        result = build_end_check_reminder(data)
        self.assertTrue(result["create"])
        op = result["operation"]
        self.assertEqual(op["payload"]["title"], "終わった？：#12")
        self.assertNotIn("架空窓口", json.dumps(op, ensure_ascii=False))
        for excluded in ({"all_day": True}, {"owner": "other"}, {"reply_or_check": True}, {"cancelled": True}):
            self.assertFalse(build_end_check_reminder(dict(data, **excluded))["create"])
        self.assertFalse(build_end_check_reminder(dict(data, done_check="off"))["create"])
        household = dict(data, calendar="household", household=True, household_owner="self",
                         household_read_only=True)
        household.pop("calendar_op_id")
        self.assertTrue(build_end_check_reminder(household)["create"])


if __name__ == "__main__":
    unittest.main()
