import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "redesign" / "lib"))

from yoteiban_rd.calc import departure
from yoteiban_rd import cli


class DepartureCalcTests(unittest.TestCase):
    def test_departure_calculation_rolls_back_to_previous_day(self):
        result = departure("2026-11-21T00:20:00+09:00", 30, 10)
        self.assertEqual(result["departure_time"], "2026-11-20T23:40:00+09:00")
        self.assertTrue(result["create_reminder"])
        self.assertFalse(result["unconfirmed"])

    def test_unknown_travel_duration_returns_ask_once_without_reminder(self):
        result = departure("2026-11-21T10:00:00+09:00", None, 10, travel_confirmed=False)
        self.assertIsNone(result["departure_time"])
        self.assertTrue(result["unconfirmed"])
        self.assertFalse(result["create_reminder"])
        self.assertTrue(result["ask_owner_once"])
        self.assertIn("未確認", result["suggestion"])

    def test_cli_departure_is_json_calculation(self):
        stdin = io.StringIO(json.dumps({"start": "2026-11-21T10:00:00+09:00", "travel_minutes": 20,
                                        "prep_minutes": 5}))
        stdout = io.StringIO()
        with patch("sys.stdin", stdin), redirect_stdout(stdout):
            self.assertEqual(cli.main(["departure"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["departure_time"], "2026-11-21T09:35:00+09:00")


class MirroredDepartureSkillsTests(unittest.TestCase):
    def test_root_and_redesign_skill_copies_match(self):
        for name in ("adjust", "notify", "write", "setup", "selftest"):
            with self.subTest(skill=name):
                primary = (ROOT / "skills" / name / "SKILL.md").read_bytes()
                mirror = (ROOT / "redesign" / "skills" / name / "SKILL.md").read_bytes()
                self.assertEqual(primary, mirror)


if __name__ == "__main__":
    unittest.main()
