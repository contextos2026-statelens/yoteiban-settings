import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import json
import hashlib
import os as _os
import tempfile
import unittest

from yoteiban_core.audit import audit
from yoteiban_core.ledger import Ledger


NOW = "2026-11-19T10:00:00+09:00"


def marker(op_id):
    return f"yoteiban-op:{op_id}"


def payload_hash(payload):
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(_os.path.join(self.tmp.name, "audit.sqlite"))
        self.ledger.upsert_case({"case_id": "case-a", "kind": "予定", "title": "学校X行事", "status": "確認中"}, now="2026-11-17T10:00:00+09:00")
        self.cfg = {"cal_draft_id": "draft-cal", "cal_personal_id": "personal-cal",
                    "cal_readonly_ids": ["household-cal"], "reminder_list_id": "dedicated-list",
                    "reminder_readonly_list_ids": ["shared-list"], "notion_db_ids": ["notion-db"]}

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def kinds(self, snapshot=None):
        return {item["type"] for item in audit(self.ledger, self.cfg, snapshot or {}, NOW)}

    def make_op(self, kind="cal_draft", now="2026-11-17T08:00:00+09:00", state=None, payload=None):
        digest = payload_hash(payload or {"title": "架空"})
        op_id, _ = self.ledger.reserve_op(kind, "draft-cal" if kind == "cal_draft" else "notion-db",
                                          "case-a", digest, now=now)
        if state in {"実行中", "成功（読み戻し済み）", "実行結果不明（要照合）", "失敗"}:
            self.ledger.set_op_state(op_id, "実行中", now=now)
        if state and state != "予約" and state != "実行中":
            self.ledger.set_op_state(op_id, state, result={"ok": True}, now=now)
        return op_id

    def test_unmarked_items_and_household_marker_are_found(self):
        snapshot = {"calendar": [
            {"id": "event-a", "calendar_id": "personal-cal", "start": NOW, "title": "架空予定", "marker": None},
            {"id": "event-b", "calendar_id": "household-cal", "start": NOW, "title": "架空予定", "marker": "yoteiban-op:unknown"}],
            "reminders": [{"id": "rem-a", "list_id": "dedicated-list", "title": "用事", "due": NOW, "completed": False, "marker": None}]}
        types = self.kinds(snapshot)
        self.assertIn("印の無い書き込み", types)
        self.assertIn("家用カレンダーへの書き込み", types)

    def test_successful_write_missing_item_is_found_and_present_marker_is_clean(self):
        op_id = self.make_op(state="成功（読み戻し済み）")
        notion_id = self.make_op(kind="notion", state="成功（読み戻し済み）", payload={"status": "確認中"})
        missing = audit(self.ledger, self.cfg, {}, NOW)
        self.assertIn("成功した書き込みが見つからない", {item["type"] for item in missing})
        self.assertTrue(any(notion_id in item["refs"] for item in missing if item["type"] == "成功した書き込みが見つからない"))
        snapshot = {"calendar": [{"id": "ev-1", "calendar_id": "draft-cal", "start": NOW,
                                  "title": "候補", "marker": marker(op_id)}],
                    "notion": [{"case_id": "case-a", "status": "確認中", "edited_by_user": False}]}
        findings = audit(self.ledger, self.cfg, snapshot, NOW)
        self.assertNotIn("成功した書き込みが見つからない", {item["type"] for item in findings})

    def test_unknown_operation_only_flags_after_24_hours(self):
        old = self.make_op(now="2026-11-18T00:00:00+09:00", state="実行結果不明（要照合）")
        fresh = self.make_op(now="2026-11-19T09:00:00+09:00", state="実行結果不明（要照合）", payload={"title": "別件"})
        findings = audit(self.ledger, self.cfg, {}, NOW)
        refs = [f["refs"][0] for f in findings if f["type"] == "24時間を過ぎた要照合"]
        self.assertIn(old, refs)
        self.assertNotIn(fresh, refs)

    def test_expired_active_approval_is_found(self):
        expired = self.ledger.grant_approval("send", "fp-old", 1, now="2026-11-18T00:00:00+09:00")
        valid = self.ledger.grant_approval("send", "fp-valid", 60, now="2026-11-19T09:30:00+09:00")
        findings = audit(self.ledger, self.cfg, {}, NOW)
        refs = [f["refs"][0] for f in findings if f["type"] == "期限切れ承認が有効のまま"]
        self.assertIn(expired, refs)
        self.assertNotIn(valid, refs)

    def test_stale_case_and_reserved_operation_are_found(self):
        self.ledger.upsert_case({"case_id": "case-stale", "kind": "締切", "title": "提出", "next_review_at": "2026-11-18T10:00:00+09:00"}, now="2026-11-18T09:00:00+09:00")
        self.ledger.upsert_case({"case_id": "case-active", "kind": "予定", "title": "更新済み", "next_review_at": "2026-11-18T10:00:00+09:00"}, now="2026-11-19T09:00:00+09:00")
        self.make_op(now="2026-11-18T08:00:00+09:00", state="予約")
        findings = audit(self.ledger, self.cfg, {}, NOW)
        refs = [f["refs"][0] for f in findings if f["type"] == "確認時刻を過ぎた案件"]
        self.assertIn("case-stale", refs)
        self.assertNotIn("case-active", refs)
        self.assertIn("予約のまま止まった操作", {f["type"] for f in findings})

    def test_notion_mismatch_and_user_edit_have_distinct_types(self):
        self.ledger.upsert_case({"case_id": "case-b", "kind": "予定", "title": "会合", "status": "未確定"}, now=NOW)
        snapshot = {"notion": [
            {"case_id": "case-a", "status": "済んだ", "edited_by_user": False},
            {"case_id": "case-b", "status": "確認中", "edited_by_user": True}]}
        findings = audit(self.ledger, self.cfg, snapshot, NOW)
        kinds_by_ref = {(f["type"], f["refs"][0]) for f in findings}
        self.assertIn(("Notion状態ずれ", "case-a"), kinds_by_ref)
        self.assertIn(("Notion本人の直し", "case-b"), kinds_by_ref)
        self.assertNotIn(("Notion状態ずれ", "case-b"), kinds_by_ref)

    def test_mirror_check_summary_and_clean_snapshot(self):
        self.ledger.upsert_case({"case_id": "case-clean", "kind": "予定", "title": "正常", "status": "確認中"}, now=NOW)
        op_id = self.make_op(state="成功（読み戻し済み）")
        snapshot = {"calendar": [{"id": "ev-clean", "calendar_id": "draft-cal", "start": NOW,
                                  "title": "正常", "marker": marker(op_id)}],
                    "notion": [{"case_id": "case-clean", "status": "確認中", "edited_by_user": False}]}
        findings = audit(self.ledger, self.cfg, snapshot, NOW)
        self.assertFalse(findings)
        row = self.ledger.conn.execute("SELECT diff FROM mirror_checks WHERE target='nightly'").fetchone()
        self.assertEqual(json.loads(row[0]), {"count": 0, "by_type": {}, "refs": []})


if __name__ == "__main__":
    unittest.main()
