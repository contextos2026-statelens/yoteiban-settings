import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import hashlib
import json
import tempfile
import unittest

from yoteiban_core.send_check import verify


class SendCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = os.path.join(self.tmp.name, "used.json")
        self.payload = {"text": "友人Aへ送る文"}
        self.digest = hashlib.sha256(
            json.dumps(self.payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        self.expiry = "2026-11-21T10:10:00+09:00"
        self.request = {"approval_id": "approval-1", "payload": self.payload,
                        "payload_hash": self.digest, "expires_at": self.expiry, "op_id": "op-1"}
        self.approval = {"payload_hash": self.digest, "expires_at": self.expiry, "state": "有効"}

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, request=None, approval=None):
        view = lambda _id: approval if approval is not None else self.approval
        return verify(request or self.request, view, self.store, "2026-11-21T10:00:00+09:00")

    def test_valid_request_is_consumed_and_second_use_rejected(self):
        self.assertEqual(self.check(), (True, "送信できます"))
        ok, reason = self.check()
        self.assertFalse(ok)
        self.assertIn("すでに使用", reason)

    def test_rejects_missing_or_revoked_approval(self):
        self.assertFalse(verify(self.request, lambda _id: None, self.store,
                                "2026-11-21T10:00:00+09:00")[0])
        revoked = dict(self.approval, state="取消")
        self.assertFalse(self.check(approval=revoked)[0])

    def test_rejects_expired_content_mismatch_and_expiry_mismatch(self):
        self.assertFalse(verify(self.request, lambda _id: self.approval, self.store,
                                "2026-11-21T10:10:00+09:00")[0])
        changed = dict(self.request, payload={"text": "書き換え"})
        self.assertFalse(self.check(request=changed)[0])
        changed_expiry = dict(self.approval, expires_at="2026-11-21T10:09:00+09:00")
        self.assertFalse(self.check(approval=changed_expiry)[0])

    def test_store_contains_both_ids(self):
        self.check()
        with open(self.store, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data, {"op_ids": ["op-1"], "approval_ids": ["approval-1"]})


if __name__ == "__main__":
    unittest.main()
