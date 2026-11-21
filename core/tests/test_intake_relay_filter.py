import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
from yoteiban_core.intake import handle
from yoteiban_core.ledger import Ledger


class IntakeRelayFilterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "ledger.sqlite")
        self.secret = "fixture-key"
        self.config = {"relay_bots": [{"sender_handles": ["fixture-bot"],
                                       "call_markers": ["CALL_X"],
                                       "reply_markers": ["BOT_X"], "owner": "family"}]}

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def receive(self, text, *, sender="fixture-human", source="imessage"):
        payload = {"kind": "yoteiban-wake", "request_id": "fixture-" + str(abs(hash(text))),
                   "source": source, "native_ref": "guid:" + str(abs(hash(text))),
                   "received_at": "2026-01-01T00:00:00+00:00", "body_text": text,
                   "sender_role": "fixture", "sender_handle": sender}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        signature = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        return handle(self.ledger, self.config, {"X-Yoteiban-Signature": signature}, body,
                      "2026-01-01T00:01:00+00:00", self.secret)

    def test_intake_filters_configured_call_bot_sender_and_prefix_reply(self):
        for result in (self.receive("CALL_X request"), self.receive("ordinary", sender="fixture-bot"),
                       self.receive("BOT_X reply")):
            self.assertTrue(result["ok"])
            self.assertEqual(result["detail_code"], "RELAY_BOT_FILTERED")
            self.assertIsNone(result["input_id"])
        self.assertEqual(self.ledger.conn.execute("select count(*) from raw_inputs").fetchone()[0], 0)

    def test_marker_in_middle_is_not_filtered_and_is_ingested(self):
        result = self.receive("ordinary mention BOT_X in text")
        self.assertEqual(result["detail_code"], "OK")
        self.assertTrue(result["input_id"])
        self.assertEqual(self.ledger.conn.execute("select count(*) from raw_inputs").fetchone()[0], 1)

    def test_empty_config_keeps_existing_message_intake(self):
        old = self.config
        self.config = {"relay_bots": []}
        result = self.receive("CALL_X request")
        self.assertEqual(result["detail_code"], "OK")
        self.assertEqual(self.ledger.conn.execute("select count(*) from raw_inputs").fetchone()[0], 1)
        self.config = old


if __name__ == "__main__":
    unittest.main()
