"""native_ref の正規化と、形が違っても同じ入力は1件になること。"""
import os
import tempfile
import unittest

from yoteiban_core.ledger import Ledger
from yoteiban_core.refs import canonicalize_source, normalize_native_ref


NOW = "2026-11-18T12:00:00+09:00"


class NormalizeTests(unittest.TestCase):
    def test_gmail_strips_brackets_case_and_space(self):
        self.assertEqual(normalize_native_ref("gmail", "  <ABC@example.com>  "), "abc@example.com")
        self.assertEqual(normalize_native_ref("mail", "<<ID@example.com>>"), "id@example.com")
        self.assertEqual(normalize_native_ref("gmail-family", "AbC@example.com"), "abc@example.com")
        self.assertEqual(normalize_native_ref("gmail", " 18c0ff  "), "18c0ff")

    def test_imessage_guid_or_rowid(self):
        self.assertEqual(normalize_native_ref("imessage", "guid:Abcd"), "guid:abcd")
        self.assertEqual(normalize_native_ref("imessage-macmini", "guid:{Abcd}"), "guid:abcd")
        self.assertEqual(normalize_native_ref("imessage", "0007"), "rowid:7")
        self.assertEqual(normalize_native_ref("imessage", "rowid:7"), "rowid:7")

    def test_calendar_event_and_update_mark(self):
        self.assertEqual(normalize_native_ref("calendar", "abc123@2026-11-19T08:00:00Z"),
                         "event:abc123@2026-11-19T08:00:00Z")
        self.assertEqual(normalize_native_ref("calendar-personal", "event:abc123@gone"), "event:abc123@gone")
        self.assertEqual(normalize_native_ref("calendar", "abc123"), "event:abc123")

    def test_source_aliases(self):
        self.assertEqual(canonicalize_source("gmail-family"), "gmail")
        self.assertEqual(canonicalize_source("calendar:home"), "calendar")
        self.assertEqual(canonicalize_source("notion"), "notion")


class IngestDedupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(os.path.join(self.temp.name, "ledger.sqlite"))

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_same_gmail_two_shapes_is_one_row(self):
        first_id, created = self.ledger.ingest(
            {"source": "gmail", "native_ref": "<ABC@example.com>", "received_at": NOW, "body_text": "案内"}
        )
        second_id, created2 = self.ledger.ingest(
            {"source": "gmail", "native_ref": " abc@example.com ", "received_at": NOW, "body_text": "案内"}
        )
        self.assertTrue(created)
        self.assertFalse(created2)
        self.assertEqual(first_id, second_id)
        rows = self.ledger.conn.execute("SELECT native_ref FROM raw_inputs").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "abc@example.com")

    def test_existing_row_is_matched_without_rewrite(self):
        self.ledger.conn.execute(
            "INSERT INTO raw_inputs(input_id,source,native_ref,received_at) VALUES(?,?,?,?)",
            ("old-input", "gmail", "<ABC@example.com>", NOW),
        )
        self.ledger.conn.commit()
        input_id, created = self.ledger.ingest(
            {"source": "gmail", "native_ref": "abc@example.com", "received_at": NOW, "body_text": "案内"}
        )
        self.assertEqual(input_id, "old-input")
        self.assertFalse(created)
        stored = self.ledger.conn.execute(
            "SELECT native_ref FROM raw_inputs WHERE input_id='old-input'"
        ).fetchone()[0]
        self.assertEqual(stored, "<ABC@example.com>")

    def test_imessage_and_calendar_shapes_dedup(self):
        a, c1 = self.ledger.ingest({"source": "imessage", "native_ref": "12", "received_at": NOW})
        b, c2 = self.ledger.ingest({"source": "imessage", "native_ref": "rowid:12", "received_at": NOW})
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(a, b)
        x, k1 = self.ledger.ingest(
            {"source": "calendar", "native_ref": "eid@2026-11-19T08:00:00Z", "received_at": NOW}
        )
        y, k2 = self.ledger.ingest(
            {"source": "calendar-personal", "native_ref": "event:eid@2026-11-19T08:00:00Z", "received_at": NOW}
        )
        self.assertTrue(k1)
        self.assertFalse(k2)
        self.assertEqual(x, y)
