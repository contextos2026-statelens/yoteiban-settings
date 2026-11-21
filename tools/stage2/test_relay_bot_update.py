import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("relay_bot_update", HERE / "relay_bot_update.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class RelayBotUpdateTests(unittest.TestCase):
    def test_request_always_starts_with_configured_intro(self):
        draft = mod.request_text("1.0.43", "fixture configuration", "fixture nickname")
        self.assertTrue(draft.startswith("【fixture nicknameの予定ばんからのメッセージです】\n"))
        self.assertIn("送信前に本人へ表示", draft)
        self.assertIn("送信も適用も行いません", draft)
        with self.assertRaises(ValueError):
            mod.request_text("1.0.43", "fixture configuration", "")

    def test_result_counts_are_checked(self):
        result = {"bundle_version": "1.0.43", "target_version": "fixture", "result": "success",
                  "tests": {"fixture_cases": 2, "passed": 2, "failed": 0}, "rolled_back": False,
                  "detail_code": "OK"}
        self.assertEqual(mod.validate_result(result), result)
        result["tests"]["passed"] = 3
        with self.assertRaises(ValueError):
            mod.validate_result(result)

    def test_bundle_manifest_hashes_match(self):
        root = HERE.parents[1] / "relay_bot_update"
        self.assertEqual(mod.verify_manifest(root), [])


if __name__ == "__main__":
    unittest.main()
