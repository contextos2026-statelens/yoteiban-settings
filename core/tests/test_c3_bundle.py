import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import unittest
from datetime import datetime, timezone, timedelta
from yoteiban_core.bundle import bundle, to_text

JST = timezone(timedelta(hours=9))


def finding(key, urgency="今日中", day="2026-11-19", kind="型", text=None):
    return {"key":key, "type":kind, "date":day, "refs":[key], "urgency":urgency, "text":text or key}


class BundleTests(unittest.TestCase):
    def test_immediate_survives_quiet_and_focus_blocks(self):
        now = datetime(2026, 11, 18, 23, 0, tzinfo=JST)
        items = [finding("urgent", "すぐ"), finding("normal", "今日中")]
        result = bundle(items, now, {"quiet_hours":[{"start":"22:00", "end":"08:00"}],
                                     "focus_blocks":[{"start":"2026-11-18T22:00:00+09:00", "end":"2026-11-19T01:00:00+09:00"}]}, set())
        self.assertEqual([x["key"] for x in result["now"]], ["urgent"])
        self.assertEqual([x["key"] for x in result["morning"]], ["normal"])

    def test_focus_block_and_quiet_ranges_accept_time_pairs(self):
        now = datetime(2026, 11, 18, 12, 30, tzinfo=JST)
        result = bundle([finding("ordinary")], now, {"quiet_hours":[["12:00", "13:00"]]}, set())
        self.assertEqual([x["key"] for x in result["morning"]], ["ordinary"])

    def test_muted_snoozed_and_already_sent_are_dropped(self):
        now = datetime(2026, 11, 18, 10, 0, tzinfo=JST)
        items = [finding("muted", kind="雨"), finding("sleep"), finding("sent"), finding("free")]
        prefs = {"muted_types":["雨"], "snooze":{"sleep":"2026-11-18T11:00:00+09:00"}}
        result = bundle(items, now, prefs, {"sent"})
        self.assertEqual({x["key"] for x in result["dropped"]}, {"muted", "sleep", "sent"})
        self.assertEqual([x["key"] for x in result["morning"]], ["free"])

    def test_morning_limit_uses_urgency_then_nearest_date(self):
        items = [finding("far", "朝でよい", "2026-11-25"), finding("soon", "今日中", "2026-11-20"),
                 finding("urgentish", "今日中", "2026-11-19"), finding("urgent", "すぐ")]
        result = bundle(items, datetime(2026, 11, 18, 10, 0, tzinfo=JST), {"morning_max_items":2}, set())
        self.assertEqual([x["key"] for x in result["morning"]], ["urgentish", "soon"])
        self.assertEqual([x["key"] for x in result["now"]], ["urgent"])
        self.assertEqual([x["key"] for x in result["dropped"]], ["far"])

    def test_snooze_overrides_immediate_priority(self):
        item = finding("urgent", "すぐ")
        result = bundle([item], datetime(2026, 11, 18, 10, 0, tzinfo=JST),
                        {"snooze":{"urgent":"2026-11-18T12:00:00+09:00"}}, set())
        self.assertEqual(result["now"], [])
        self.assertEqual(result["dropped"], [item])

    def test_text_is_one_short_line_per_item(self):
        self.assertEqual(to_text([finding("a", text="お知らせA\n続き"), finding("b", text="お知らせB")]),
                         "お知らせA 続き\nお知らせB")

    def test_rejects_naive_now(self):
        with self.assertRaises(ValueError):
            bundle([], datetime(2026, 11, 18, 10), {}, set())


if __name__ == "__main__":
    unittest.main()
