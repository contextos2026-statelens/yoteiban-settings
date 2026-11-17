import json, os, subprocess, sys, unittest
import _paths  # noqa

ENV = dict(os.environ, PYTHONPATH=os.path.join(_paths.ROOT, "lib"))


def run(task, data):
    p = subprocess.run([sys.executable, "-m", "yoteiban_rd.cli", task], input=json.dumps(data), capture_output=True,
                       text=True, env=ENV)
    return p.returncode, json.loads(p.stdout)


class CliTest(unittest.TestCase):
    def test_urgency(self):
        rc, out = run("urgency", {"item": {"title": "提出", "time": "2026-11-20 17:00 JST", "travel_min": 0, "prep_min": 30},
                                  "context": {"now": "2026-11-20 10:00 JST", "next_summary": "2026-11-21 07:00 JST"}})
        self.assertEqual((rc, out["tier"]), (0, "今日中"))

    def test_check_write(self):
        rc, out = run("check-write", {"new": {"id": "n", "title": "面接指導", "start": "2026-11-28 10:30 JST", "end": "2026-11-28 11:20 JST", "location": "学校"},
                                      "events": [{"id": "a", "title": "自習室", "start": "2026-11-28 10:00 JST", "end": "2026-11-28 13:00 JST", "calendar": "household"}]})
        self.assertEqual(rc, 0)
        self.assertFalse(out["ok"])
        self.assertTrue(out["conflicts"]) ; self.assertTrue(out["travel"])

    def test_bad_input(self):
        rc, out = run("urgency", {"item": {"title": "x", "time": "2026-11-20 17:00"}, "context": {"now": "2026-11-20 10:00 JST", "next_summary": "2026-11-21 07:00 JST"}})
        self.assertEqual(rc, 2); self.assertIn("error", out)
        rc, out = run("check-write", {})
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
