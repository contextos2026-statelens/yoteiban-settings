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
                             "pending_user_action")


if __name__ == "__main__":
    unittest.main()
