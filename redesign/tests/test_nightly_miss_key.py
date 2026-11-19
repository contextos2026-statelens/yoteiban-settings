import unittest
import _paths  # noqa
from yoteiban_rd.calc import Event, parse_time as T
from yoteiban_rd.nightly import Finding, _key, review


def ev(i, s, e, **k):
    return Event(id=i, title=k.pop("title", i), start=T(s), end=T(e), **k)


class MissKeyTest(unittest.TestCase):
    """「外れ」で止めるのは、その1件だけ。同じ件名の別の日・別の回は止めない。"""
    now = T("2026-11-20 22:00 JST")

    def test_key_with_refs_is_per_instance(self):
        a = Finding("移動", "通院", "", "", refs=["ev-1"], when="2026-11-24")
        b = Finding("移動", "通院", "", "", refs=["ev-2"], when="2026-12-01")
        self.assertNotEqual(_key(a), _key(b))
        self.assertEqual(_key(a), _key(Finding("移動", "別の件名", "", "", refs=["ev-1"], when="2026-11-24")))

    def test_fallback_key_includes_kind_and_date(self):
        a = Finding("作業量の目安", "通院", "", "", when="2026-11-24")
        b = Finding("作業量の目安", "通院", "", "", when="2026-12-01")
        c = Finding("未確認", "通院", "", "", when="2026-11-24")
        self.assertNotEqual(_key(a), _key(b))
        self.assertNotEqual(_key(a), _key(c))
        self.assertEqual(_key(a), _key(Finding("作業量の目安", "通院", "", "", when="2026-11-24")))
        self.assertIn("日付なし", _key(Finding("未確認", "通院", "", "")))

    def _move_days(self):
        # 同じ件名の通院が2回（内容は回ごとに違う）。どちらも移動の案が出る形
        return [ev("v1", "2026-11-24 10:00 JST", "2026-11-24 11:00 JST", title="通院"),
                ev("m1", "2026-11-24 09:00 JST", "2026-11-24 10:10 JST", kind="move", title="通院への移動"),
                ev("v2", "2026-11-27 10:00 JST", "2026-11-27 11:00 JST", title="通院"),
                ev("m2", "2026-11-27 09:00 JST", "2026-11-27 10:10 JST", kind="move", title="通院への移動")]

    def test_miss_on_one_visit_does_not_suppress_other_visit(self):
        evs = self._move_days()
        first = review([], evs, self.now, goals=[])
        found = first["morning"] + first["record_only"]
        by_when = {}
        for f in found:
            by_when.setdefault(f["when"], []).append(f)
        self.assertIn("2026-11-24", by_when, found)
        self.assertIn("2026-11-27", by_when, found)
        miss = by_when["2026-11-24"][0]
        fb = {miss["key"]: {"verdict": "外れ", "at": "2026-11-20T22:30:00+09:00"}}
        second = review([], evs, T("2026-11-21 22:00 JST"), goals=[], feedback=fb)
        suppressed = {f["key"] for f in second["suppressed"]}
        alive = {f["key"] for f in second["morning"] + second["record_only"]}
        self.assertEqual(suppressed, {miss["key"]})
        self.assertTrue(any(k for k in alive if k != miss["key"]))
        for f in second["morning"] + second["record_only"]:
            if f["when"] == "2026-11-27":
                break
        else:
            self.fail("別の日の通院の案まで止まっている")

    def test_title_only_findings_not_suppressed_on_other_dates(self):
        # 参照の無い「作業量の目安」：同じ件名形式でも日付が違えば別の件
        evs = [ev("x1", "2026-11-24 09:00 JST", "2026-11-24 12:00 JST"),
               ev("x2", "2026-11-25 09:00 JST", "2026-11-25 12:00 JST")]
        r = review([], evs, self.now, goals=[], daily_load_limit_min=60)
        loads = [f for f in r["morning"] + r["record_only"] if f["kind"] == "作業量の目安"]
        self.assertEqual(len(loads), 2)
        fb = {loads[0]["key"]: {"verdict": "外れ", "at": "2026-11-20T22:30:00+09:00"}}
        r2 = review([], evs, T("2026-11-21 22:00 JST"), goals=[], daily_load_limit_min=60, feedback=fb)
        left = [f for f in r2["morning"] + r2["record_only"] if f["kind"] == "作業量の目安"]
        self.assertEqual([f["key"] for f in left], [loads[1]["key"]])
        self.assertEqual([f["key"] for f in r2["suppressed"]], [loads[0]["key"]])

    def test_miss_expires_after_30_days(self):
        evs = self._move_days()
        first = review([], evs, self.now, goals=[])
        f = (first["morning"] + first["record_only"])[0]
        fb = {f["key"]: {"verdict": "外れ", "at": "2026-10-01T22:00:00+09:00"}}
        r = review([], evs, self.now, goals=[], feedback=fb)
        self.assertEqual(r["suppressed"], [])


if __name__ == "__main__":
    unittest.main()
