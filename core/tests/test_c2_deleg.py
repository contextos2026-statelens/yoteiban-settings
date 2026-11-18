import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import unittest
from yoteiban_core.deleg import fill_template, in_scope, transition, validate_delegation, validate_share_grant


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.d = {"state": "下書き", "scope": {
            "period": {"from": "2026-11-01T00:00:00+09:00", "to": "2026-12-01T00:00:00+09:00"},
            "max_count": 3, "recipients": ["友人A"], "template_id": "予定確認",
            "stop_how": "本人に知らせる", "kind": "send"}}

    def test_required_fields(self):
        self.assertEqual(validate_delegation(self.d), [])
        self.assertGreaterEqual(len(validate_delegation({"scope": {"period": {}, "max_count": -1}})), 5)

    def test_transitions_and_approval(self):
        with self.assertRaises(ValueError):
            transition(self.d, "有効", now="2026-11-18T10:00:00+09:00")
        with self.assertRaises(ValueError):
            transition(self.d, "本人の確認待ち")
        d = transition(self.d, "影の運用中", now="2026-11-18T10:00:00+09:00")
        d = transition(d, "本人の確認待ち", now="2026-11-18T10:00:00+09:00")
        d = transition(d, "有効", approval_id="approval-x", now="2026-11-18T10:00:00+09:00")
        self.assertEqual(d["approval_id"], "approval-x")
        self.assertEqual(transition(d, "取消")["state"], "取消")

    def test_scope_and_placeholder_only_edits(self):
        d = dict(self.d, state="有効")
        op = {"kind": "send", "recipient": "友人A", "template_id": "予定確認",
              "approved_body": "{name}さん、{when}に会いましょう。",
              "body": "花子さん、2026-11-20T10:00:00+09:00に会いましょう。"}
        self.assertTrue(in_scope(d, op, "2026-11-18T10:00:00+09:00", 0)[0])
        self.assertFalse(in_scope(d, dict(op, recipient="友人B"), "2026-11-18T10:00:00+09:00", 0)[0])
        self.assertFalse(in_scope(d, op, "2026-12-02T10:00:00+09:00", 0)[0])
        self.assertFalse(in_scope(d, op, "2026-11-18T10:00:00+09:00", 3)[0])
        self.assertFalse(in_scope(d, dict(op, body="別の文章に変えました"), "2026-11-18T10:00:00+09:00", 0)[0])

    def test_share_is_not_send_authority(self):
        grant = {"recipient": "学校X", "info_kinds": ["予定"], "purpose": "連絡",
                 "period": self.d["scope"]["period"]}
        self.assertEqual(validate_share_grant(grant), [])
        self.assertTrue(any("送信" in e for e in validate_share_grant(dict(grant, send_allowed=True))))
        self.assertTrue(validate_share_grant({"recipient": "学校X"}))

    def test_template_slot_types(self):
        template = {"body": "{when}に{name}さんと{place}で会います。", "fields": {
            "when": {"kind": "日時"}, "name": {"kind": "人の呼び名"},
            "place": {"kind": "場所の種類", "allowed": ["図書館", "公園"]}}}
        vals = {"when": "2026-11-20T10:00:00+09:00", "name": "友人A", "place": "公園"}
        self.assertEqual(fill_template(template, vals), "2026-11-20T10:00:00+09:00に友人Aさんと公園で会います。")
        with self.assertRaises(ValueError):
            fill_template(template, dict(vals, when="明日"))
        with self.assertRaises(ValueError):
            fill_template(template, dict(vals, place="自宅住所"))


if __name__ == "__main__":
    unittest.main()
