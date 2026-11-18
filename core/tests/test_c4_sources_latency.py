import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import json
import tempfile
import unittest
from datetime import datetime

from yoteiban_core.ledger import Ledger
from yoteiban_core import sources, latency_rec


class SourceLatencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(os.path.join(self.tmp.name, "ledger.sqlite"))

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_source_register_seen_stale_and_summary(self):
        sources.register(self.ledger, "source-mail", "mail", "reader-a", True)
        sources.register(self.ledger, "source-cal", "calendar", lambda: None, True)
        self.assertEqual(self.ledger.conn.execute("select status from sources where source_id='source-mail'").fetchone()[0], "未接続")
        self.assertEqual(sources.seen(self.ledger, "source-mail", True, "2026-11-18T10:00:00+09:00"), "動いている")
        self.assertEqual(sources.seen(self.ledger, "source-cal", False, "2026-11-18T10:04:00+09:00"), "止まっている")
        old = sources.stale(self.ledger, "2026-11-18T10:06:00+09:00", {"mail": 5, "calendar": 1})
        self.assertEqual([x["source_id"] for x in old], ["source-cal", "source-mail"])
        row = self.ledger.conn.execute("select status from sources where source_id='source-mail'").fetchone()
        self.assertEqual(row[0], "止まっている")
        self.assertEqual(sources.summary_text(old),
                         "source-calの新着を確かめられていません\nsource-mailの新着を確かめられていません")

    def test_unconnected_sources_are_not_reported_stale_without_a_seen_time(self):
        sources.register(self.ledger, "source-chat", "chat", "reader", False)
        self.assertEqual(sources.stale(self.ledger, "2026-11-18T10:00:00+09:00", {"chat": 1}), [])
        row = self.ledger.conn.execute("select status,last_seen_at from sources where source_id='source-chat'").fetchone()
        self.assertEqual(tuple(row), ("未接続", None))

    def test_latency_start_end_report_median_p90_and_cost(self):
        for index, seconds in enumerate((1, 2, 3), 1):
            start_at = f"2026-11-18T10:00:0{index}+09:00"
            end_at = f"2026-11-18T10:00:{index + seconds:02d}+09:00"
            latency_rec.start(self.ledger, f"req-{index}", "read", start_at)
            latency_rec.end(self.ledger, f"req-{index}", "read", end_at,
                             ai_kind="fast", cost_yen=0.1 * index)
        report = latency_rec.report(self.ledger, "2026-11-18T09:00:00+09:00")
        self.assertEqual(report["read"], {"median_seconds": 2.0, "p90_seconds": 3.0, "count": 3})
        filtered = latency_rec.report(self.ledger, "2026-11-18T10:00:05+09:00")
        self.assertEqual(filtered["read"]["count"], 1)
        row = self.ledger.conn.execute("select ai_kind,cost_yen from latency where req_id='req-3'").fetchone()
        self.assertEqual(row["ai_kind"], "fast")
        self.assertAlmostEqual(row["cost_yen"], 0.3)

    def test_legacy_redesign_jsonl_import_is_repeatable(self):
        path = os.path.join(self.tmp.name, "latency.jsonl")
        rows = [
            {"asked_at": "2026-11-18 09:00:00 JST", "logged_at": "2026-11-18 09:00:05 JST",
             "scene": "simple", "adjustments": 1},
            {"asked_at": "2026-11-18T09:10:00+09:00", "logged_at": "2026-11-18T09:10:30+09:00",
             "scene": "dialog", "adjustments": 2},
            {"asked_at": "bad", "logged_at": "bad", "scene": "simple", "adjustments": 1},
        ]
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.assertEqual(latency_rec.import_jsonl(self.ledger, path), 2)
        self.assertEqual(latency_rec.import_jsonl(self.ledger, path), 2)
        self.assertEqual(self.ledger.conn.execute("select count(*) from latency").fetchone()[0], 2)
        report = latency_rec.report(self.ledger, datetime.fromisoformat("2026-11-18T08:00:00+09:00"))
        self.assertEqual(report["legacy:simple"]["median_seconds"], 5.0)
        self.assertEqual(report["legacy:dialog"]["median_seconds"], 30.0)


if __name__ == "__main__":
    unittest.main()
