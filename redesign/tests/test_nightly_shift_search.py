"""先読みの見る範囲（先1週間）と、ずらし先の 7→14→21 日の広げ方。"""
from __future__ import annotations

import os, sys
import unittest
from datetime import datetime, timedelta

R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(R, "redesign/lib"))

from yoteiban_rd.calc import Event, JST
from yoteiban_rd.nightly import HORIZON_DAYS, SHIFT_SEARCH_STEPS, find_shift_slots, review


def P(s: str) -> datetime:
    return datetime.strptime(s.replace(" JST", ""), "%Y-%m-%d %H:%M").replace(tzinfo=JST)


def E(id, title, start, end, **k):
    return Event(id, title, P(start), P(end), **k)


NOW = P("2026-11-19 21:45 JST")


def _block(day_offset: int, ident: str) -> Event:
    d = (NOW + timedelta(days=day_offset)).date()
    return Event(ident, "埋まり", datetime(d.year, d.month, d.day, 9, tzinfo=JST),
                 datetime(d.year, d.month, d.day, 21, tzinfo=JST))


class HorizonAndShift(unittest.TestCase):
    def test_default_horizon_is_seven(self):
        self.assertEqual(HORIZON_DAYS, 7)
        self.assertEqual(SHIFT_SEARCH_STEPS, (7, 14, 21))

    def test_overlap_inside_seven_days_is_found(self):
        a = E("a", "会議A", "2026-11-22 10:00 JST", "2026-11-22 11:00 JST")
        b = E("b", "会議B", "2026-11-22 10:30 JST", "2026-11-22 11:30 JST")
        r = review([], [a, b], NOW, goals=[])
        kinds = [f["kind"] for f in r["morning"] + r["record_only"]]
        self.assertIn("重なり", kinds)

    def test_overlap_on_day_ten_is_not_scanned(self):
        a = E("a", "会議A", "2026-11-29 10:00 JST", "2026-11-29 11:00 JST")
        b = E("b", "会議B", "2026-11-29 10:30 JST", "2026-11-29 11:30 JST")
        r = review([], [a, b], NOW, goals=[])
        kinds = [f["kind"] for f in r["morning"] + r["record_only"]]
        self.assertNotIn("重なり", kinds)

    def test_shift_found_in_first_week(self):
        target = E("t", "買い物", "2026-11-21 10:00 JST", "2026-11-21 11:00 JST")
        # 21日は重なり用の相手。ずらし先は 22日の朝が空いている
        other = E("o", "通院", "2026-11-21 10:30 JST", "2026-11-21 11:30 JST")
        result = find_shift_slots(target, [target, other], NOW)
        self.assertEqual(result["window_days"], 7)
        self.assertTrue(result["slots"])
        self.assertIsNone(result["reason"])
        first = result["slots"][0][0]
        self.assertGreaterEqual(first, P("2026-11-20 09:00 JST"))
        self.assertLess(first, P("2026-11-27 21:45 JST"))

    def test_shift_expands_to_two_then_three_weeks(self):
        target = E("t", "買い物", "2026-11-21 10:00 JST", "2026-11-21 11:00 JST")
        # 先1週間（今から7日＝11/26 21:45まで）を埋める
        blocked = [_block(i, f"w1-{i}") for i in range(1, 8)]
        # 11/27（8日後）はまだ14日の窓。空きを1つ残す
        result = find_shift_slots(target, [target, *blocked], NOW)
        self.assertEqual(result["window_days"], 14)
        self.assertTrue(result["slots"])
        self.assertGreaterEqual(result["slots"][0][0], P("2026-11-26 21:45 JST"))

        blocked2 = blocked + [_block(i, f"w2-{i}") for i in range(8, 15)]
        result = find_shift_slots(target, [target, *blocked2], NOW)
        self.assertEqual(result["window_days"], 21)
        self.assertTrue(result["slots"])
        self.assertGreaterEqual(result["slots"][0][0], P("2026-12-03 21:45 JST"))

        blocked3 = blocked2 + [_block(i, f"w3-{i}") for i in range(15, 22)]
        result = find_shift_slots(target, [target, *blocked3], NOW)
        self.assertEqual(result["slots"], [])
        self.assertEqual(result["reason"], "有力なずらし先は見つかりませんでした")

    def test_household_is_not_moved(self):
        target = E("h", "家の用事", "2026-11-21 10:00 JST", "2026-11-21 11:00 JST",
                   calendar="household", owner="self")
        result = find_shift_slots(target, [target], NOW)
        self.assertEqual(result["slots"], [])
        self.assertEqual(result["reason"], "家用の予定は動かさない")

    def test_review_mentions_shift_or_no_alternative(self):
        a = E("a", "会議A", "2026-11-22 10:00 JST", "2026-11-22 11:00 JST")
        b = E("b", "会議B", "2026-11-22 10:30 JST", "2026-11-22 11:30 JST")
        r = review([], [a, b], NOW, goals=[])
        texts = [f["proposal"] for f in r["morning"] + r["record_only"] if f["kind"] == "重なり"]
        self.assertTrue(texts)
        self.assertTrue(any("ずらし先" in t or "有力なずらし先は見つかりませんでした" in t for t in texts))
        self.assertTrue(any("目的（何のため）と方法" in t for t in texts))

    def test_no_slot_asks_to_report(self):
        a = E("a", "会議A", "2026-11-22 10:00 JST", "2026-11-22 11:00 JST")
        b = E("b", "会議B", "2026-11-22 10:30 JST", "2026-11-22 11:30 JST")
        blocked = [_block(i, f"w-{i}") for i in range(1, 22)]
        r = review([], [a, b, *blocked], NOW, goals=[])
        texts = [f["proposal"] for f in r["morning"] + r["record_only"] if f["kind"] == "重なり"]
        self.assertTrue(texts)
        self.assertTrue(any("有力なずらし先は見つかりませんでした" in t for t in texts))
        self.assertTrue(any("有力な代替案が無ければ" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
