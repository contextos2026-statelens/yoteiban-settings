import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import tempfile, unittest
from yoteiban_core.ledger import Ledger
from yoteiban_core.search import search
from yoteiban_core.retention import purge, stats


class SearchRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(os.path.join(self.tmp.name, "db.sqlite"))
        for i in range(100):
            text = f"友達と土曜に会う予定、{i}番目の歯医者予約"
            if i == 0:
                text += " AB"
            input_id, _ = self.ledger.ingest({"source": "mail", "native_ref": f"message-{i}",
                "received_at": f"2026-11-{(i % 28) + 1:02d}T09:00:00+09:00", "body_text": text,
                "body_masked": text, "summary_masked": f"予定 {i}"})
            self.ledger.conn.execute("UPDATE raw_inputs SET retention_until=? WHERE input_id=?", ("2026-11-20T00:00:00+09:00" if i in (0, 1) else "2026-12-20T00:00:00+09:00", input_id))
            if i == 0:
                self.ledger.upsert_case({"case_id": "case-0", "kind": "予定", "title": "歯医者", "cause_input_id": input_id}, now="2026-11-18T10:00:00+09:00")
        self.ledger.conn.execute("UPDATE raw_inputs SET summary_masked='' WHERE native_ref='message-1'")
    def tearDown(self):
        self.ledger.close(); self.tmp.cleanup()
    def test_search_japanese_and_short_query(self):
        self.assertEqual(len(search(self.ledger, "友達と", limit=200)), 100)
        self.assertEqual(len(search(self.ledger, "土曜", limit=200)), 100)
        self.assertEqual(len(search(self.ledger, "歯医者", limit=200)), 100)
        self.assertEqual(len(search(self.ledger, "AB")), 1)
        row = search(self.ledger, "歯医者", case_id="case-0")[0]
        self.assertEqual(row["case_ids"], ["case-0"])
        self.assertLessEqual(len(row["snippet"]), len("友達と土曜に会う予定、0番目の歯医者予約") + 40)
    def test_search_filters(self):
        results = search(self.ledger, "歯医者", since="2026-11-05", until="2026-11-10", sender_role="不明", limit=20)
        self.assertTrue(all("2026-11-" in r["received_at"] for r in results))
        self.assertLessEqual(len(results), 20)
    def test_retention_deadline_summary_and_repeat(self):
        before = purge(self.ledger, "2026-11-19T23:59:59+09:00")
        self.assertEqual(before["count"], 0)
        at_deadline = purge(self.ledger, "2026-11-20T00:00:00+09:00")
        self.assertEqual(at_deadline["purged"], [self.ledger.conn.execute("select input_id from raw_inputs where native_ref='message-0'").fetchone()[0]])
        self.assertEqual(len(at_deadline["needs_summary"]), 1)
        row = self.ledger.conn.execute("select body_text,body_masked,summary_masked,native_ref,media_ref from raw_inputs where native_ref='message-0'").fetchone()
        self.assertEqual(tuple(row), ("", "", "予定 0", "message-0", None))
        expired_id = self.ledger.conn.execute("select input_id from raw_inputs where native_ref='message-0'").fetchone()[0]
        self.assertNotIn(expired_id, [hit["input_id"] for hit in search(self.ledger, "歯医者", limit=200)])
        again = purge(self.ledger, "2026-11-21T00:00:00+09:00")
        self.assertEqual(again["count"], 0)
        self.assertEqual(len(again["needs_summary"]), 1)
    def test_stats(self):
        result = stats(self.ledger)
        self.assertEqual(result["raw_inputs"], 100)
        self.assertEqual(result["cases"], 1)
        self.assertGreater(result["db_bytes"], 0)
if __name__ == "__main__": unittest.main()
