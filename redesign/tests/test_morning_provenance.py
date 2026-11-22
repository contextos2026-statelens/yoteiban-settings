import os, sys, unittest
from datetime import date
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(R, "redesign", "lib"))
from yoteiban_rd.calc import Event, parse_time, relate_events, find_conflicts, representative_events, day_load_minutes
from yoteiban_rd.message_check import validate_morning_send
from yoteiban_rd.morning import Source, build_morning


def event(key, title, start, end, location=None):
    return Event(key, title, parse_time("2026-11-22 " + start + " JST"),
                 parse_time("2026-11-22 " + end + " JST"), location=location)


def raw(key, title, start, end, description=""):
    return {"id": key, "summary": title, "description": description,
            "start": {"dateTime": "2026-11-22T" + start + ":00+09:00"},
            "end": {"dateTime": "2026-11-22T" + end + ":00+09:00"}}


class MorningProvenanceTests(unittest.TestCase):
    def test_relationship_same_contained_conflict_and_separate(self):
        self.assertEqual(relate_events(event("a", "授業", "13:00", "14:00"), event("b", "授業", "13:05", "14:05")), "同一")
        self.assertEqual(relate_events(event("a", "スクーリング", "11:30", "17:40"), event("b", "スクーリング授業", "13:00", "14:00")), "包含")
        house = event("a", "出発", "10:06", "11:06"); house.calendar = "household"
        personal = event("b", "出発", "09:00", "10:00")
        self.assertEqual(relate_events(house, personal), "食い違い")
        self.assertEqual(relate_events(event("a", "英語", "10:00", "11:00"), event("b", "歯医者", "10:00", "11:00")), "別件")
        self.assertEqual(relate_events(event("a", "自習", "10:00", "11:00"), event("b", "自習", "15:00", "16:00")), "別件")
        self.assertEqual(relate_events(event("a", "面談", "10:00", "11:00"), event("b", "三者面談", "10:00", "11:00")), "別件")
        self.assertEqual(relate_events(event("a", "授業", "10:00", "11:00"), event("b", "授業", "10:05", "11:05")), "同一")

    def test_conflict_scan_does_not_stop_before_later_calendar_mismatch(self):
        first = event("p", "講義", "09:00", "10:00")
        middle = event("p2", "別件", "10:30", "11:00")
        home = event("h", "講義", "12:00", "13:00"); home.calendar = "household"
        self.assertIn((first, home), find_conflicts([first, middle, home]))

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
        self.assertIn("📅勝手な一文", validate_morning_send("📅予定\n📅勝手な一文", items)["unmatched_lines"])
        self.assertTrue(validate_morning_send("📅予定\n↳補足", [], [{"text": "↳補足"}])["ok"])
        self.assertTrue(validate_morning_send("📅予定\n・天気：晴れ", [{"type": "確認すること", "text": "・天気：晴れ"}])["ok"])

    def test_morning_1122_fixture_copies_frame_and_one_departure_mismatch(self):
        sources = [Source("p", "個人用カレンダー", "personal"), Source("h", "家用カレンダー", "household")]
        reads = {"p": {"ok": True, "events": [raw("pc", "スクーリング授業", "09:05", "10:05"),
                    raw("inner", "スクーリング授業", "13:00", "14:00"),
                    raw("p9", "出発", "09:00", "09:10"), raw("p11", "出発", "11:00", "11:10")]},
                 "h": {"ok": True, "events": [raw("hc", "スクーリング授業", "09:00", "10:00"),
                    raw("frame", "スクーリング", "11:30", "17:40", "架空本人"),
                    raw("hd", "出発", "10:06", "10:16", "架空本人"),
                    raw("only", "家用だけの用事", "18:00", "18:30", "架空本人")]}}
        result = build_morning(date(2026, 11, 22), sources, reads,
                               owner_cfg={"self_names": ["架空本人"]}, weather={"value": "晴れ", "source": "web"})
        body = result["body"]
        self.assertEqual(body.count("スクーリング授業"), 2)  # copy shown once; contained inner class once
        self.assertEqual(sum("スクーリング授業" in line and "09:" in line for line in body.splitlines()), 1)
        self.assertNotIn("11:30〜17:40 スクーリング", body)
        self.assertTrue(any("スクーリング" in str(item.get("values")) for item in result["ledger_items"]))
        self.assertEqual(body.count("食い違い"), 1)
        self.assertIn("09:00〜09:10、11:00〜11:10", body)
        self.assertEqual(sum("個人用カレンダー" in line for line in body.splitlines()), 1)
        self.assertEqual(sum("家用カレンダー" in line for line in body.splitlines()), 2)
        self.assertIn("家用だけの用事（家用カレンダー）", body)

    def test_containment_does_not_assign_unknown_household_owner(self):
        sources = [Source("p", "個人用カレンダー", "personal"), Source("h", "家用カレンダー", "household")]
        reads = {"p": {"ok": True, "events": [raw("p", "スクーリング授業", "13:00", "14:00")]},
                 "h": {"ok": True, "events": [raw("h", "スクーリング", "11:30", "17:40")]}}
        result = build_morning(date(2026, 11, 22), sources, reads)
        self.assertTrue(result["unknown"])
        self.assertIn("誰の予定か未確認", result["body"])
        self.assertTrue(any("誰の予定ですか" in card["question"] for card in result["cards"]))

    def test_notices_require_structured_evidence_and_legacy_strings_are_ledger_only(self):
        notices = ["架空の旧通知", {"text": "根拠なし通知"},
                   {"text": "根拠付き通知", "values": {"id": "fixture"}}]
        result = build_morning(date(2026, 11, 22), [Source("p", "personal", "personal")],
                               {"p": {"ok": True, "events": []}}, notices=notices)
        self.assertIn("根拠付き通知", result["body"])
        self.assertNotIn("架空の旧通知", result["body"])
        self.assertNotIn("根拠なし通知", result["body"])
        self.assertTrue(any(item.get("values") == "架空の旧通知" for item in result["ledger_items"]))

    def test_nightly_load_filters_owners_before_grouping(self):
        self_event = event("self", "授業", "13:00", "14:00")
        other = event("other", "授業", "13:00", "15:00"); other.calendar = "household"; other.owner = "unknown"
        all_grouped = representative_events([self_event, other])
        filtered_grouped = representative_events([e for e in [self_event, other] if e.owner in ("self", "all")])
        self.assertEqual(len(all_grouped), 1)
        self.assertEqual(day_load_minutes(filtered_grouped, self_event.start.date()), 60)


if __name__ == "__main__":
    unittest.main()
