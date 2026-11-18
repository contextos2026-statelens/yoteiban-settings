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


class CliErrorTest(unittest.TestCase):
    def test_missing_keys_give_clear_error_not_traceback(self):
        cases = [({}, "item"), ({"item": {"title": "x"}}, "context"), ({"item": {}, "context": {}}, "title"),
                 ({"item": {"title": "x"}, "context": {"now": "2026-11-20 10:00 JST"}}, "next_summary"),
                 ({"item": {"title": "x", "tiem": "a"}, "context": {"now": "2026-11-20 10:00 JST", "next_summary": "2026-11-21 07:00 JST"}}, "tiem"),
                 ([], "オブジェクト")]
        for data, word in cases:
            p = subprocess.run([sys.executable, "-m", "yoteiban_rd.cli", "urgency"], input=json.dumps(data),
                               capture_output=True, text=True, env=ENV)
            self.assertEqual(p.returncode, 2, data)
            self.assertNotIn("Traceback", p.stderr + p.stdout)
            out = json.loads(p.stdout)
            self.assertIn(word, out["error"])
            self.assertIn("example", out)

    def test_documented_example_runs(self):
        import re
        txt = open(os.path.join(_paths.ROOT, "skills", "intake", "SKILL.md"), encoding="utf-8").read()
        block = re.search(r"`(\{\"item\".*?)`", txt, re.S).group(1)
        rc, out = run("urgency", json.loads(block))
        self.assertEqual(rc, 0)
        self.assertEqual((out["tier"], out["deliver"]), ("今すぐ", "now"))
        self.assertEqual(out["action_deadline"], "2026-11-19 07:50 JST")
        self.assertEqual(out["slack_min"], 50)
        self.assertTrue(out["estimated"])


if __name__ == "__main__":
    unittest.main()
