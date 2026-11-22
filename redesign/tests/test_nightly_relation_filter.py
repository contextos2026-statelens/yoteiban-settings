import os, sys, unittest
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(R, "redesign", "lib"))
from yoteiban_rd.calc import Event, parse_time
from yoteiban_rd.nightly import review


def event(key, title, start, end):
    return Event(key, title, parse_time("2026-11-22 " + start + " JST"),
                 parse_time("2026-11-22 " + end + " JST"))


class NightlyRelationTests(unittest.TestCase):
    def test_related_events_are_record_only_without_overlap_or_load_noise(self):
        events = [event("copy-a", "出発", "09:00", "09:10"), event("copy-b", "出発", "09:05", "09:15"),
                  event("block", "スクーリング", "11:30", "17:40"), event("class", "スクーリング授業", "13:00", "14:00"),
                  event("depart-a", "別日の出発確認", "18:00", "18:10"), event("depart-b", "別日の出発確認", "20:00", "20:10")]
        result = review([], events, parse_time("2026-11-22 08:00 JST"), [], horizon_days=7,
                        daily_load_limit_min=400)
        self.assertEqual(sum(item["kind"] == "記録だけ" for item in result["record_only"]), 2)
        self.assertFalse(any(item["kind"] == "重なり" for item in result["morning"]))
        self.assertFalse(any(item["kind"] == "準備不足" for item in result["morning"]))
        self.assertFalse(any(item["kind"] == "作業量の目安" for item in result["morning"]))
        self.assertTrue(any(item["kind"] == "食い違い" for item in result["morning"]))


if __name__ == "__main__":
    unittest.main()
