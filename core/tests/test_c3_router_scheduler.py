import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import time as clock
import unittest
from yoteiban_core.router import route
from yoteiban_core.scheduler import plan


class RouterSchedulerTests(unittest.TestCase):
    def test_thirty_routing_examples(self):
        cases = [
            ({"reply_only":True}, "すぐ"), ({"writing_only":True}, "すぐ"), ({"intent":"返事だけ"}, "すぐ"),
            ({"one_person":True, "one_slot_search":True}, "簡単"), ({"people_count":1}, "簡単"),
            ({"intent":"1人の空き探し"}, "簡単"),
            ({"multiple_people":True}, "難しい"), ({"people_count":3}, "難しい"),
            ({"multiple_conditions":True}, "難しい"), ({"conditions":[1,2]}, "難しい"),
            ({"travel":True}, "難しい"), ({"family_calendar":True}, "難しい"),
            ({"intent":"家族の予定と移動あり"}, "難しい"),
            ({"research_days":2}, "もっと難しい"), ({"plan_days":5}, "もっと難しい"),
            ({"comparison_count":3}, "もっと難しい"), ({"options_count":5}, "もっと難しい"),
            ({"long_research":True}, "もっと難しい"), ({"many_comparisons":True}, "もっと難しい"),
            ({"reply_only":True, "uncertain":True}, "簡単"),
            ({"one_person":True, "uncertain":True}, "難しい"),
            ({"multiple_people":True, "uncertain":True}, "もっと難しい"),
            ({"reply_only":True, "multiple_people":True}, "難しい"),
            ({"one_person":True, "travel":True}, "難しい"),
            ({"comparison_count":2}, "簡単"), ({"research_days":1}, "簡単"),
            ({"conditions":[1,2,3], "reply_only":True}, "難しい"),
            ({"intent":"作文だけ"}, "すぐ"), ({"intent":"下調べ"}, "もっと難しい"),
            ({"description":"条件未確認"}, "難しい"),
        ]
        self.assertEqual(len(cases), 30)
        for index, (signals, expected) in enumerate(cases, 1):
            with self.subTest(example=index):
                self.assertEqual(route({}, signals)["level"], expected)
                self.assertTrue(route({}, signals)["why"])

    def test_router_thresholds_are_configurable(self):
        self.assertEqual(route({}, {"comparison_count":2})["level"], "簡単")
        self.assertEqual(route({}, {"comparison_count":2}, {"comparison_threshold":2})["level"], "もっと難しい")
        self.assertEqual(route({}, {})["level"], "難しい")

    def test_scheduler_obeys_fixed_time_window_order_and_worst_duration(self):
        tasks = [
            {"id":"a", "title":"準備", "dur":[30,45,60], "deadline":"2026-11-18T12:00:00+09:00"},
            {"id":"b", "title":"提出", "dur":[30,60,90], "after":["a"], "deadline":"2026-11-18T16:00:00+09:00"},
        ]
        fixed = [{"id":"busy", "title":"固定予定", "start":"2026-11-18T10:00:00+09:00", "end":"2026-11-18T11:00:00+09:00"}]
        candidates = plan(tasks, fixed, {"start":"2026-11-18T09:00:00+09:00", "end":"2026-11-18T17:00:00+09:00"}, {}, {})
        self.assertIsInstance(candidates, list)
        self.assertLessEqual(len(candidates), 3)
        assignment = candidates[0]["assignments"]
        a, b = {item["id"]:item for item in assignment}["a"], {item["id"]:item for item in assignment}["b"]
        self.assertLessEqual(a["end"], "2026-11-18T10:00:00+09:00")
        self.assertGreaterEqual(b["start"], "2026-11-18T11:00:00+09:00")
        self.assertEqual(a["duration_min"], 60)
        self.assertEqual(b["duration_min"], 90)
        self.assertLessEqual(a["end"], b["start"])
        self.assertTrue(a["deadline_safe"] and b["deadline_safe"])

    def test_scheduler_enforces_required_time_windows_and_after_constraints(self):
        tasks = [
            {"id":"first", "title":"先", "dur":[30,30,30]},
            {"id":"next", "title":"後", "dur":[30,30,30]},
        ]
        constraints = {"required":[{"type":"time_window", "task_id":"first", "start":"2026-11-18T13:00:00+09:00", "end":"2026-11-18T14:00:00+09:00"},
                                    {"type":"after", "task_id":"next", "after":"first"}]}
        result = plan(tasks, [], {"start":"2026-11-18T09:00:00+09:00", "end":"2026-11-18T17:00:00+09:00"}, constraints, {})
        first, next_ = result[0]["assignments"]
        self.assertGreaterEqual(first["start"], "2026-11-18T13:00:00+09:00")
        self.assertGreaterEqual(next_["start"], first["end"])

    def test_scheduler_respects_travel_and_rejects_impossible_deadline(self):
        tasks = [
            {"id":"a", "title":"場所A", "dur":[60,60,60], "place_kind":"A"},
            {"id":"b", "title":"場所B", "dur":[30,30,30], "place_kind":"B"},
        ]
        result = plan(tasks, [], {"start":"2026-11-18T09:00:00+09:00", "end":"2026-11-18T12:00:00+09:00"},
                      {"required":[{"type":"after", "task_id":"b", "after":"a"}]},
                      {"travel_min":{"A|B":20}, "travel_margin_min":10})
        assign = {item["id"]:item for item in result[0]["assignments"]}
        self.assertGreaterEqual(assign["b"]["start"], "2026-11-18T10:30:00+09:00")
        impossible = plan([{ "id":"hard", "title":"期限直前", "dur":[30,60,120], "deadline":"2026-11-18T10:00:00+09:00"}], [],
                          {"start":"2026-11-18T09:00:00+09:00", "end":"2026-11-18T12:00:00+09:00"}, {}, {})
        self.assertFalse(impossible["ok"])
        self.assertIn("悲観所要", impossible["why"])

    def test_required_travel_rule_and_preference_points_are_used(self):
        tasks = [{"id":"a", "title":"場所A", "dur":[60,60,60], "place_kind":"A"},
                 {"id":"b", "title":"場所B", "dur":[30,30,30], "place_kind":"B"}]
        window = {"start":"2026-11-18T08:00:00+09:00", "end":"2026-11-18T12:00:00+09:00"}
        constraints = {"required":[{"type":"after", "task_id":"b", "after":"a"},
                                    {"type":"travel", "from_kind":"A", "to_kind":"B", "minutes":30}],
                       "preferences":[{"type":"morning", "start":"09:00", "weight":7}]}
        result = plan(tasks, [], window, constraints, {})
        self.assertGreaterEqual(result[0]["score"], 0)
        assignment = {item["id"]:item for item in result[0]["assignments"]}
        self.assertGreaterEqual(assignment["b"]["start"], "2026-11-18T10:30:00+09:00")

        options = plan([{"id":"one", "title":"作業", "dur":[30,30,30]}], [], window,
                       {"preferences":[{"type":"morning", "start":"09:00", "weight":7}]}, {})
        self.assertEqual(options[0]["assignments"][0]["start"], "2026-11-18T09:00:00+09:00")
        self.assertEqual(options[0]["score"], 7)

    def test_scheduler_returns_reason_for_cycles_and_missing_prerequisites(self):
        window = {"start":"2026-11-18T09:00:00+09:00", "end":"2026-11-18T17:00:00+09:00"}
        cycle = plan([{ "id":"a", "title":"A", "dur":[1,1,1], "after":["b"]},
                      {"id":"b", "title":"B", "dur":[1,1,1], "after":["a"]}], [], window, {}, {})
        missing = plan([{ "id":"a", "title":"A", "dur":[1,1,1], "after":["x"]}], [], window, {}, {})
        self.assertFalse(cycle["ok"])
        self.assertIn("循環", cycle["why"])
        self.assertFalse(missing["ok"])
        self.assertIn("ありません", missing["why"])

    def test_twenty_tasks_fourteen_days_under_five_seconds(self):
        start = "2026-11-18T08:00:00+09:00"
        end = "2026-12-02T20:00:00+09:00"
        tasks = [{"id":f"task-{i:02d}", "title":f"作業{i}", "dur":[15,30,45], "after":[f"task-{i-1:02d}"] if i else []}
                 for i in range(20)]
        started = clock.perf_counter()
        result = plan(tasks, [], {"start":start, "end":end}, {}, {})
        elapsed = clock.perf_counter() - started
        self.assertIsInstance(result, list)
        self.assertLessEqual(len(result), 3)
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
