import unittest
from datetime import timedelta
import _paths  # noqa
from yoteiban_rd.calc import (CalcError, Event, parse_time, find_conflicts, find_duplicates, free_slots,
                              latest_action_time, travel_check, event_from_google)
from yoteiban_rd.urgency import Context, Item, Notifier, decide

T = parse_time


def ev(i, s, e, **k):
    return Event(id=i, title=k.pop("title", i), start=T(s), end=T(e), **k)


class CalcTest(unittest.TestCase):
    def test_parse_requires_zone(self):
        with self.assertRaises(CalcError):
            parse_time("2026-11-20 10:00")
        with self.assertRaises(CalcError):
            parse_time("")
        self.assertEqual(T("2026-11-20T01:00:00Z"), T("2026-11-20 10:00 JST"))

    def test_end_before_start_rejected(self):
        with self.assertRaises(CalcError):
            ev("x", "2026-11-20 11:00 JST", "2026-11-20 10:00 JST")

    def test_conflict_and_duplicate(self):
        a = ev("a", "2026-11-28 10:00 JST", "2026-11-28 13:00 JST", calendar="household")
        b = ev("b", "2026-11-28 10:30 JST", "2026-11-28 11:20 JST")
        c = ev("c", "2026-11-28 10:00 JST", "2026-11-28 13:00 JST", title="a")
        self.assertEqual(len(find_conflicts([a, b])), 1)
        self.assertEqual(len(find_duplicates([a, c])), 1)
        self.assertEqual(find_conflicts([ev("o", "2026-11-28 10:00 JST", "2026-11-28 11:00 JST", owner="other"), b]), [])

    def test_free_slots(self):
        evs = [ev("a", "2026-11-20 10:00 JST", "2026-11-20 11:00 JST")]
        s = free_slots(evs, T("2026-11-20 09:00 JST"), T("2026-11-20 12:00 JST"), 30)
        self.assertEqual(len(s), 2)
        with self.assertRaises(CalcError):
            free_slots(evs, T("2026-11-20 12:00 JST"), T("2026-11-20 09:00 JST"))

    def test_latest_action_estimated(self):
        t, est = latest_action_time(T("2026-11-20 10:00 JST"), 60, None)
        self.assertTrue(est)
        self.assertEqual(t, T("2026-11-20 08:45 JST"))
        with self.assertRaises(CalcError):
            latest_action_time(T("2026-11-20 10:00 JST"), -5, 0)

    def test_travel_return_only(self):
        m = ev("m", "2026-11-28 17:40 JST", "2026-11-28 18:50 JST", kind="move", meta={"direction": "return"})
        f = travel_check([m])
        self.assertEqual(f[0]["type"], "帰りの移動だけがあり、元の予定が無い")
        cls = ev("k", "2026-11-28 11:30 JST", "2026-11-28 12:20 JST", location="学校")
        self.assertEqual(travel_check([cls])[0]["type"], "移動の予定が無い")

    def test_google_event(self):
        self.assertIsNone(event_from_google({"status": "cancelled"}, "personal"))
        e = event_from_google({"id": "1", "summary": "x", "start": {"date": "2026-12-05"}, "end": {"date": "2026-12-06"}}, "household")
        self.assertTrue(e.all_day)
        with self.assertRaises(CalcError):
            event_from_google({"id": "2", "start": {}, "end": {}}, "personal")


class UrgencyTest(unittest.TestCase):
    def ctx(self, now, **k):
        n = T(now)
        nxt = (n + timedelta(days=1)).replace(hour=7, minute=0) if n.hour >= 7 else n.replace(hour=7, minute=0)
        return Context(now=n, next_summary=nxt, **k)

    def test_cases(self):
        c = self.ctx("2026-11-20 10:00 JST")
        self.assertEqual(decide(Item("事故", family_emergency=True), self.ctx("2026-11-20 02:00 JST")).deliver, "now")
        d = decide(Item("診察の繰り上げ", time=T("2026-11-20 11:30 JST"), travel_min=40, prep_min=10, impact={"体"}), c)
        self.assertEqual((d.tier, d.deliver), ("今すぐ", "now"))
        self.assertIsNotNone(d.remind_at)
        d = decide(Item("17時締切", time=T("2026-11-20 17:00 JST"), travel_min=0, prep_min=30, impact={"学業の締切"}), c)
        self.assertEqual(d.tier, "今日中")
        d = decide(Item("明日正午の返事", time=T("2026-11-21 12:00 JST"), travel_min=0, prep_min=10), c)
        self.assertEqual(d.tier, "朝一番")
        d = decide(Item("来週の誘い", time=T("2026-11-27 19:00 JST"), travel_min=30, prep_min=10), c)
        self.assertEqual(d.tier, "ふつう")
        self.assertEqual(decide(Item("30分後・変化なし", needs_action=False, time=T("2026-11-20 10:30 JST")), c).tier, "急ぎにしない")
        self.assertEqual(decide(Item("家族の別の人", for_owner=False), c).deliver, "log")
        self.assertEqual(decide(Item("時刻なし"), c).tier, "ふつう")
        self.assertEqual(decide(Item("過ぎた", time=T("2026-11-20 09:00 JST")), c).tier, "過ぎていた")

    def test_unregistered_no_time_not_urgent(self):
        c = self.ctx("2026-11-20 10:00 JST")
        d = decide(Item("至急", time=T("2026-11-20 10:30 JST"), registered_source=False, time_in_text=False, travel_min=0, prep_min=0), c)
        self.assertEqual(d.tier, "朝一番")

    def test_night(self):
        # 22時に明日9時の面談の場所変更（朝のまとめ7時・移動60分）→ 行動の締切 7:45 < 9:00 → 夜でも
        c = self.ctx("2026-11-20 22:00 JST")
        it = Item("場所変更", time=T("2026-11-21 09:00 JST"), travel_min=60, prep_min=15)
        self.assertEqual(decide(it, c).deliver, "now")
        self.assertEqual(decide(it, self.ctx("2026-11-20 22:00 JST", night_setting="safety_only")).deliver, "morning_top")
        # 夜の念押しはしない
        self.assertIsNone(decide(Item("x", time=T("2026-11-20 23:30 JST"), travel_min=0, prep_min=0), c).remind_at)

    def test_bad_context(self):
        with self.assertRaises(CalcError):
            decide(Item("x"), Context(now=T("2026-11-20 10:00 JST"), next_summary=T("2026-11-20 07:00 JST")))

    def test_notifier(self):
        n, t = Notifier(), T("2026-11-20 10:00 JST")
        self.assertTrue(n.offer("k", "出発を30分早める", t)["send"])
        self.assertFalse(n.offer("k", "出発を30分早める", t + timedelta(minutes=1))["send"])
        self.assertTrue(n.offer("k", "出発を30分早める→やめる", t + timedelta(minutes=5)).get("merge"))
        self.assertTrue(n.offer("k", "別のこと", t + timedelta(minutes=30))["send"])
        asks = [n.offer(f"k{i}", "a", t)["ask_too_many"] for i in range(10)]
        self.assertEqual(asks.count(True), 1)


if __name__ == "__main__":
    unittest.main()
