import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "core"))

from yoteiban_core.ledger import Ledger
from yoteiban_core.reminder_cleanup import deletion_matches, plan, record_completed


class ReminderCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(os.path.join(self.tmp.name, "ledger.sqlite"))
        self.now = "2026-11-23T12:00:00+09:00"
        self.ledger.upsert_case({"case_id": "event", "kind": "予定", "title": "用事",
                                 "status": "決まった", "event_end": "2026-11-24T05:00:00+09:00",
                                 "extra": '{"movement":true,"move_status":{"signals":[{"type":"到着"}]}}'}, now=self.now)
        self.ledger.upsert_case({"case_id": "elapsed-event", "kind": "予定", "title": "終了済み",
                                 "status": "決まった", "event_end": "2026-11-23T05:00:00+09:00"}, now=self.now)
        self.ledger.upsert_case({"case_id": "cancelled", "kind": "予定", "title": "中止",
                                 "status": "取消", "event_end": "2026-11-30T10:00:00+09:00"}, now=self.now)
        self.add("arrive", "event", "到着", list_name="予定ばん")
        self.add("elapsed", "elapsed-event", "時刻", list_name="予定ばん")
        self.add("cancel", "cancelled", "出発", list_name="予定ばん")

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def add(self, rid, case, kind, title=None, list_name="個人"):
        self.ledger.conn.execute("INSERT INTO reminder_cleanup(reminder_id,list_name,case_id,kind,title,created_at) VALUES(?,?,?,?,?,?)",
                                 (rid, list_name, case, kind, title or f"title-{rid}", self.now))
        self.ledger.conn.commit()

    def item(self, rid, title=None, list_name="予定ばん", completed=False):
        row = self.ledger.conn.execute("SELECT title FROM reminder_cleanup WHERE reminder_id=?", (rid,)).fetchone()
        return {"id": rid, "title": title or (row[0] if row else f"title-{rid}"),
                "list_name": list_name, "completed": completed}

    def test_all_three_completion_conditions(self):
        # event_end + 6h and signal both apply to the first event's reminders.
        result = plan(self.ledger, {"reminder_cleanup": "完了にするだけ"}, self.now,
                      [self.item(x) for x in ("arrive", "elapsed", "cancel")])
        self.assertEqual({r["reminder_id"] for r in result["complete"]}, {"arrive", "elapsed", "cancel"})

    def test_untracked_and_non_dedicated_list_are_untouched(self):
        self.add("outside-list", "event", "時刻", list_name="個人")
        result = plan(self.ledger, {"reminder_cleanup": "完了にするだけ"}, self.now,
                      [self.item(x) for x in ("arrive", "elapsed", "cancel", "untracked")]
                      + [self.item("outside-list", list_name="個人")])
        self.assertNotIn("outside-list", {r["reminder_id"] for r in result["complete"]})

    def test_no_action_setting_changes_nothing(self):
        result = plan(self.ledger, {"reminder_cleanup": "何もしない"}, self.now, [self.item("arrive")])
        self.assertEqual(result, {"complete": [], "delete": [], "pending": 0})

    def test_deletion_requires_fresh_matching_id_and_title(self):
        record_completed(self.ledger, "arrive", "2026-11-10T10:00:00+09:00")
        row = self.item("arrive")
        self.assertFalse(deletion_matches(self.ledger, "arrive", {**row, "id": "other"}, self.now,
                                          {"move_reminder_list": "予定ばん"}))

    def test_delete_after_seven_days_from_completed_at(self):
        self.ledger.conn.execute("UPDATE reminder_cleanup SET created_at=? WHERE reminder_id='arrive'",
                                 ("2026-11-10T10:00:00+09:00",))
        self.ledger.conn.commit()
        record_completed(self.ledger, "arrive", "2026-11-10T10:00:00+09:00")
        result = plan(self.ledger, {"reminder_cleanup": "片づける（完了→7日後に削除）"}, self.now,
                      [self.item("arrive", completed=True)])
        self.assertEqual([r["reminder_id"] for r in result["delete"]], ["arrive"])
        self.assertTrue(deletion_matches(
            self.ledger, "arrive", self.item("arrive", completed=True), self.now,
            {"reminder_cleanup": "片づける（完了→7日後に削除）"}))

    def test_under_seven_days_after_completion_is_not_deleted(self):
        record_completed(self.ledger, "arrive", "2026-11-20T12:00:00+09:00")
        result = plan(self.ledger, {"reminder_cleanup": "片づける（完了→7日後に削除）"}, self.now,
                      [self.item("arrive", completed=True)])
        self.assertEqual(result["delete"], [])

    def test_created_under_seven_days_ago_is_not_deleted(self):
        self.ledger.conn.execute("UPDATE reminder_cleanup SET created_at=? WHERE reminder_id='arrive'",
                                 ("2026-11-20T12:00:00+09:00",))
        self.ledger.conn.commit()
        record_completed(self.ledger, "arrive", "2026-11-10T10:00:00+09:00")
        result = plan(self.ledger, {"reminder_cleanup": "片づける（完了→7日後に削除）"}, self.now,
                      [self.item("arrive", completed=True)])
        self.assertEqual(result["delete"], [])

    def test_complete_only_setting_never_deletes(self):
        record_completed(self.ledger, "arrive", "2026-11-10T10:00:00+09:00")
        result = plan(self.ledger, {"reminder_cleanup": "完了にするだけ"}, self.now,
                      [self.item("arrive", completed=True)])
        self.assertEqual(result["delete"], [])
        self.assertFalse(deletion_matches(self.ledger, "arrive", self.item("arrive", completed=True), self.now,
                                          {"reminder_cleanup": "完了にするだけ"}))

    def test_title_or_list_mismatch_is_retained(self):
        self.ledger.conn.execute("UPDATE reminder_cleanup SET created_at=? WHERE reminder_id='arrive'",
                                 ("2026-11-10T10:00:00+09:00",))
        self.ledger.conn.commit()
        record_completed(self.ledger, "arrive", "2026-11-10T10:00:00+09:00")
        settings = {"move_reminder_list": "予定ばん", "reminder_cleanup": "片づける（完了→7日後に削除）"}
        self.assertFalse(deletion_matches(self.ledger, "arrive", {**self.item("arrive"), "title": "変更済み"},
                                          self.now, settings))
        self.assertIsNone(self.ledger.conn.execute(
            "SELECT deleted_at FROM reminder_cleanup WHERE reminder_id='arrive'").fetchone()[0])
        # Use another row because a detected mismatch is deliberately sticky.
        self.add("list-mismatch", "event", "時刻", list_name="予定ばん")
        self.ledger.conn.execute("UPDATE reminder_cleanup SET created_at=? WHERE reminder_id='list-mismatch'",
                                 ("2026-11-10T10:00:00+09:00",))
        self.ledger.conn.commit()
        record_completed(self.ledger, "list-mismatch", "2026-11-10T10:00:00+09:00")
        self.assertFalse(deletion_matches(self.ledger, "list-mismatch",
                                          self.item("list-mismatch", list_name="別リスト"), self.now, settings))

    def test_configured_dedicated_list_is_the_only_other_accepted_list(self):
        self.add("configured", "elapsed-event", "時刻", list_name="架空専用リスト")
        self.add("other", "event", "時刻", list_name="個人")
        result = plan(self.ledger, {"reminder_cleanup": "完了にするだけ",
                                    "move_reminder_list": "架空専用リスト"}, self.now,
                      [self.item("configured", list_name="架空専用リスト"), self.item("arrive"),
                       self.item("other", list_name="個人")])
        self.assertEqual([r["reminder_id"] for r in result["complete"]], ["configured"])

    def test_ledger_never_has_location_data(self):
        columns = {r[1] for r in self.ledger.conn.execute("PRAGMA table_info(reminder_cleanup)")}
        self.assertFalse(columns.intersection({"location", "address", "latitude", "longitude", "coordinates"}))


if __name__ == "__main__":
    unittest.main()
