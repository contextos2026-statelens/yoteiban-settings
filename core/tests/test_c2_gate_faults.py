import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import json
import os as _os
import tempfile
import unittest

from yoteiban_core.gate import check, payload_hash, reconcile, result
from yoteiban_core.ledger import Ledger


NOW = "2026-11-18T10:00:00+09:00"


class GateFaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(_os.path.join(self.tmp.name, "faults.sqlite"))
        self.ledger.upsert_case({"case_id": "case-a", "kind": "予定", "title": "テスト", "status": "決まった"}, now=NOW)
        self.cfg = {"cal_personal_id": "p", "cal_draft_id": "d", "cal_readonly_ids": ["house"],
                    "reminder_list_id": "r", "reminder_readonly_list_ids": ["shared"], "notion_db_ids": ["n"]}

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def op(self, payload, **kw):
        return {"kind": "cal_draft", "target_id": "d", "case_id": "case-a", "draft_kind": "候補", "payload": payload, **kw}

    def test_unresolved_result_blocks_a_different_operation_for_same_case_and_kind(self):
        first = check(self.ledger, self.cfg, self.op({"title": "A"}), NOW)
        self.assertTrue(first["allow"])
        result(self.ledger, first["op_id"], "timeout", None, NOW)
        second = check(self.ledger, self.cfg, self.op({"title": "B"}), NOW)
        self.assertFalse(second["allow"])
        self.assertIn("照合", second["reason"])
        self.assertEqual(self.ledger.get_op(second["op_id"])["state"], "取消")

    def test_cancelled_or_used_approval_cannot_be_reused(self):
        payload = {"body": "連絡", "recipient": "友人A"}
        approval = self.ledger.grant_approval("send", payload_hash(payload), 10, now=NOW)
        self.ledger.use_approval(approval, NOW)
        op = {"kind": "send", "target_id": "友人A", "case_id": "case-a", "payload": payload, "approval_id": approval}
        self.assertFalse(check(self.ledger, self.cfg, op, NOW)["allow"])
        self.assertEqual(self.ledger.get_approval(approval)["state"], "使用済み")

    def test_reconcile_only_accepts_unknown_operation(self):
        first = check(self.ledger, self.cfg, self.op({"title": "A"}), NOW)
        result(self.ledger, first["op_id"], None, None, NOW)
        self.assertTrue(reconcile(self.ledger, first["op_id"], False, NOW)["ok"])
        self.assertEqual(self.ledger.get_op(first["op_id"])["state"], "失敗")
        again = reconcile(self.ledger, first["op_id"], True, NOW)
        self.assertFalse(again["ok"])

    def test_bad_now_and_unserializable_payload_fail_closed(self):
        self.assertFalse(check(self.ledger, self.cfg, self.op({"title": "A"}), "not-a-time")["allow"])
        self.assertFalse(check(self.ledger, self.cfg, self.op({"value": object()}), NOW)["allow"])


if __name__ == "__main__":
    unittest.main()
