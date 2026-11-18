import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import unittest
from datetime import date
from yoteiban_core.lookahead import run, rerun


class LookaheadTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 18)
        self.settings = {"overload_minutes": 120, "rain_prob": 60, "prep_min": 20, "travel_margin_min": 15}

    def test_personal_overlap_family_overlap_candidate_overlap(self):
        events = [
            {"id":"a", "title":"本人A", "start":"2026-11-18T10:00:00+09:00", "end":"2026-11-18T11:00:00+09:00", "calendar_role":"personal"},
            {"id":"b", "title":"本人B", "start":"2026-11-18T10:30:00+09:00", "end":"2026-11-18T11:30:00+09:00", "calendar_role":"personal"},
            {"id":"fam", "title":"家族予定", "start":"2026-11-18T10:45:00+09:00", "end":"2026-11-18T11:15:00+09:00", "calendar_role":"family"},
            {"id":"draft", "title":"候補", "start":"2026-11-18T10:10:00+09:00", "end":"2026-11-18T10:20:00+09:00", "calendar_role":"draft"},
        ]
        types = {f["type"] for f in run(events, [], self.today, self.settings)}
        self.assertIn("重なり", types)
        self.assertIn("家族と重なる", types)
        self.assertIn("候補の予定が確定と重なる", types)

    def test_travel_prep_rain_overload_and_meals(self):
        events = [
            {"id":"a", "title":"外出A", "start":"2026-11-18T09:00:00+09:00", "end":"2026-11-18T10:00:00+09:00", "calendar_role":"personal", "place_kind":"場所A"},
            {"id":"b", "title":"外出B", "start":"2026-11-18T10:05:00+09:00", "end":"2026-11-18T15:00:00+09:00", "calendar_role":"personal", "place_kind":"屋外"},
            {"id":"c", "title":"夜の予定", "start":"2026-11-18T17:00:00+09:00", "end":"2026-11-18T21:30:00+09:00", "calendar_role":"personal"},
        ]
        found = run(events, [], self.today, self.settings, [{"date":"2026-11-18", "rain_prob":80}], lambda a,b: 30)
        types = {f["type"] for f in found}
        self.assertTrue({"移動が間に合わない", "準備の時間が無い", "雨と外出", "詰め込みすぎ", "食事の時間が無い"} <= types)
        self.assertEqual(len({f["key"] for f in found}), len(found))

    def test_deadline_and_reply(self):
        cases = [
            {"case_id":"c1", "title":"提出", "due_at":"2026-11-18T18:00:00+09:00", "required_minutes":1200},
            {"case_id":"c2", "title":"返事", "reply_by":"2026-11-20T10:00:00+09:00", "status":"未確定"},
            {"case_id":"c3", "title":"期限超過の返事", "reply_by":"2026-11-17T10:00:00+09:00", "status":"返事なし"},
            {"case_id":"c4", "title":"期限不明の返事", "status":"返事なし"},
        ]
        found = run([], cases, self.today, self.settings)
        self.assertEqual({f["type"] for f in found}, {"締切の逆算", "返事の期限"})
        replies = [f for f in found if f["type"] == "返事の期限"]
        self.assertEqual(len(replies), 3)
        self.assertTrue(any("期限超過" in f["text"] for f in replies))

    def test_unknown_travel_is_not_judged_and_family_only_is_not_personal_conflict(self):
        events = [
            {"id":"f1", "title":"家族1", "start":"2026-11-18T10:00:00+09:00", "end":"2026-11-18T12:00:00+09:00", "calendar_role":"family"},
            {"id":"f2", "title":"家族2", "start":"2026-11-18T10:30:00+09:00", "end":"2026-11-18T11:30:00+09:00", "calendar_role":"household"},
        ]
        self.assertEqual(run(events, [], self.today, self.settings, travel_min=lambda *_: 20), [])

    def test_outside_horizon_is_ignored(self):
        event = {"id":"far", "title":"遠い予定", "start":"2026-12-04T10:00:00+09:00", "end":"2026-12-04T12:00:00+09:00", "calendar_role":"personal"}
        self.assertEqual(run([event], [], self.today, self.settings, days=14), [])

    def test_rerun_replaces_only_changed_day_and_neighbors(self):
        original = [{"type":"古い", "date":"2026-11-19", "refs":[], "urgency":"朝でよい", "text":"古い", "key":"old"},
                    {"type":"保持", "date":"2026-11-25", "refs":[], "urgency":"朝でよい", "text":"保持", "key":"keep"}]
        event = {"id":"a", "title":"予定", "start":"2026-11-20T10:00:00+09:00", "end":"2026-11-20T12:00:00+09:00", "calendar_role":"personal"}
        result = rerun(["2026-11-20"], [event], [], self.today, self.settings, existing=original)
        self.assertIn("keep", [f["key"] for f in result])
        self.assertNotIn("old", [f["key"] for f in result])
        self.assertTrue(all(f["date"] in {"2026-11-19", "2026-11-20", "2026-11-21", "2026-11-25"} for f in result))


if __name__ == "__main__":
    unittest.main()
