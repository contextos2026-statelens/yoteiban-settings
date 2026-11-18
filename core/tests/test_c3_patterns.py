import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import unittest
from datetime import date, timedelta
from yoteiban_core.patterns import REGISTRY, check_all, propose


class PatternTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 18)
        self.settings = {"overload_minutes":480, "rain_prob":60, "prep_min":30,
                         "same_person_weekly_threshold":2,
                         "weather":[{"date":"2026-11-18", "rain_prob":80}],
                         "anniversaries":[{"id":"anniv-a", "title":"記念日", "date":"2026-11-25"}]}
        self.events = [
            {"id":"a", "title":"月例会", "start":"2026-09-10T10:00:00+09:00", "end":"2026-09-10T11:00:00+09:00", "calendar_role":"personal", "place_kind":"屋外", "person_id":"friend-a"},
            {"id":"b", "title":"月例会", "start":"2026-10-10T10:00:00+09:00", "end":"2026-10-10T11:00:00+09:00", "calendar_role":"personal", "place_kind":"屋外", "person_id":"friend-a"},
            {"id":"c", "title":"外出", "start":"2026-11-18T10:00:00+09:00", "end":"2026-11-18T11:00:00+09:00", "calendar_role":"personal", "place_kind":"屋外", "person_id":"friend-a"},
            {"id":"d", "title":"用事D", "start":"2026-11-19T10:00:00+09:00", "end":"2026-11-19T11:00:00+09:00", "calendar_role":"personal", "person_id":"friend-a"},
            {"id":"e", "title":"用事E", "start":"2026-11-20T10:00:00+09:00", "end":"2026-11-20T11:00:00+09:00", "calendar_role":"personal", "person_id":"friend-a"},
            {"id":"meal1", "title":"昼の予定", "start":"2026-11-18T11:00:00+09:00", "end":"2026-11-18T14:00:00+09:00", "calendar_role":"personal"},
            {"id":"meal2", "title":"夕方の予定", "start":"2026-11-18T17:00:00+09:00", "end":"2026-11-18T21:00:00+09:00", "calendar_role":"personal"},
        ]
        self.cases = [
            {"case_id":"deadline", "title":"提出", "due_at":"2026-11-18T18:00:00+09:00", "required_minutes":1200},
            {"case_id":"reply", "title":"返事", "reply_by":"2026-11-20T10:00:00+09:00"},
            {"case_id":"trip", "title":"遠足", "due_at":"2026-11-20T08:00:00+09:00", "questions":[{"q_id":"bag", "kind":"持ち物", "status":"未"}]},
        ]

    def test_registry_contains_eight_patterns(self):
        self.assertEqual(len(REGISTRY), 8)

    def test_each_pattern_finds_and_nonmatching_cases_do_not(self):
        expected = {"締切の逆算", "返事の期限", "天気（雨と外出）", "持ち物", "毎月の用事", "記念日", "食事の時間が無い", "重なりすぎ"}
        got = check_all(self.cases, self.events, self.today, set(REGISTRY), self.settings)
        types = {item["type"] if item["type"] != "雨と外出" else "天気（雨と外出）" for item in got}
        self.assertEqual(types, expected)
        self.assertTrue(all({"type", "date", "refs", "urgency", "text", "key"} <= set(item) for item in got))
        no_hit = check_all([], [], self.today, set(REGISTRY), {"weather":[], "anniversaries":[]})
        self.assertEqual(no_hit, [])

    def test_disabled_pattern_does_not_run(self):
        got = check_all(self.cases, self.events, self.today, {"持ち物"}, self.settings)
        self.assertEqual({item["type"] for item in got}, {"持ち物"})

    def test_resolved_supply_is_ignored_and_monthly_occurrence_suppresses(self):
        cases = [{"case_id":"trip", "title":"遠足", "due_at":"2026-11-20T08:00:00+09:00", "questions":[{"kind":"持ち物", "status":"埋まった"}]}]
        events = self.events + [{"id":"nov", "title":"月例会", "start":"2026-11-10T10:00:00+09:00", "end":"2026-11-10T11:00:00+09:00"}]
        got = check_all(cases, events, self.today, {"持ち物", "毎月の用事"}, self.settings)
        self.assertEqual(got, [])

    def test_propose_only_returns_reviewable_suggestion(self):
        result = propose([{"type":"新しい事例", "text":"架空の例"}])
        self.assertTrue(result["requires_human_approval"])
        self.assertNotIn("新しい事例", REGISTRY)


if __name__ == "__main__":
    unittest.main()
