import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
from yoteiban_core.relay_bot_filter import classify_relay_bot


class RelayBotFilterTests(unittest.TestCase):
    def test_empty_config_has_no_effect(self):
        self.assertIsNone(classify_relay_bot("CALL_X ordinary request", "sender", []))

    def test_family_and_self_bots_keep_separate_markers_and_sender_ids(self):
        config = [
            {"label":"<self>", "sender_handles":["self-id"], "call_markers":["SELF_CALL"],
             "reply_markers":["SELF_REPLY"], "owner":"self"},
            {"label":"<family>", "sender_handles":["family-id"], "call_markers":["FAMILY_CALL"],
             "reply_markers":["FAMILY_REPLY"], "owner":"family"},
        ]
        self.assertEqual(classify_relay_bot("SELF_CALL task", "other", config), "relay_bot_call")
        self.assertEqual(classify_relay_bot("FAMILY_CALL task", "other", config), "relay_bot_call")
        self.assertIsNone(classify_relay_bot("normal SELF_REPLY", "other", config))
        self.assertEqual(classify_relay_bot("SELF_REPLY normal", "other", config), "relay_bot_message")
        self.assertEqual(classify_relay_bot("ordinary", "self-id", config), "self_relay_bot_notice")
        self.assertEqual(classify_relay_bot("ordinary", "family-id", config), "relay_bot_message")

    def test_marker_in_middle_is_not_a_call(self):
        config = [{"label":"<fixture>", "call_markers":["CALL_X"],
                   "reply_markers":[], "sender_handles":[], "owner":"family"}]
        self.assertIsNone(classify_relay_bot("ordinary CALL_X", "someone", config))


if __name__ == "__main__":
    unittest.main()
