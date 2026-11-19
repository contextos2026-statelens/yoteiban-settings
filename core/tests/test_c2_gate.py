import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import hashlib
import json
import os as _os
import tempfile
import unittest

from yoteiban_core.gate import check, marker, payload_hash, reconcile, result
from yoteiban_core.ledger import Ledger


NOW = "2026-11-18T10:00:00+09:00"


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(_os.path.join(self.tmp.name, "gate.sqlite"))
        self.ledger.upsert_case({"case_id": "case-a", "kind": "予定", "title": "行事", "status": "決まった"}, now=NOW)
        self.cfg = {"db_path": "unused", "cal_personal_id": "calendar-personal",
                    "cal_draft_id": "calendar-draft", "cal_readonly_ids": ["calendar-household"],
                    "reminder_list_id": "reminder-dedicated", "reminder_readonly_list_ids": ["reminder-shared"],
                    "notion_db_ids": ["notion-db"]}

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def op(self, kind="cal_personal", target="calendar-personal", payload=None, **extra):
        value = {"kind": kind, "target_id": target, "case_id": "case-a",
                 "payload": payload if payload is not None else {"title": "行事", "event_start": "2026-11-21T10:00:00+09:00"}}
        value.update(extra)
        return value

    def approve(self, kind, payload, minutes=10):
        return self.ledger.grant_approval(kind, payload_hash(payload), minutes, "確認", now=NOW)

    def test_configured_targets_and_draft_kind(self):
        home = check(self.ledger, self.cfg, self.op(target="calendar-household"), NOW)
        self.assertFalse(home["allow"])
        bad = check(self.ledger, self.cfg, self.op(target="unconfigured"), NOW)
        self.assertFalse(bad["allow"])
        # 下書きカレンダーは作らない（2026-11-19）。どの下書きの種類でも拒否する。
        for draft_kind in ("確定", "準備", "候補"):
            denied = check(self.ledger, self.cfg, self.op("cal_draft", "calendar-draft", draft_kind=draft_kind), NOW)
            self.assertFalse(denied["allow"])
            self.assertIn("下書き", denied["reason"])
        self.assertEqual(self.ledger.list_ops(), [])

    def test_approval_is_required_matches_content_and_is_consumed(self):
        payload = {"title": "行事", "event_start": "2026-11-21T10:00:00+09:00"}
        approval = self.approve("cal_personal", payload)
        op = self.op(payload=payload, approval_id=approval)
        allowed = check(self.ledger, self.cfg, op, NOW)
        self.assertTrue(allowed["allow"], allowed)
        self.assertEqual(self.ledger.get_approval(approval)["state"], "使用済み")
        self.assertEqual(self.ledger.get_op(allowed["op_id"])["state"], "実行中")
        result(self.ledger, allowed["op_id"], "ok", True, NOW)
        denied = check(self.ledger, self.cfg, op, NOW)
        self.assertFalse(denied["allow"])
        self.assertEqual(denied["reason"], "成功済み")

        wrong = self.approve("cal_personal", {"title": "別の行事"})
        self.assertFalse(check(self.ledger, self.cfg, self.op(payload=payload, approval_id=wrong), NOW)["allow"])

    def test_expired_approval_is_rejected(self):
        payload = {"title": "期限"}
        approval = self.ledger.grant_approval("cal_personal", payload_hash(payload), 1, now=NOW)
        later = "2026-11-18T10:01:00+09:00"
        self.assertFalse(check(self.ledger, self.cfg, self.op(payload=payload, approval_id=approval), later)["allow"])

    def test_notion_and_send_permissions(self):
        notion = check(self.ledger, self.cfg, self.op("notion", "notion-db", {"status": "確認中"}), NOW)
        self.assertTrue(notion["allow"])
        self.assertFalse(check(self.ledger, self.cfg, self.op("notion", "unknown-db", {}), NOW)["allow"])
        payload = {"recipient": "友人A", "text": "予定を確認してください"}
        approval = self.approve("send", payload)
        sent = check(self.ledger, self.cfg, self.op("send", "友人A", payload, approval_id=approval), NOW)
        self.assertTrue(sent["allow"], sent)
        wrong_target = check(self.ledger, self.cfg, self.op("send", "友人B", payload, approval_id=approval), NOW)
        self.assertFalse(wrong_target["allow"])
        self.assertEqual(result(self.ledger, sent["op_id"], "timeout", None, NOW)["state"], "実行結果不明（要照合）")
        self.assertEqual(self.ledger.get_op(sent["op_id"])["handoff_state"], "渡し済み（未確認）")
        self.assertEqual(reconcile(self.ledger, sent["op_id"], True, NOW)["state"], "成功（読み戻し済み）")

    def test_reminder_needs_successful_personal_calendar_write(self):
        reminder = self.op("reminder", "reminder-dedicated", {"title": "行事"}, linked_op_id="missing")
        self.assertFalse(check(self.ledger, self.cfg, reminder, NOW)["allow"])
        payload = {"title": "行事", "event_start": "2026-11-21T10:00:00+09:00"}
        approval = self.approve("cal_personal", payload)
        calendar = check(self.ledger, self.cfg, self.op(payload=payload, approval_id=approval), NOW)
        result(self.ledger, calendar["op_id"], "ok", True, NOW)
        reminder["linked_op_id"] = calendar["op_id"]
        self.assertTrue(check(self.ledger, self.cfg, reminder, NOW)["allow"])
        shared = self.op("reminder", "reminder-shared", {"title": "行事"}, linked_op_id=calendar["op_id"])
        self.assertFalse(check(self.ledger, self.cfg, shared, NOW)["allow"])

    def test_active_ledger_delegation_is_scoped_and_counted(self):
        scope = {"period": {"from": "2026-11-18T09:00:00+09:00", "to": "2026-11-18T11:00:00+09:00"},
                 "max_count": 1, "recipients": ["友人A"], "template_id": "send-template",
                 "stop_how": "停止は本人に伝える", "kind": "send"}
        approval = self.ledger.grant_approval("send", "delegation-approval", 60, now=NOW)
        self.ledger.conn.execute("INSERT INTO delegations(del_id,scope,state,approved_by_approval_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                                 ("del-a", json.dumps(scope, ensure_ascii=False), "有効", approval, NOW, NOW))
        self.ledger.conn.execute("INSERT INTO templates(template_id,name,body,slots,state) VALUES(?,?,?,?,?)",
                                 ("send-template", "確認文", "こんにちは、{name}さん。", json.dumps({"name": {"kind": "人の呼び名"}}, ensure_ascii=False), "有効"))
        delegated_op = {"kind": "send", "target_id": "友人A", "case_id": "case-a", "template_id": "send-template",
                        "payload": {"recipient": "友人A", "body": "こんにちは、Aさん。"}, "delegation": "del-a"}
        allowed = check(self.ledger, self.cfg, delegated_op, NOW)
        self.assertTrue(allowed["allow"], allowed)
        self.assertEqual(self.ledger.get_op(allowed["op_id"])["state"], "実行中")
        self.assertEqual(self.ledger.get_op(allowed["op_id"])["result"]["delegation_id"], "del-a")
        result(self.ledger, allowed["op_id"], "error", False, NOW)
        changed_value = dict(delegated_op, payload={"recipient": "友人A", "body": "こんにちは、Bさん。"})
        self.assertFalse(check(self.ledger, self.cfg, changed_value, NOW)["allow"])
        changed_template = dict(delegated_op, payload={"recipient": "友人A", "body": "違う文面です"})
        self.assertFalse(check(self.ledger, self.cfg, changed_template, NOW)["allow"])
        self.assertFalse(check(self.ledger, self.cfg, dict(delegated_op, delegation={"del_id": "fake", "state": "有効", "scope": scope}), NOW)["allow"])

    def test_marker_matches_required_format(self):
        self.assertEqual(marker("abc123"), "yoteiban-op:abc123")


if __name__ == "__main__":
    unittest.main()
