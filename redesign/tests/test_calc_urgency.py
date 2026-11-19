import unittest
from datetime import timedelta
import _paths  # noqa
from yoteiban_rd.calc import (CalcError, Event, parse_time, find_conflicts, find_duplicates, free_slots,
                              latest_action_time, travel_check, event_from_google)
from yoteiban_rd.urgency import Context, Item, Notifier, decide
from yoteiban_rd.calc import fmt

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

    def test_night_always_matches_day(self):
        night = self.ctx("2026-11-18 23:00 JST", night_setting="always", next_delivery=T("2026-11-19 08:00 JST"))
        day = self.ctx("2026-11-18 10:00 JST", next_delivery=T("2026-11-18 18:00 JST"))
        mid = Item("習い事の振替", time=T("2026-12-06 14:00 JST"), importance="中")
        self.assertEqual((decide(mid, night).tier, decide(mid, night).deliver), ("今日中", "next_delivery"))
        self.assertEqual(decide(mid, day).deliver, "next_delivery")
        self.assertEqual(decide(mid, self.ctx("2026-11-18 23:00 JST")).deliver, "morning_top")
        high = Item("高・待てる", time=T("2026-11-19 18:00 JST"), importance="高")
        self.assertEqual(decide(high, night).deliver, "now")
        self.assertEqual(decide(high, self.ctx("2026-11-18 23:00 JST")).deliver, "morning_top")
        self.assertEqual(decide(high, self.ctx("2026-11-18 23:00 JST", night_setting="safety_only")).deliver, "morning_top")
        self.assertEqual(decide(Item("家族", family_emergency=True), night).deliver, "now")
        no_night = Item("高・待てる", time=T("2026-11-19 18:00 JST"), importance="高", night_now=False)
        self.assertEqual(decide(no_night, night).deliver, "morning_top")
        type4 = self.ctx("2026-11-18 23:00 JST", night_setting="always", notify_type=4)
        self.assertEqual(decide(high, type4).deliver, "morning_top")
        with self.assertRaises(CalcError):
            decide(Item("x"), self.ctx("2026-11-18 23:00 JST", night_setting="whenever"))



if __name__ == "__main__":
    unittest.main()



class SpeedTestB2Cases(unittest.TestCase):
    """速さの試験B2の3件（架空）。朝のまとめ 7:00、夜は急ぎだけ、型3。"""

    def ctx(self, now, **k):
        return Context(now=T(now), next_summary=T("2026-11-19 07:00 JST"), **k)

    def test_case1_record_only(self):
        d = decide(Item("お礼とあいさつ", related=False), self.ctx("2026-11-18 21:30 JST"))
        self.assertEqual((d.tier, d.deliver), ("急ぎにしない", "log"))

    def test_case2_importance_mid_type3_day(self):
        c = self.ctx("2026-11-18 10:00 JST", next_delivery=T("2026-11-18 18:00 JST"))
        it = Item("習い事の振替", time=T("2026-12-06 14:00 JST"))
        self.assertEqual(decide(it, c).tier, "ふつう")  # 時刻だけなら余裕十分
        it.importance_score = 3  # 動かせない時刻 +3 → 中
        d = decide(it, c)
        self.assertEqual((d.tier, d.deliver, d.level), ("今日中", "next_delivery", "中"))
        # 夜なら朝のまとめの先頭へ（段階も合わせて下げる）
        d = decide(Item("習い事の振替", time=T("2026-12-06 14:00 JST"), importance="中"), self.ctx("2026-11-18 23:00 JST"))
        self.assertEqual((d.tier, d.deliver), ("朝一番", "morning_top"))
        # 型4では中は即時にしない
        d = decide(Item("習い事の振替", time=T("2026-12-06 14:00 JST"), importance="中"), self.ctx("2026-11-18 10:00 JST", notify_type=4))
        self.assertEqual(d.tier, "ふつう")

    def test_case3_meeting_moved_earlier(self):
        d = decide(Item("通院が早まった・駅で待ち合わせ", time=T("2026-11-19 08:20 JST"), travel_min=30, impact={"体"}),
                   self.ctx("2026-11-18 21:30 JST"))
        self.assertEqual((d.tier, d.deliver), ("今すぐ", "now"))
        self.assertEqual(fmt(d.action_deadline), "2026-11-19 07:50 JST")  # 準備は足さない
        self.assertEqual(d.slack_min, 50)
        self.assertTrue(d.estimated)
        self.assertEqual(d.assumed, {"prep_min": 0})
        self.assertTrue(any("準備" in r and "推定" in r for r in d.reasons))
        # 同じ件を静かな時間（23:00）に見つけても、夜も即時1回
        d = decide(Item("同上", time=T("2026-11-19 08:20 JST"), travel_min=30), self.ctx("2026-11-18 23:00 JST"))
        self.assertEqual((d.tier, d.deliver), ("今すぐ", "now"))
        # 夜は鳴らさない設定なら、段階を下げて朝の先頭
        d = decide(Item("同上", time=T("2026-11-19 08:20 JST"), travel_min=30), self.ctx("2026-11-18 23:00 JST", night_setting="safety_only"))
        self.assertEqual((d.tier, d.deliver), ("朝一番", "morning_top"))


class UrgencyRulesTest(unittest.TestCase):
    def test_importance_precedence_takes_higher(self):
        c = Context(now=T("2026-11-18 10:00 JST"), next_summary=T("2026-11-19 07:00 JST"))
        far = T("2026-11-25 10:00 JST")
        self.assertEqual(decide(Item("x", time=far, travel_min=0, prep_min=0, importance="緊急"), c).tier, "今すぐ")
        self.assertEqual(decide(Item("x", time=far, travel_min=0, prep_min=0, cannot_wait=True), c).tier, "今すぐ")
        self.assertEqual(decide(Item("x", time=far, travel_min=0, prep_min=0, importance="高"), c).deliver, "now")
        self.assertEqual(decide(Item("x", time=far, travel_min=0, prep_min=0, importance="低"), c).tier, "ふつう")
        self.assertEqual(decide(Item("x", time=far, travel_min=0, prep_min=0, importance_score=9), c).level, "緊急")
        # 時刻のほうが高ければ時刻（低い重要度で下げない）
        near = T("2026-11-18 10:40 JST")
        self.assertEqual(decide(Item("x", time=near, travel_min=0, prep_min=0, importance="低"), c).tier, "今すぐ")
        # 登録外で時刻が文面に無い「待てない」は採らない
        d = decide(Item("至急", time=far, registered_source=False, time_in_text=False, cannot_wait=True), c)
        self.assertNotEqual(d.tier, "今すぐ")

    def test_bad_importance(self):
        c = Context(now=T("2026-11-18 10:00 JST"), next_summary=T("2026-11-19 07:00 JST"))
        with self.assertRaises(CalcError):
            decide(Item("x", importance="すごく"), c)
        with self.assertRaises(CalcError):
            decide(Item("x"), Context(now=c.now, next_summary=c.next_summary, notify_type=9))

    def test_tier_and_deliver_always_consistent(self):
        from yoteiban_rd.urgency import TIER_DELIVER
        base = T("2026-11-18 00:00 JST")
        n = 0
        for now_h in (1, 10, 15, 21, 23):
            now = base + timedelta(hours=now_h)
            nxt = (now + timedelta(days=1)).replace(hour=7) if now.hour >= 7 else now.replace(hour=7)
            for typ in (1, 2, 3, 4, 6):
                for night in ("urgent_only", "safety_only", "always"):
                    c = Context(now=now, next_summary=nxt, notify_type=typ, night_setting=night)
                    for off in (20, 50, 90, 200, 600, 1000, 1600, 5000):
                        for lv in (None, "緊急", "高", "中", "低"):
                            for reg in (True, False):
                                d = decide(Item("x", time=now + timedelta(minutes=off), travel_min=0, prep_min=0,
                                                importance=lv, registered_source=reg), c)
                                self.assertIn(d.deliver, TIER_DELIVER[d.tier], (now_h, typ, night, off, lv, d))
                                n += 1
        self.assertGreater(n, 3000)

