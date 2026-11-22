import os, sys, unittest
from datetime import date
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(R, "redesign", "lib"))
from yoteiban_rd.calc import Event, parse_time, relate_events
from yoteiban_rd.message_check import validate_morning_send
from yoteiban_rd.morning import Source, build_morning


def event(key, title, start, end, location=None):
    return Event(key, title, parse_time("2026-11-22 " + start + " JST"),
                 parse_time("2026-11-22 " + end + " JST"), location=location)


class MorningProvenanceTests(unittest.TestCase):
    def test_relationship_same_contained_conflict_and_separate(self):
        self.assertEqual(relate_events(event("a", "授業", "13:00", "14:00"), event("b", "授業", "13:05", "14:05")), "同一")
        self.assertEqual(relate_events(event("a", "スクーリング", "11:30", "17:40"), event("b", "スクーリング授業", "13:00", "14:00")), "包含")
        self.assertEqual(relate_events(event("a", "スクーリング", "10:06", "11:06"), event("b", "スクーリング", "09:00", "10:00")), "食い違い")
        self.assertEqual(relate_events(event("a", "英語", "10:00", "11:00"), event("b", "歯医者", "10:00", "11:00")), "別件")

    def test_morning_reports_departure_mismatch_once_and_no_prose_notices(self):
        sources = [Source("p", "個人用カレンダー", "personal"), Source("h", "家用カレンダー", "household")]
        reads = {"p": {"ok": True, "events": [
                    {"id": "p1", "summary": "出発", "start": {"dateTime": "2026-11-22T09:00:00+09:00"}, "end": {"dateTime": "2026-11-22T09:10:00+09:00"}},
                    {"id": "p2", "summary": "出発", "start": {"dateTime": "2026-11-22T11:00:00+09:00"}, "end": {"dateTime": "2026-11-22T11:10:00+09:00"}}]},
                 "h": {"ok": True, "events": [{"id": "h1", "summary": "出発", "start": {"dateTime": "2026-11-22T10:06:00+09:00"}, "end": {"dateTime": "2026-11-22T10:16:00+09:00"}}]}}
        result = build_morning(date(2026, 11, 22), sources, reads, weather={"value": "晴れ", "source": "web"},
                               notices=["家用にも同じスクーリングがある"])
        self.assertEqual(result["body"].count("食い違い"), 1)
        self.assertIn("個人用カレンダー 09:00〜09:10、11:00〜11:10", result["body"])
        self.assertIn("晴れ（Web）", result["body"])
        self.assertNotIn("家用にも同じスクーリング", result["body"])
        self.assertIn("食い違い", [item["type"] for item in result["items"]])

    def test_weather_missing_is_only_unconfirmed(self):
        result = build_morning(date(2026, 11, 22), [Source("p", "personal", "personal")],
                               {"p": {"ok": True, "events": []}}, weather={"value": "", "source": "unavailable"})
        self.assertEqual(result["body"], "今日の知らせはありません（天気は未確認）")

    def test_send_check_finds_added_line_and_missing_important_item(self):
        items = [{"type": "予定", "text": "・10:00 受診", "values": {"start": "10:00"}},
                 {"type": "確認すること", "text": "・本人に確認", "values": {"question": "場所"}}]
        result = validate_morning_send("📅予定\n・10:00 受診\n・AIの書き足し", items)
        self.assertEqual(result["unmatched_lines"], ["・AIの書き足し"])
        self.assertEqual(result["missing_items"][0]["text"], "・本人に確認")


if __name__ == "__main__":
    unittest.main()
