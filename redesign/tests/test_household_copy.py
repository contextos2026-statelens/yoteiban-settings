import os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(R, "redesign/lib"))

import unittest

from yoteiban_rd.calc import Event
from yoteiban_rd.household import (
    already_on_personal, classify, copy_candidates, name_at_boundary, skip_copy,
)

JST = ZoneInfo("Asia/Tokyo")


def ev(eid, title, start, end="2026-11-25T15:00:00+09:00", calendar="household", all_day=False, memo=""):
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    return Event(id=eid, title=title, start=s, end=e, calendar=calendar, all_day=all_day,
                 meta={"memo": memo} if memo else {})


CFG = {
    "self_names": ["たろう"],
    "self_name_rules": [{"form": "た", "class": "self"}],
    "roster": ["はなこ"],
    "other_name_rules": [{"form": "は", "class": "other"}],
    "table": [
        {"word": "わくわく教室", "class": "self"},
        {"word": "2人分の薬", "class": "other"},
    ],
    "skip_copy": [{"start": "2026-12-03T17:00:00+09:00", "title_contains": "提出期限"}],
}


class NameBoundaryTests(unittest.TestCase):
    def test_short_form_at_start_or_delim_not_ordinary_word(self):
        self.assertTrue(name_at_boundary("た 歯医者", "た"))
        self.assertTrue(name_at_boundary("た)塾", "た"))
        self.assertFalse(name_at_boundary("たとえば会", "た"))
        self.assertFalse(name_at_boundary("まとめ会", "ま"))
        self.assertTrue(name_at_boundary("は✴️運動", "は"))
        self.assertFalse(name_at_boundary("はなこ講師", "は"))  # あとにひらがな

    def test_classify_uses_rules_and_table(self):
        self.assertEqual(classify("たろう 自習", cfg=CFG)[0], "self")
        self.assertEqual(classify("わくわく教室", cfg=CFG)[0], "self")
        self.assertEqual(classify("2人分の薬", cfg=CFG)[0], "other")
        self.assertEqual(classify("は✴️運動", cfg=CFG)[0], "other")
        self.assertEqual(classify("はなこ 会議", cfg=CFG)[0], "other")
        self.assertEqual(classify("共同購入", cfg=CFG)[0], "unknown")
        # 場所の語だけを表にすると、本人の名前つきまで本人以外になるため、長い語だけにする
        self.assertEqual(classify("病院 たろう", cfg=CFG)[0], "self")


class CopyCandidateTests(unittest.TestCase):
    def test_copies_self_and_skips_other_unknown_allday_and_all_word(self):
        now = datetime(2026, 11, 19, 17, 25, tzinfo=JST)
        events = [
            ev("1", "たろう 自習", "2026-11-21T10:00:00+09:00", "2026-11-21T13:00:00+09:00"),
            ev("2", "はなこ 会議", "2026-11-21T10:00:00+09:00", "2026-11-21T11:00:00+09:00"),
            ev("3", "共同購入", "2026-11-21T16:00:00+09:00", "2026-11-21T17:00:00+09:00"),
            ev("4", "たろう 終日", "2026-11-21T00:00:00+09:00", "2026-11-22T00:00:00+09:00", all_day=True),
            ev("5", "家族全員：外食", "2026-11-21T18:00:00+09:00", "2026-11-21T20:00:00+09:00", memo="参加：たろう、はなこ"),
            ev("6", "わくわく教室", "2026-11-24T15:30:00+09:00", "2026-11-24T16:00:00+09:00"),
            ev("7", "過去の自習 たろう", "2026-11-18T10:00:00+09:00", "2026-11-18T13:00:00+09:00"),
        ]
        got = [e.id for e in copy_candidates(events, [], CFG, now=now)]
        self.assertEqual(got, ["1", "6"])

    def test_near_start_is_treated_as_already_present_even_if_titles_differ(self):
        household = ev("h", "たろう スクーリング", "2026-11-22T11:30:00+09:00", "2026-11-22T17:40:00+09:00")
        personal = ev("p", "授業A", "2026-11-22T11:30:00+09:00", "2026-11-22T12:20:00+09:00", calendar="personal")
        self.assertTrue(already_on_personal(household, [personal], 10))
        got = copy_candidates([household], [personal], CFG)
        self.assertEqual(got, [])
        travel_h = ev("th", "移動 自宅→学園(たろう)", "2026-11-22T10:06:00+09:00", "2026-11-22T11:15:00+09:00")
        travel_p = ev("tp", "移動（家→学園）", "2026-11-22T10:00:00+09:00", "2026-11-22T11:10:00+09:00", calendar="personal")
        self.assertTrue(already_on_personal(travel_h, [travel_p], 10))
        self.assertEqual(copy_candidates([travel_h], [travel_p], CFG), [])

    def test_skip_copy_matches_start_and_title(self):
        ev1 = ev("d", "たろう、第7回 提出期限", "2026-12-03T17:00:00+09:00", "2026-12-03T17:00:00+09:00")
        ev2 = ev("d2", "たろう、第7回 提出期限", "2026-12-10T17:00:00+09:00", "2026-12-10T17:00:00+09:00")
        ev3 = ev("d3", "たろう 面談", "2026-12-03T17:00:00+09:00", "2026-12-03T17:30:00+09:00")
        self.assertTrue(skip_copy(ev1, CFG))
        self.assertFalse(skip_copy(ev2, CFG))
        self.assertFalse(skip_copy(ev3, CFG))
        got = [e.id for e in copy_candidates([ev1, ev2, ev3], [], CFG)]
        self.assertEqual(got, ["d2", "d3"])

    def test_overlap_with_different_start_is_still_a_candidate(self):
        household = ev("h", "たろう 自習", "2026-11-28T10:00:00+09:00", "2026-11-28T13:00:00+09:00")
        personal = ev("p", "授業A", "2026-11-28T10:30:00+09:00", "2026-11-28T11:20:00+09:00", calendar="personal")
        self.assertFalse(already_on_personal(household, [personal], 10))
        got = copy_candidates([household], [personal], CFG)
        self.assertEqual([e.id for e in got], ["h"])


if __name__ == "__main__":
    unittest.main()
