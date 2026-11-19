"""意地悪ケース。第1弾（評価 2026-11-18 の22件。10番は第18章(a)を表す入力 end を使う形に直した）と、
第2弾（直した後に新しく作った意地悪ケース）。期待は正本（18〜22・25章）に沿った正解。"""
import json, os, subprocess, sys, tempfile, unittest
import _paths  # noqa
from datetime import date, timedelta
from yoteiban_rd.calc import (parse_time as P, Event, find_conflicts, find_duplicates, travel_check, day_load_minutes,
                              event_from_google, free_slots, check_weekdays, weekday_ja)
import yoteiban_rd.calc as C
from yoteiban_rd.urgency import Item, Context, decide
from yoteiban_rd.nightly import Case, review, plan_steps
from yoteiban_rd.morning import Source, build_morning, coverage
from yoteiban_rd.household import classify


def U(item, **ctx):
    it = dict(item)
    for k in ("time", "end", "recover_by"):
        if it.get(k):
            it[k] = P(it[k])
    it["impact"] = set(it.get("impact", []))
    c = {"now": P(ctx.pop("now")), "next_summary": P(ctx.pop("next_summary")), **ctx}
    return decide(Item(**it), Context(**c))


def E(i, t, s, e, **k):
    return Event(i, t, P(s), P(e), **k)


NIGHT = dict(now="2026-11-18 23:00 JST", next_summary="2026-11-19 07:00 JST")


class Round1(unittest.TestCase):
    """評価の22件（10番だけ入力の形を直した）。"""

    def test_01_cross_midnight_conflict(self):
        a = E("a", "夜の作業", "2026-11-18 23:30 JST", "2026-11-19 00:30 JST"); b = E("b", "深夜の電話", "2026-11-19 00:00 JST", "2026-11-19 01:00 JST")
        self.assertEqual(len(find_conflicts([a, b])), 1)
        self.assertEqual(day_load_minutes([a], date(2026, 11, 18)), 30)  # 2

    def test_03_04_safety_only(self):
        self.assertEqual(U({"title": "体調の連絡", "time": "2026-11-19 10:00 JST", "body_safety": True}, night_setting="safety_only", **NIGHT).deliver, "morning_top")
        self.assertEqual(U({"title": "家族が搬送", "family_emergency": True}, night_setting="safety_only", **NIGHT).deliver, "now")

    def test_05_new_year(self):
        d = U({"title": "初詣の集合", "time": "2027-01-01 08:00 JST", "travel_min": 30}, now="2026-12-31 22:00 JST", next_summary="2027-01-01 07:00 JST")
        self.assertEqual((d.tier, d.deliver, d.slack_min), ("今すぐ", "now", 30))

    def test_06_weekday_tool(self):
        self.assertTrue(any("weekday" in n for n in dir(C)))
        self.assertEqual(weekday_ja(date(2027, 1, 1)), "金")  # 22

    def test_07_estimated(self):
        d = U({"title": "駅で待ち合わせ", "time": "2026-11-19 08:20 JST"}, now="2026-11-18 21:30 JST", next_summary="2026-11-19 07:00 JST")
        self.assertEqual((d.estimated, d.action_deadline.strftime("%H:%M")), (True, "08:20"))

    def test_08_09_21_household_owner(self):
        s = E("s", "塾", "2026-11-20 15:00 JST", "2026-11-20 16:00 JST")
        u = E("u", "面談", "2026-11-20 15:30 JST", "2026-11-20 16:30 JST", calendar="household", owner="unknown")
        o = E("o", "父の会議", "2026-11-20 15:30 JST", "2026-11-20 16:30 JST", calendar="household", owner="other")
        self.assertEqual(len(find_conflicts([s, u])), 1)
        self.assertEqual(len(find_conflicts([s, o])), 0)
        fs = free_slots([u], P("2026-11-20 15:00 JST"), P("2026-11-20 17:00 JST"))
        self.assertEqual([(x.strftime("%H:%M"), y.strftime("%H:%M")) for x, y in fs], [("15:00", "17:00")])

    def test_10_past_a_night(self):
        # 入力の形を直した：「途中参加可」を end（終わりの時刻）で表す
        d = U({"title": "オンライン説明会（途中参加可）", "time": "2026-11-18 22:00 JST", "end": "2026-11-18 23:30 JST", "importance": "高"},
              now="2026-11-18 22:20 JST", next_summary="2026-11-19 07:00 JST")
        self.assertEqual(d.deliver, "now")
        self.assertEqual(d.tier, "過ぎた予定の急ぎ")

    def test_11_12_past_and_cancelled(self):
        d = U({"title": "昼の約束", "time": "2026-11-18 12:00 JST"}, now="2026-11-18 13:00 JST", next_summary="2026-11-19 07:00 JST")
        self.assertEqual((d.tier, d.deliver), ("過ぎていた", "morning_top"))
        self.assertIsNone(event_from_google({"status": "cancelled", "id": "x", "start": {"dateTime": "2026-11-20T10:00:00+09:00"}, "end": {"dateTime": "2026-11-20T11:00:00+09:00"}}, "personal"))

    def test_13_travel_late(self):
        m = E("m", "移動", "2026-11-20 09:00 JST", "2026-11-20 10:10 JST", kind="move")
        e = E("e", "面接指導", "2026-11-20 10:00 JST", "2026-11-20 10:50 JST", location="学校")
        self.assertEqual([t["type"] for t in travel_check([m, e])], ["移動が間に合わない"])

    def test_14_quiet_edges(self):
        self.assertFalse(Context(P("2026-11-18 07:00 JST"), P("2026-11-19 07:00 JST")).is_quiet())
        self.assertTrue(Context(P("2026-11-18 22:00 JST"), P("2026-11-19 07:00 JST")).is_quiet())

    def test_15_16_duplicates(self):
        p1 = E("p1", "ピアノ", "2026-11-19 11:45 JST", "2026-11-19 12:35 JST"); p2 = E("p2", "ピアノ", "2026-11-19 11:45 JST", "2026-11-19 12:35 JST")
        self.assertEqual(len(find_duplicates([p1, p2])), 1)
        p3 = E("p3", "ピアノ たろう", "2026-11-19 11:45 JST", "2026-11-19 12:35 JST")
        h3 = E("h3", "ピアノ", "2026-11-19 11:45 JST", "2026-11-19 12:35 JST", calendar="household")
        self.assertEqual(len(find_duplicates([p3, h3])), 1)
        self.assertEqual(find_conflicts([p3, h3]), [])  # ダブりは重なりにしない

    def test_17_18_19_nightly(self):
        hk1 = E("hk1", "物理", "2026-11-18 13:50 JST", "2026-11-18 15:10 JST", calendar="household", owner="self")
        r = review([], [hk1], P("2026-11-17 22:00 JST"), goals=[])
        self.assertTrue(any(f["kind"] == "家用だけ" for f in r["morning"] + r["record_only"]))
        r = review([Case("c", "歯医者の予約を取る", "予定あり", missing=["時刻"])], [], P("2026-11-17 22:00 JST"), goals=[])
        self.assertEqual(len(r["morning"] + r["record_only"]), 1)
        near = E("f1", "遠足", "2026-11-24 09:00 JST", "2026-11-24 15:00 JST", location="公園")
        r = review([], [near], P("2026-11-17 22:00 JST"), goals=[])
        self.assertEqual(len(r["morning"] + r["record_only"]), 1)
        far = E("f2", "遠足", "2026-12-10 09:00 JST", "2026-12-10 15:00 JST", location="公園")
        r = review([], [far], P("2026-11-17 22:00 JST"), goals=[])
        self.assertEqual(len(r["morning"] + r["record_only"]), 0)

    def test_20_unregistered_body_safety_night(self):
        d = U({"title": "知らない番号から：体調が", "time": "2026-11-19 10:00 JST", "body_safety": True, "registered_source": False, "time_in_text": False},
              now="2026-11-18 23:30 JST", next_summary="2026-11-19 07:00 JST")
        self.assertEqual(d.deliver, "morning_top")


class Round2(unittest.TestCase):
    """直した後に作った新しい意地悪ケース。"""

    def test_a01_safety_only_cannot_wait_not_c(self):
        d = U({"title": "迎えの時刻変更（至急）", "time": "2026-11-19 07:30 JST", "cannot_wait": True}, night_setting="safety_only", **NIGHT)
        self.assertEqual(d.deliver, "morning_top")

    def test_a02_c_from_unregistered_rings_with_warning(self):
        d = U({"title": "家族が入院", "family_emergency": True, "registered_source": False, "time_in_text": False}, **NIGHT)
        self.assertEqual(d.deliver, "now")
        self.assertIn("知らない相手です。本当か確かめてください", d.notes)

    def test_a03_past_b(self):
        d = U({"title": "返却期限（今夜中に延長手続きで延滞金を防げる）", "time": "2026-11-18 22:00 JST", "importance": "高",
               "recover_by": "2026-11-19 00:00 JST"}, **NIGHT)
        self.assertEqual((d.tier, d.deliver), ("過ぎた予定の急ぎ", "now"))

    def test_a04_past_b_needs_high(self):
        d = U({"title": "x", "time": "2026-11-18 22:00 JST", "importance": "中", "recover_by": "2026-11-19 00:00 JST"}, **NIGHT)
        self.assertEqual(d.tier, "過ぎていた")

    def test_a05_past_without_time_in_text_is_not_a(self):
        d = U({"title": "x", "time": "2026-11-18 22:00 JST", "importance": "高", "late_ok": True, "time_in_text": False}, **NIGHT)
        self.assertEqual((d.tier, d.deliver), ("過ぎていた", "morning_top"))

    def test_a06_past_a_daytime_now_and_safety_only_night_morning(self):
        d = U({"title": "x", "time": "2026-11-18 13:00 JST", "importance": "中", "late_ok": True}, now="2026-11-18 13:30 JST", next_summary="2026-11-19 07:00 JST")
        self.assertEqual(d.deliver, "now")
        d = U({"title": "x", "time": "2026-11-18 22:30 JST", "importance": "中", "late_ok": True}, night_setting="safety_only", **NIGHT)
        self.assertEqual(d.deliver, "morning_top")

    def test_a07_override_night_off(self):
        d = U({"title": "x", "time": "2026-11-19 07:20 JST", "cannot_wait": True, "night_now": False}, **NIGHT)
        self.assertEqual(d.deliver, "morning_top")

    def test_a08_override_night_on_not_for_safety_only(self):
        d = U({"title": "x", "time": "2026-11-19 12:00 JST", "importance": "高", "night_now": True}, night_setting="safety_only", **NIGHT)
        self.assertEqual(d.deliver, "morning_top")
        d = U({"title": "x", "time": "2026-11-19 12:00 JST", "importance": "高", "night_now": True}, **NIGHT)
        self.assertEqual(d.deliver, "now")

    def test_a09_load_not_double_counted(self):
        a = E("a", "x", "2026-11-18 10:00 JST", "2026-11-18 12:00 JST"); b = E("b", "y", "2026-11-18 11:00 JST", "2026-11-18 13:00 JST")
        self.assertEqual(day_load_minutes([a, b], date(2026, 11, 18)), 180)

    def test_a10_not_duplicate_when_time_differs(self):
        a = E("a", "会議", "2026-11-18 10:00 JST", "2026-11-18 11:00 JST"); b = E("b", "会議", "2026-11-18 14:00 JST", "2026-11-18 15:00 JST")
        self.assertEqual(find_duplicates([a, b]), [])

    def test_a11_kana_width_duplicates(self):
        a = E("a", "ピアノ", "2026-11-18 10:00 JST", "2026-11-18 11:00 JST"); b = E("b", "ぴあの", "2026-11-18 10:00 JST", "2026-11-18 11:00 JST", calendar="household")
        self.assertEqual(len(find_duplicates([a, b])), 1)

    def test_a12_unknown_pairs_not_conflict(self):
        u1 = E("u1", "x", "2026-11-18 10:00 JST", "2026-11-18 11:00 JST", owner="unknown"); u2 = E("u2", "y", "2026-11-18 10:30 JST", "2026-11-18 11:30 JST", owner="unknown")
        self.assertEqual(find_conflicts([u1, u2]), [])

    def test_a13_return_move_is_not_going(self):
        r = E("r", "移動（会場→家）", "2026-11-18 09:00 JST", "2026-11-18 09:50 JST", kind="move", meta={"direction": "return"})
        e = E("e", "講習", "2026-11-18 10:00 JST", "2026-11-18 12:00 JST", location="会場")
        self.assertIn("移動の予定が無い", [t["type"] for t in travel_check([r, e])])

    def test_a14_move_ending_on_time_is_ok(self):
        m = E("m", "移動", "2026-11-18 09:00 JST", "2026-11-18 10:00 JST", kind="move")
        e = E("e", "講習", "2026-11-18 10:00 JST", "2026-11-18 12:00 JST", location="会場")
        self.assertEqual(travel_check([m, e]), [])

    def test_a15_weekday_year_rollover(self):
        self.assertEqual(check_weekdays("1/1(金)に集合", date(2026, 12, 20)), [])
        self.assertEqual(len(check_weekdays("1/1(木)に集合", date(2026, 12, 20))), 1)

    def test_a16_weekday_impossible_date(self):
        self.assertEqual(check_weekdays("2/30(月)", date(2026, 11, 18))[0]["error"], "ありえない日付")

    def test_a17_coverage_optional_source(self):
        S = [Source("p", "個人用", "personal"), Source("x", "予備", "family", required=False)]
        self.assertTrue(coverage(S, {"p": {"ok": True}})["all_ok"])
        self.assertFalse(coverage(S, {"x": {"ok": True}})["can_say_none"])

    def test_a18_morning_all_and_other_and_merge(self):
        S = [Source("p", "個人用カレンダー", "personal"), Source("h", "家用カレンダー", "household")]
        g = lambda i, t, s, e: {"id": i, "summary": t, "start": {"dateTime": s}, "end": {"dateTime": e}}
        reads = {"p": {"ok": True, "events": [g("1", "ピアノ", "2026-11-18T10:00:00+09:00", "2026-11-18T11:00:00+09:00")]},
                 "h": {"ok": True, "events": [g("2", "ピアノ", "2026-11-18T10:00:00+09:00", "2026-11-18T11:00:00+09:00"),
                                               g("3", "家族で夕食", "2026-11-18T18:00:00+09:00", "2026-11-18T19:00:00+09:00"),
                                               g("4", "テスト花子 ヨガ", "2026-11-18T12:00:00+09:00", "2026-11-18T13:00:00+09:00")]}}
        r = build_morning(date(2026, 11, 18), S, reads, {"self_names": ["テスト太郎"], "roster": ["テスト花子"]})
        self.assertIn("・10:00〜11:00 ピアノ（個人用カレンダー・家用カレンダー）", r["body"])
        self.assertIn("家族で夕食", r["body"])
        self.assertNotIn("ヨガ", r["body"])
        self.assertEqual(r["body"].count("ピアノ"), 1)

    def test_a19_morning_nothing(self):
        S = [Source("p", "個人用カレンダー", "personal"), Source("h", "家用カレンダー", "household")]
        self.assertEqual(build_morning(date(2026, 11, 18), S, {"p": {"ok": True, "events": []}, "h": {"ok": True, "events": []}})["body"], "今日の知らせはありません")
        b = build_morning(date(2026, 11, 18), S, {"p": {"ok": True, "events": []}})["body"]
        self.assertNotIn("ありません", b); self.assertNotIn("予定なし", b); self.assertIn("家用カレンダーは未確認", b)

    def test_a20_classify_priority(self):
        cfg = {"self_names": ["たろう"], "roster": ["はなこ"], "table": [{"word": "当番", "class": "other"}], "learned": [{"word": "ヨガ", "class": "other"}]}
        self.assertEqual(classify("たろう 当番", cfg=cfg)[0], "other")  # (0) 表がいちばん優先
        self.assertEqual(classify("ヨガ タロウ", cfg=cfg)[0], "self")  # 学習より本人の呼ばれ方。カナも同じ
        self.assertEqual(classify("はなこさん 歯医者", cfg=cfg)[0], "other")
        self.assertEqual(classify("共同購入の受け取り", cfg=cfg)[0], "unknown")
        self.assertEqual(classify("家族みんなで外食", cfg=cfg)[0], "all")

    def test_a21_replan(self):
        now = P("2026-11-19 22:00 JST")
        cases = [Case("c1", "書類の時刻を確かめる", "確認待ち", due=P("2026-11-22 09:00 JST"), missing=["時刻"]),
                 Case("c2", "旅行の日程を決める", "迷い")]
        r1 = review(cases, [], P("2026-11-18 22:00 JST"), [])
        r2 = review(cases, [], now, [], previous=r1)
        k1 = r1["morning"][0]["key"]
        self.assertEqual(next(f for f in r2["morning"] if f["key"] == k1)["status"], "上げた")
        r3 = review(cases, [], now + timedelta(days=1), [], previous=r2)
        self.assertIn(k1, [f["key"] for f in r3["weekly"]])
        r4 = review(cases, [], now, [], previous=r1, feedback={k1: {"verdict": "外れ", "at": "2026-11-19 07:30 JST"}})
        self.assertIn(k1, [f["key"] for f in r4["suppressed"]])
        r5 = review([cases[1]], [], now, [], previous=r1)
        self.assertIn(k1, r5["diff"]["片付いた"])

    def test_a22_step_slots_do_not_overlap(self):
        now = P("2026-11-18 22:00 JST")
        busy = [E(f"b{i}", "授業", f"2026-11-{19+i} 09:00 JST", f"2026-11-{19+i} 12:00 JST") for i in range(6)]
        c = Case("c", "レポート提出", "締切", due=P("2026-11-24 17:00 JST"), heavy=True)
        steps = plan_steps(c, now, busy)
        self.assertEqual(len(steps), 3)
        for s in steps:
            self.assertIn("12:00〜", s)  # 9〜12時は埋まっているので12時から

    def test_a23_cli_accepts_past_fields(self):
        data = {"item": {"title": "x", "time": "2026-11-18 22:00 JST", "end": "2026-11-18 23:30 JST", "importance": "高"},
                "context": {"now": "2026-11-18 22:20 JST", "next_summary": "2026-11-19 07:00 JST"}}
        p = subprocess.run([sys.executable, "-m", "yoteiban_rd.cli", "urgency"], input=json.dumps(data), capture_output=True,
                           text=True, cwd=os.path.join(_paths.ROOT, "lib"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)["tier"], "過ぎた予定の急ぎ")

    def test_a24_slice_falls_back_to_full(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# 正本\n## 2. 大原則\nA\n## 19. 夜\nB\n")
        p = subprocess.run([sys.executable, "-m", "yoteiban_rd.rules_slice", "--seihon", f.name, "--task", "notify"],
                           capture_output=True, text=True, cwd=os.path.join(_paths.ROOT, "lib"))
        self.assertEqual(p.returncode, 3)
        self.assertIn("## 19. 夜", p.stdout); self.assertIn("全文", p.stderr)
        os.unlink(f.name)

    def test_a25_link_same_cause_and_shared_word(self):
        now = P("2026-11-17 22:00 JST")
        ret = E("r", "移動（会場→家）", "2026-11-23 17:40 JST", "2026-11-23 18:40 JST", kind="move", meta={"direction": "return"})
        bday = Event("b", "北町のおばあちゃん誕生日", P("2026-12-05 00:00 JST"), P("2026-12-06 00:00 JST"), all_day=True, owner="all")
        cases = [Case("c1", "面接（11/23・11/28の時刻）", "確認待ち", due=P("2026-11-23 09:00 JST"), missing=["時刻"]),
                 Case("c2", "北町へ行く日程を決める", "迷い")]
        r = review(cases, [ret, bday], now, [])
        kinds = [f["kind"] for f in r["morning"]]
        self.assertIn("まとめ", kinds); self.assertIn("つながり", kinds)
        self.assertEqual(r["morning"][0]["kind"], "まとめ")


if __name__ == "__main__":
    unittest.main()
