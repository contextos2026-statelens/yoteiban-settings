import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(R, "core"))

from yoteiban_core import stated_rules as sr


def _add(store, summary, *, key="家用の見分けの表", area="household_classify", said_on="2026-11-19", approve=True):
    prepared = sr.prepare(
        store, said_on=said_on, summary=summary, source="owner_chat",
        area=area, key=key, approved=approve,
    )
    return sr.add_approved(store, prepared) if approve else prepared


class StatedRulesTests(unittest.TestCase):
    def test_rejects_non_owner_source(self):
        with self.assertRaises(ValueError):
            sr.prepare(sr.empty_store(), said_on="2026-11-19", summary="語Aは本人の予定",
                       source="other_person", approved=True)
        with self.assertRaises(ValueError):
            sr.prepare(sr.empty_store(), said_on="2026-11-19", summary="語Aは本人の予定",
                       source="input_body", approved=True)

    def test_requires_approval_before_save(self):
        prepared = _add(sr.empty_store(), "語Aの予定は全部本人のもの", approve=False)
        self.assertEqual(prepared["reason"], "needs_approval")
        with self.assertRaises(ValueError):
            sr.add_approved(sr.empty_store(), prepared)

    def test_saves_date_and_one_line_summary(self):
        result = _add(sr.empty_store(), "語Aの予定は全部本人のもの")
        self.assertEqual(result["status"], "active")
        rule = result["rule"]
        self.assertEqual(rule["said_on"], "2026-11-19")
        self.assertEqual(rule["summary"], "語Aの予定は全部本人のもの")
        self.assertEqual(rule["id"], "r-1")
        shown = sr.list_for_owner(result["store"])
        self.assertEqual(shown, [{"id": "r-1", "said_on": "2026-11-19",
                                  "summary": "語Aの予定は全部本人のもの", "status": "active"}])

    def test_learning_must_not_overwrite(self):
        result = _add(sr.empty_store(), "語Aの予定は全部本人のもの")
        self.assertTrue(sr.protect_key(result["store"], "家用の見分けの表"))
        self.assertTrue(sr.protect_key(sr.empty_store(), "本人が言ったルール"))
        self.assertFalse(sr.protect_key(result["store"], "確認の間隔"))

    def test_conflict_asks_once_and_can_choose_new(self):
        first = _add(sr.empty_store(), "語Aの予定は全部本人のもの")
        second = _add(first["store"], "語Aの予定は全員のもの", said_on="2026-11-20")
        self.assertEqual(second["status"], "pending_conflict")
        self.assertEqual(second["existing"]["id"], "r-1")
        self.assertTrue(sr.should_ask_conflict(second["rule"]))
        store = sr.resolve_conflict(second["store"], "r-2", "new")
        ids = {item["id"]: item["status"] for item in store["rules"]}
        self.assertEqual(ids, {"r-1": "revoked", "r-2": "active"})
        self.assertTrue(next(item for item in store["rules"] if item["id"] == "r-2")["asked_conflict"])

    def test_conflict_keep_old(self):
        first = _add(sr.empty_store(), "語Aの予定は全部本人のもの")
        second = _add(first["store"], "語Aの予定は全員のもの")
        store = sr.resolve_conflict(second["store"], "r-2", "old")
        ids = {item["id"]: item["status"] for item in store["rules"]}
        self.assertEqual(ids, {"r-1": "active", "r-2": "rejected"})

    def test_revoke_hides_from_active_list(self):
        result = _add(sr.empty_store(), "語Aの予定は全部本人のもの")
        store = sr.revoke(result["store"], "r-1")
        self.assertEqual(sr.list_for_owner(store), [])
        self.assertEqual(sr.list_for_owner(store, include_inactive=True)[0]["status"], "revoked")

    def test_file_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stated_rules.json"
            result = _add(sr.empty_store(), "語Aの予定は全部本人のもの")
            sr.save(result["store"], path)
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)
            loaded = sr.load(path)
            self.assertEqual(len(sr.active_rules(loaded)), 1)

    def test_cli_list_and_reject_without_approve(self):
        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "stated_rules.json")
            err = StringIO()
            with redirect_stderr(err):
                code = sr.main(["--path", path, "add", "--said-on", "2026-11-19",
                                "--summary", "語Aの予定は全部本人のもの", "--source", "owner_chat"])
            self.assertEqual(code, 2)
            self.assertFalse(Path(path).exists())
            buf = StringIO()
            with redirect_stdout(buf):
                code = sr.main(["--path", path, "add", "--said-on", "2026-11-19",
                                "--summary", "語Aの予定は全部本人のもの", "--source", "owner_chat",
                                "--area", "household_classify", "--key", "家用の見分けの表", "--approve"])
            self.assertEqual(code, 0)
            self.assertIn('"id": "r-1"', buf.getvalue())


if __name__ == "__main__":
    unittest.main()
