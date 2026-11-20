import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "redesign" / "lib"))

from yoteiban_rd.urgency import is_express
from yoteiban_rd.latency import judge


class ExpressTests(unittest.TestCase):
    def test_express_conditions_are_calculated_without_time_arithmetic(self):
        self.assertTrue(is_express("今すぐ"))
        self.assertTrue(is_express("今日中", action_deadline_minutes=120))
        self.assertFalse(is_express("今日中", action_deadline_minutes=180))
        self.assertTrue(is_express("ふつう", travel_delay=True))
        self.assertTrue(is_express("ふつう", owner_event_minutes=120))
        self.assertFalse(is_express("ふつう"))
        self.assertFalse(is_express("ふつう", owner_event_minutes=121))

    def test_latency_counts_express_and_compares_with_dialog(self):
        rows = []
        for i, scene in enumerate(("dialog", "express")):
            start = datetime(2026, 11, 10 + i, 9)
            rows.append({"asked_at": start.isoformat() + "+09:00",
                         "replied_at": (start + timedelta(seconds=(100 if scene == "dialog" else 50))).isoformat() + "+09:00",
                         "scene": scene, "adjustments": 1})
        report = judge(rows)
        self.assertEqual(report["場面ごと"]["express"]["件数"], 1)
        self.assertEqual(report["expressとdialogの比較"],
                         {"dialog中央値秒": 100.0, "express中央値秒": 50.0})

    def test_owner_facing_example_has_no_pressure_terms(self):
        text = (ROOT / "skills/chat/SKILL.md").read_text(encoding="utf-8")
        lines = text.splitlines()
        marker = next(i for i, line in enumerate(lines) if "本人への文の例：" in line)
        line = lines[marker + 1]
        banned = ("大特急", "特急", "急いで", "至急", "大至急", "緊急", "!!", "‼", "🔴")
        self.assertFalse([word for word in banned if word in line])

    def test_canonical_morning_rules_match_notify_skill(self):
        canonical = (ROOT / "rules/seihon.md").read_text(encoding="utf-8")
        skill = (ROOT / "skills/notify/SKILL.md").read_text(encoding="utf-8")
        canonical = canonical[canonical.index("### 20.4 朝のまとめ"):canonical.index("### 20.5", canonical.index("### 20.4 朝のまとめ"))]
        skill = skill[skill.index("朝のまとめの書き方（2026-11-14 本人承認）"):]
        required = ("天気は未確認", "↳ 傘", "未確認", "新着の無い入口", "今日の知らせはありません", "最高と最低の気温")
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, canonical)
                self.assertIn(phrase, skill)
        for text in (canonical, skill):
            self.assertIn("📅予定の先頭", text)
            self.assertIn("その予定の直下", text)
            self.assertIn("読めた天気を添える", text)


if __name__ == "__main__":
    unittest.main()
