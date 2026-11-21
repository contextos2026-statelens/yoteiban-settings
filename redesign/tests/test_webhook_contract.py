import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "redesign" / "mac"))
from yoteiban_core.intake import handle
from yoteiban_core.ledger import Ledger
from yoteiban_watch.waker import build_signed_body


class WebhookContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "ledger.sqlite")
        self.payload = {"kind":"yoteiban-wake", "request_id":"fixture-request", "source":"imessage",
                        "native_ref":"guid:fixture-id", "received_at":"2026-11-21T10:00:00+09:00",
                        "body_text":"fixture request", "sender_role":"家族"}

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_raw_and_envelope_vectors_are_accepted_by_same_intake(self):
        for envelope in (False, True):
            body, signature = build_signed_body(self.payload, "fixture-secret", envelope=envelope)
            result = handle(self.ledger, {}, {"X-Yoteiban-Signature":signature}, body,
                            "2026-11-21T10:01:00+09:00", "fixture-secret")
            self.assertTrue(result["ok"], result["detail_code"])
            self.assertIn(result["signed_by"], {"header"})
            self.assertEqual(result["detail_code"], "OK")

    def test_different_key_or_changed_body_bytes_fail_without_secret_echo(self):
        body, signature = build_signed_body(self.payload, "fixture-secret", envelope=False)
        result = handle(self.ledger, {}, {"X-Yoteiban-Signature":signature}, body,
                        "2026-11-21T10:01:00+09:00", "other-fixture-secret")
        self.assertFalse(result["ok"])
        self.assertEqual(result["detail_code"], "SIGNATURE_INVALID")
        self.assertNotIn("fixture-secret", repr(result))
        changed = body + b" "
        result = handle(self.ledger, {}, {"X-Yoteiban-Signature":signature}, changed,
                        "2026-11-21T10:01:00+09:00", "fixture-secret")
        self.assertFalse(result["ok"])
        self.assertEqual(result["detail_code"], "SIGNATURE_INVALID")

    def test_json_encoding_is_canonical_for_sender_helper(self):
        body, _ = build_signed_body(self.payload, "fixture-secret")
        self.assertEqual(body, json.dumps(self.payload, ensure_ascii=False, separators=(",", ":")).encode())


if __name__ == "__main__":
    unittest.main()
