import os
import unittest
import _paths  # noqa
from yoteiban_rd.calc import Event, parse_time as T
from yoteiban_rd.nightly import Case, review
from yoteiban_rd.rules_slice import TASK_CHAPTERS, slice_for, chapters
from yoteiban_rd.shadow import ShadowSink, ShadowViolation, compare


def ev(i, s, e, **k):
    return Event(id=i, title=k.pop("title", i), start=T(s), end=T(e), **k)


class NightlyTest(unittest.TestCase):
    now = T("2026-11-20 22:00 JST")

    def test_findings_and_cap(self):
        evs = [ev("a", "2026-11-22 10:00 JST", "2026-11-22 13:00 JST", calendar="household"),
               ev("b", "2026-11-22 10:30 JST", "2026-11-22 11:20 JST"),
               ev("m", "2026-11-26 17:40 JST", "2026-11-26 18:50 JST", kind="move", meta={"direction": "return"}),
               ev("bd", "2026-12-05 00:00 JST", "2026-12-06 00:00 JST", title="祖父の誕生日", all_day=True, owner="all")]
        cases = [Case("c1", "面接指導の時刻", "確認待ち", due=T("2026-11-23 09:00 JST"), missing=["時刻"], heavy=True),
                 Case("c2", "提出の締切", "締切", due=T("2026-12-03 17:00 JST"), heavy=True),
                 Case("c3", "旅行の日程", "迷い"),
                 Case("c4", "返事待ち", "返事待ち", waiting_since=T("2026-11-16 10:00 JST")),
                 Case("c5", "止まった件", "保留", status="paused", next_line="資料を開く"),
                 Case("c6", "済んだ件", "予定あり", status="done")]
        r = review(cases, evs, self.now, goals=["面接"], daily_load_limit_min=60)
        kinds = {f["kind"] for f in r["morning"] + r["record_only"]}
        for k in ("重なり", "移動", "情報不足", "手順分け", "決め事", "返事待ち", "戻る一行", "機会", "作業量の目安"):
            self.assertIn(k, kinds)
        self.assertLessEqual(len(r["morning"]), 3)
        self.assertNotIn("済んだ件", str(r))
        self.assertEqual(r["morning"][0]["kind"], "重なり")

    def test_nothing_to_do(self):
        r = review([], [], self.now, goals=[])
        self.assertEqual((r["morning"], r["record_only"]), ([], []))


class SliceTest(unittest.TestCase):
    SAMPLE = "# 正本\n## 目次\nx\n" + "".join(f"## {i}. 章{i}\n本文{i}\n" for i in range(1, 31)) + "## 付記\nz\n"

    def test_slice(self):
        r = slice_for("notify", self.SAMPLE)
        self.assertEqual(r["missing"], [])
        self.assertIn("本文19", r["text"]); self.assertNotIn("本文21", r["text"]); self.assertNotIn("付記", r["text"])
        self.assertIn("本文2\n", r["text"])
        with self.assertRaises(KeyError):
            slice_for("unknown", self.SAMPLE)
        self.assertEqual(slice_for("notify", "## 2. x\n")["missing"], [12, 16, 17, 18, 19, 20, 22, 27])

    def test_urgency_tasks_have_ch19_20(self):
        # 急ぎの段階を決める仕事（intake・notify・nightly）は 19・20章を必ず読む（試験Bで欠けていた）
        from yoteiban_rd.rules_slice import task_chapter_nos
        for t in ("intake", "notify", "nightly"):
            self.assertIn(19, task_chapter_nos(t), t)
            self.assertIn(20, task_chapter_nos(t), t)
        self.assertIn(20, task_chapter_nos("write"))  # 型による自動追加の範囲
        for t in ("intake", "write"):  # 急ぎ・自動追加には型（20.1）・動き（20.2）・静かな時間（20.6）が要る
            for s in ("20.1", "20.2", "20.6"):
                self.assertIn(s, TASK_CHAPTERS[t], t)

    def test_skill_chapter_text_matches_map(self):
        import re
        for d in ("intake", "notify", "nightly", "write", "delegate", "infer", "adjust", "interval", "setup", "chat"):
            txt = open(os.path.join(_paths.ROOT, "skills", d, "SKILL.md"), encoding="utf-8").read()
            m = re.search(r"--task (\w+)`（第([\d・.]+)章）", txt)
            self.assertIsNotNone(m, d)
            self.assertEqual(m.group(1), d)
            self.assertEqual(m.group(2).split("・"), [str(x) for x in TASK_CHAPTERS[d]], d)

    def test_every_task_has_principles(self):
        for t, ch in TASK_CHAPTERS.items():
            self.assertIn(2, ch, t)

    def test_real_seihon_if_present(self):
        if not _paths.SEIHON:
            self.skipTest("正本が無い")
        text = open(_paths.SEIHON, encoding="utf-8").read()
        for t in TASK_CHAPTERS:
            r = slice_for(t, text)
            self.assertEqual(r["missing"], [], t)
            if "### 20.1 " in text:
                self.assertEqual(r["widened"], [], t)  # 節の見出しのある正本では、節が全部見つかる
            self.assertLess(r["chars"], r["full_chars"] * 0.5, t)  # 参照する章を足した後でも半分未満


class ShadowTest(unittest.TestCase):
    def test_sink_blocks(self):
        s = ShadowSink()
        for f in (s.notify, s.write_calendar, s.send):
            with self.assertRaises(ShadowViolation):
                f("x")
        self.assertEqual(len(s.attempts), 3)

    def test_compare(self):
        old = [{"ref": "1", "related": True, "received_at": "2026-11-20T10:00:00+09:00", "decided_at": "2026-11-20T11:30:00+09:00"},
               {"ref": "2", "related": False}, {"ref": "3", "related": True}]
        new = [{"ref": "1", "decision": "起こす", "received_at": "2026-11-20T10:00:00+09:00", "decided_at": "2026-11-20T10:02:00+09:00"},
               {"ref": "2", "decision": "起こす"}, {"ref": "3", "decision": "何もしない"}]
        r = compare(old, new)
        self.assertEqual((r["missed_by_new"], r["extra_wakes"]), (["3"], ["2"]))
        self.assertFalse(r["pass"])
        self.assertEqual(r["latency_new"]["median_min"], 2.0)
        ok = compare([{"ref": "1", "related": True}], [{"ref": "1", "decision": "朝へ"}])
        self.assertTrue(ok["pass"])
        self.assertFalse(compare([], [], sink_attempts=1)["pass"])


if __name__ == "__main__":
    unittest.main()
