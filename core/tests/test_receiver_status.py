import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
from yoteiban_core.receiver_status import update_status


class ReceiverStatusTests(unittest.TestCase):
    def test_connection_state_is_separate_and_existing_answer_data_survives(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "private" / "status.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"setup_answers":{"iMessage":"使う"},
                                        "connections":{"other":{"state":"connected"}}}))
            result = update_status(path, "mac_chatdb", "pending_user_action", detail_code="FDA_REQUIRED")
            self.assertEqual(result["setup_answers"], {"iMessage":"使う"})
            self.assertEqual(result["connections"]["other"], {"state":"connected"})
            self.assertEqual(json.loads(path.read_text())["connections"]["mac_chatdb"]["state"],
                             "本人の操作待ち")
            self.assertEqual(result["connections"]["mac_chatdb"]["legacy_state"], "pending_user_action")

    def test_generic_integration_records_step_without_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            result = update_status(path, "reader_heartbeat", "接続テスト待ち",
                                   step="heartbeat", detail_code="HTTP_UNREACHABLE")
            entry = result["connections"]["reader_heartbeat"]
            self.assertEqual((entry["state"], entry["step"], entry["detail_code"]),
                             ("接続テスト待ち", "heartbeat", "HTTP_UNREACHABLE"))


if __name__ == "__main__":
    unittest.main()
