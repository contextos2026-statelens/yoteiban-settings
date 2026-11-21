import json
import os
import re
import unittest
from pathlib import Path

R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS = Path(R) / "docs"
JSON_PATH = DOCS / "handoff_checklist.json"
MD_PATH = DOCS / "HANDOFF_CHECKLIST.md"

REQUIRED_IDS = [f"C{i:02d}" for i in range(1, 18)]
YOTEIBAN_SKILLS = {
    "予定ばんの運用ルール",
    "予定ばんの初回設定",
    "予定ばんの自己試験",
    "予定ばんの受け取りと振り分け",
    "予定ばんの推測と確認",
    "予定ばんのカレンダーへの書き込み",
    "予定ばんの予定調整",
    "予定ばんの重要度と通知",
    "予定ばんの毎晩の見直し",
    "予定ばんの対話の早道",
    "予定ばんの確認間隔の学習",
    "予定ばんの委任送信",
}
OTHER_INCLUDED = {
    "今日の予定と未読メールの要約",
    "研究テーマの多方向調査と統合",
    "朝の短歌",
    "連携ボットハンズオン進行",
}
FRAMEWORK_ONLY = {
    "プレゼント探し",
    "プレゼント好み分析ラウンド",
}

FORBIDDEN_IN_HANDOFF = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"skill-\d{10,}"),
    re.compile(r"store-[0-9a-f]{6,}", re.I),
    re.compile("automations" + "/webhook/"),
]


class HandoffChecklistTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        self.md = MD_PATH.read_text(encoding="utf-8")

    def test_items_are_yes_no_and_complete(self):
        ids = [item["id"] for item in self.data["items"]]
        self.assertEqual(ids, REQUIRED_IDS)
        self.assertIn("はい", self.md)
        self.assertIn("いいえ", self.md)
        self.assertIn("一つでも「いいえ」なら、直してから渡す", self.md)
        for item in self.data["items"]:
            self.assertIn(item["id"], self.md)
            self.assertIn(item["text"], self.md)
            self.assertRegex(self.md, rf"\| {item['id']} \| .+ \|  \|  \|")

    def test_skill_inventory(self):
        by_name = {s["name"]: s for s in self.data["skills"]}
        self.assertEqual(set(n for n, s in by_name.items() if s["kind"] == "yoteiban"), YOTEIBAN_SKILLS)
        for name in YOTEIBAN_SKILLS:
            self.assertEqual(by_name[name]["action"], "include")
            self.assertIn(name, self.md)
        for name in OTHER_INCLUDED:
            self.assertEqual(by_name[name]["kind"], "other")
            self.assertEqual(by_name[name]["action"], "include")
            self.assertIn(name, self.md)
        for name in FRAMEWORK_ONLY:
            self.assertEqual(by_name[name]["action"], "framework_only")
            self.assertIn(name, self.md)
            self.assertIn("枠だけ", self.md)
        self.assertTrue(all(s["action"] in {"include", "framework_only", "exclude"} for s in self.data["skills"]))

    def test_handoff_docs_have_no_fixed_values(self):
        for path in (JSON_PATH, MD_PATH):
            text = path.read_text(encoding="utf-8")
            for rx in FORBIDDEN_IN_HANDOFF:
                self.assertIsNone(rx.search(text), msg=f"{path.name} hit {rx.pattern}")

    def test_safety_and_no_transfer(self):
        blob = self.md + json.dumps(self.data, ensure_ascii=False)
        for phrase in (
            "家用カレンダーには書かない",
            "自動では送らない",
            "買わない・払わない",
            "新しい人や外部に渡さない",
            "AIなので間違えることもある",
        ):
            self.assertIn(phrase, blob)

    def test_new_owner_points_here(self):
        text = (DOCS / "NEW_OWNER.md").read_text(encoding="utf-8")
        self.assertIn("HANDOFF_CHECKLIST.md", text)


if __name__ == "__main__":
    unittest.main()
