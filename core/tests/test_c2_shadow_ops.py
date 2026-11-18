import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import json
import os as _os
import tempfile
import unittest

from yoteiban_core.ledger import Ledger
from yoteiban_core.shadow_ops import guard, record, report


NOW = "2026-11-18T10:00:00+09:00"


class ShadowOpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(_os.path.join(self.tmp.name, "shadow.sqlite"))
        self.scope = {"period": {"from": "2026-11-18T00:00:00+09:00", "to": "2026-12-18T00:00:00+09:00"},
                      "max_count": 10, "recipients": ["友人A"], "template_id": "template-a",
                      "stop_how": "本人に知らせる", "kind": "send"}
        self.ledger.conn.execute("INSERT INTO delegations(del_id,scope,state,created_at,updated_at) VALUES(?,?,?,?,?)",
                                 ("del-a", json.dumps(self.scope, ensure_ascii=False), "影の運用中", NOW, NOW))
        self.ledger.conn.execute("INSERT INTO templates(template_id,name,body,slots,state) VALUES(?,?,?,?,?)",
                                 ("template-a", "確認文", "{name}さん、{when}に会いましょう。",
                                  json.dumps({"name": {"kind": "人"}, "when": {"kind": "日時"}}, ensure_ascii=False), "有効"))

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_records_match_only_when_template_slot_values_match(self):
        proposed = {"body": "友人Aさん、2026-11-20T10:00:00+09:00に会いましょう。"}
        same = record(self.ledger, "del-a", proposed, {"body": "友人Aさん、2026-11-20T10:00:00+09:00に会いましょう。"}, NOW)
        changed = record(self.ledger, "del-a", proposed, {"body": "友人Bさん、2026-11-20T10:00:00+09:00に会いましょう。"}, NOW)
        self.assertEqual(same["match"], 1)
        self.assertEqual(changed["match"], 0)
        self.assertEqual(self.ledger.conn.execute("SELECT count(*) FROM shadow_records WHERE del_id='del-a'").fetchone()[0], 2)

    def test_report_counts_edits_and_only_sets_candidate(self):
        record(self.ledger, "del-a", {"values": {"name": "友人A", "when": "明日"}},
               {"values": {"name": "友人B", "when": "明日"}}, NOW)
        record(self.ledger, "del-a", {"values": {"name": "友人A", "when": "明日"}},
               {"values": {"name": "友人A", "when": "明日"}}, NOW)
        value = report(self.ledger, "del-a", {"shadow_pass": {"n": 2, "max_edits": 1}})
        self.assertEqual(value["n"], 2)
        self.assertEqual(value["match_rate"], 0.5)
        self.assertEqual(len(value["edits"]), 1)
        self.assertTrue(value["candidate"])
        self.assertFalse(report(self.ledger, "del-a", {"shadow_pass": {"n": 2, "max_edits": 0}})["candidate"])
        self.assertEqual(self.ledger.conn.execute("SELECT state FROM delegations WHERE del_id='del-a'").fetchone()[0], "影の運用中")
        self.assertFalse(report(self.ledger, "del-a", {"shadow_pass": {"n": 3, "max_edits": 1}})["candidate"])

    def test_report_redacts_values_from_edit_summaries(self):
        record(self.ledger, "del-a", {"values": {"name": "友人A"}}, {"values": {"name": "友人B"}}, NOW)
        value = report(self.ledger, "del-a", {"shadow_pass": {"n": 5, "max_edits": 1}})
        self.assertNotIn("友人A", json.dumps(value, ensure_ascii=False))
        self.assertNotIn("友人B", json.dumps(value, ensure_ascii=False))

    def test_guard_refuses_shadow_operations_only(self):
        blocked = guard({"kind": "send", "delegation": {"state": "影の運用中"}})
        self.assertFalse(blocked["allow"])
        self.assertFalse(guard({"state": "影の運用中"})["allow"])
        self.assertTrue(guard({"kind": "send", "delegation": {"state": "有効"}})["allow"])
        self.assertFalse(guard(None)["allow"])

    def test_records_require_shadow_state_and_valid_time(self):
        self.ledger.conn.execute("UPDATE delegations SET state='有効' WHERE del_id='del-a'")
        with self.assertRaises(ValueError):
            record(self.ledger, "del-a", {"values": {}}, {"values": {}}, NOW)
        self.assertFalse(report(self.ledger, "del-a", {"shadow_pass": {"n": 0, "max_edits": -1}})["candidate"])


if __name__ == "__main__":
    unittest.main()
