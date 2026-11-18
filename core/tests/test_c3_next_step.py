import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import unittest
from datetime import datetime, timezone, timedelta
from yoteiban_core.next_step import decide

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 18, 10, 0, tzinfo=JST)


class NextStepTests(unittest.TestCase):
    def case(self, **patch):
        return {"case_id":"c", "status":"未確定", "urgency":"今日中", **patch}

    def question(self, **patch):
        return {"q_id":"q", "status":"未", **patch}

    def test_twenty_scenarios(self):
        scenarios = [
            (self.case(status="見送り"), [], 0, None),
            (self.case(status="取消"), [], 0, None),
            (self.case(status="済んだ"), [], 0, None),
            (self.case(urgency="すぐ"), [self.question(fill_method="台帳")], 0, ("台帳", "2026-11-18T11:00:00+09:00")),
            (self.case(urgency="今日中"), [self.question(fill_method="台帳")], 0, ("台帳", "2026-11-18T18:00:00+09:00")),
            (self.case(urgency="朝でよい"), [self.question(fill_method="台帳")], 0, ("台帳", "2026-11-19T08:00:00+09:00")),
            (self.case(), [self.question(fill_method="台帳"), self.question(q_id="q2", fill_method="本人に聞く")], 0, ("台帳", "q")),
            (self.case(), [self.question(fill_method="調べる")], 0, ("調べる", "q")),
            (self.case(), [self.question(fill_method="相手に聞く")], 0, ("相手に聞く", "q")),
            (self.case(), [self.question(fill_method="本人に聞く")], 0, ("本人に聞く", "q")),
            (self.case(), [self.question(fill_method="本人に聞く")], 1, ("待つ", "q")),
            (self.case(), [self.question(fillable_from_ledger=True)], 0, ("台帳", "q")),
            (self.case(), [self.question(researchable=True)], 0, ("調べる", "q")),
            (self.case(), [self.question(ask_contact=True)], 0, ("相手に聞く", "q")),
            (self.case(), [self.question(status="埋まった")], 0, ("待つ", None)),
            (self.case(), [self.question(status="埋められない")], 0, ("待つ", None)),
            (self.case(due_at="2026-11-19T09:00:00+09:00"), [self.question(fill_method="調べる", due_at="2026-11-19T09:00:00+09:00")], 0, ("調べる", "2026-11-18T09:00:00+09:00")),
            (self.case(reply_by="2026-11-20T10:00:00+09:00", status="返事なし"), [], 0, ("相手に聞く", None)),
            (self.case(reply_by="2026-11-20T10:00:00+09:00", status="返事なし", reply_reask_suggested=True), [], 0, ("待つ", None)),
            (self.case(reply_by="2026-11-17T10:00:00+09:00", status="返事なし"), [], 0, ("待つ", None)),
        ]
        for index, (case, questions, open_count, expected) in enumerate(scenarios, 1):
            with self.subTest(scenario=index):
                result = decide(case, questions, NOW, {}, open_count)
                if expected is None:
                    self.assertIsNone(result)
                else:
                    method, second = expected
                    self.assertEqual(result["method"], method)
                    if second and "T" in second:
                        self.assertEqual(result["next_review_at"], second)
                    else:
                        self.assertEqual(result["q_id"], second)
                    self.assertIn("why", result)
                    self.assertIn("draft_needed", result)

    def test_counterparty_question_is_a_draft_never_a_send(self):
        result = decide(self.case(), [self.question(fill_method="相手に聞く")], NOW, {}, 0)
        self.assertTrue(result["draft_needed"])
        self.assertNotIn("send", result)

    def test_deadline_caps_review_at_twenty_four_hours_before(self):
        result = decide(self.case(due_at="2026-11-19T09:00:00+09:00"),
                        [self.question(fill_method="台帳")], NOW, {}, 0)
        self.assertEqual(result["next_review_at"], "2026-11-18T09:00:00+09:00")

    def test_rejects_naive_now_and_negative_open_count(self):
        with self.assertRaises(ValueError):
            decide(self.case(), [], datetime(2026, 11, 18, 10), {}, 0)
        with self.assertRaises(ValueError):
            decide(self.case(), [], NOW, {}, -1)


if __name__ == "__main__":
    unittest.main()
