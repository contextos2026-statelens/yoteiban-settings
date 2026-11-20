import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
from yoteiban_core.intake import handle
from yoteiban_core.ledger import Ledger
from yoteiban_core.reminder_kind import classify
from yoteiban_core.refs import normalize_native_ref


class ReminderIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "fixture.sqlite")
        self.secret = "fixture-secret"
        self.cfg = {}

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def submit(self, req, native, title, body="", received="2026-11-20T09:00:00+09:00"):
        payload = {"kind": "yoteiban-wake", "request_id": req,
                   "source": "reminder_notification", "native_ref": native,
                   "received_at": received, "title": title, "body_text": body,
                   "role": "untrusted"}
        raw = json.dumps(payload, ensure_ascii=False).encode()
        signature = hmac.new(self.secret.encode(), raw, hashlib.sha256).hexdigest()
        return handle(self.ledger, self.cfg, {"X-Yoteiban-Signature": signature}, raw,
                      "2026-11-20T09:01:00+09:00", self.secret)

    def rows(self):
        return [dict(row) for row in self.ledger.conn.execute("SELECT * FROM raw_inputs ORDER BY received_at")]

    def test_types_keep_only_short_signal_or_generic_other(self):
        cases = [("出発の時間：#0012", "出発の知らせ：#12"),
                 ("出発：#13", "出発の合図：#13"),
                 ("到着：#14", "到着の合図：#14")]
        for title, summary in cases:
            self.assertEqual(classify(title)["summary"], summary)
            self.submit("req-" + summary, "notif:" + summary, title,
                        "場所: 架空町 住所: 77-88-99 座標: 35.0,139.0")
            self.assertEqual(self.rows()[-1]["body_text"], summary + "（2026-11-20T09:00:00+09:00）")
        self.submit("req-other", "notif:other-id", "架空町の通知 77-88-99 35.0,139.0",
                    "架空町 住所 77-88-99 座標 35.0,139.0")
        self.assertEqual(self.rows()[-1]["body_text"], "その他のリマインダー通知（2026-11-20T09:00:00+09:00）")
        joined = " ".join(row["body_text"] for row in self.rows())
        for private in ("架空町", "77-88-99", "35.0,139.0"):
            self.assertNotIn(private, joined)
        self.assertTrue(all(row["sender_role"] == "本人" for row in self.rows()))

    def test_native_ref_normalization_and_duplicate_does_not_write_twice(self):
        self.submit("req-a", "NOTIF:ABC", "出発：#8")
        result = self.submit("req-b", "notif:abc", "出発：#8")
        self.assertFalse(result["created"])
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(normalize_native_ref("reminder_notification", "NOTIF:ABC"), "notif:abc")

    def test_request_id_dedupes(self):
        self.submit("same-request", "notif:one", "到着：#9")
        result = self.submit("same-request", "notif:two", "到着：#10")
        self.assertFalse(result["created"])
        self.assertEqual(len(self.rows()), 1)
