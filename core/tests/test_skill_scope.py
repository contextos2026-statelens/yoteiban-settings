import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_one_skill.py"
SPEC = importlib.util.spec_from_file_location("check_one_skill", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SkillScopeTests(unittest.TestCase):
    def test_mirrored_skill_counts_once(self):
        self.assertEqual(MODULE.changed_skill_names(["skills/adjust/SKILL.md", "redesign/skills/adjust/SKILL.md"]), {"adjust"})

    def test_distinct_skills_are_detected(self):
        self.assertEqual(MODULE.changed_skill_names(["skills/adjust/SKILL.md", "redesign/skills/morning/SKILL.md"]), {"adjust", "morning"})


if __name__ == "__main__":
    unittest.main()
