import json
import hashlib
import hmac
import plistlib
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "redesign" / "mac"))
from reminder_reader.reader import Reader
from yoteiban_core.intake import handle
from yoteiban_core.ledger import Ledger
from yoteiban_core.reminder_kind import classify

MAC_EPOCH = 978307200


def make_db(path, rows=()):
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE app(app_id INTEGER PRIMARY KEY,identifier TEXT);"
                         "CREATE TABLE record(rec_id INTEGER PRIMARY KEY,uuid TEXT,data BLOB,"
                         "delivered_date REAL,app_id INTEGER);")
        db.execute("INSERT INTO app VALUES(1,'com.apple.reminders')")
        db.execute("INSERT INTO app VALUES(2,'other.app')")
        for rec_id, title, delivered, app_id, uuid in rows:
            data = plistlib.dumps({"req": {"titl": title}})
            db.execute("INSERT INTO record VALUES(?,?,?,?,?)", (rec_id, uuid, data, delivered, app_id))


class ReminderReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "fixture.sqlite"
        make_db(self.db)
        self.state = self.root / "state.json"
        self.sent = []
        self.heartbeats = []
        self.responses = [{"state": "起こし済み", "http": 200}]
        self.when = datetime.fromtimestamp(MAC_EPOCH + 1790000000, timezone.utc).astimezone()
        self.cfg = {"notification_db": str(self.db), "webhook_url": "fixture", "heartbeat_url": "fixture-heartbeat"}

    def wake(self, *args, **kwargs):
        self.sent.append(args[2])
        return self.responses.pop(0)

    def heartbeat(self, url, ok):
        self.heartbeats.append((url, ok))
        return True

    def reader(self):
        return Reader(self.cfg, self.state, wake_fn=self.wake,
                      heartbeat_fn=self.heartbeat, now=lambda: self.when)

    def insert(self, rec_id, title, uuid="fixture-id"):
        delivered = self.when.astimezone(timezone.utc).timestamp() - MAC_EPOCH
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO record VALUES(?,?,?,?,1)",
                       (rec_id, uuid, plistlib.dumps({"req": {"titl": title}}), delivered))

    def test_startup_baselines_old_notifications_then_forwards_new_original_title(self):
        self.insert(1, "出発：#77", uuid="old-fixture")
        reader = self.reader()
        self.assertEqual(reader._state()["cursor"], 1)
        self.insert(2, "架空町 住所1-2 座標35.0,139.0", uuid="new-fixture")
        self.assertTrue(reader.once()["ok"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["title"], "架空町 住所1-2 座標35.0,139.0")
        self.assertEqual(set(self.sent[0]), {"kind", "request_id", "source", "native_ref", "received_at", "title"})
        self.assertIsNotNone(datetime.fromisoformat(self.sent[0]["received_at"]).tzinfo)
        self.assertIn("架空町", json.dumps(self.sent, ensure_ascii=False))
        self.assertEqual(self.heartbeats[-1], ("fixture-heartbeat", True))
        reader_hb = json.loads((self.root / "heartbeat_reader.json").read_text())
        self.assertTrue(reader_hb["ok"])
        self.assertFalse(reader_hb["dedicated"])
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["sent"], [self.sent[0]["native_ref"]])
        self.assertNotIn("架空町", self.state.read_text())

    def test_fixture_reader_payload_flows_through_intake_classifier_and_sanitized_ledger(self):
        self.state.write_text(json.dumps({"cursor": 0, "sent": []}))
        reader = self.reader()
        cases = [
            ("出発の時間：#12", "出発の知らせ"),
            ("出発：#12", "出発の合図"),
            ("到着：#12", "到着の合図"),
            ("架空町のリマインダー 77-88-99 座標35.0,139.0", "その他"),
        ]
        self.responses = [{"state": "起こし済み", "http": 200} for _ in cases]
        ledger = Ledger(self.root / "intake.sqlite")
        secret = "fixture-secret"
        try:
            for index, (title, expected_kind) in enumerate(cases, start=1):
                self.insert(index, title, uuid=f"joined-fixture-{index}")
                self.assertTrue(reader.once()["ok"])
                sent = self.sent[-1]
                self.assertEqual(sent["title"], title)
                self.assertIn("T", sent["received_at"])
                self.assertEqual(set(sent), {"kind", "request_id", "source", "native_ref", "received_at", "title"})
                self.assertEqual(classify(sent["title"])["kind"], expected_kind)
                if expected_kind != "その他":
                    self.assertEqual(classify(sent["title"])["case_number"], "12")
                raw = json.dumps(sent, ensure_ascii=False).encode()
                signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
                result = handle(ledger, {}, {"X-Yoteiban-Signature": signature}, raw,
                                "2026-11-20T09:01:00+09:00", secret)
                self.assertTrue(result["ok"], result)
            rows = [row["body_text"] for row in ledger.conn.execute("SELECT body_text FROM raw_inputs ORDER BY received_at")]
            self.assertEqual([row.split("（", 1)[0] for row in rows],
                             ["出発の知らせ：#12", "出発の合図：#12", "到着の合図：#12", "その他のリマインダー通知"])
            self.assertNotIn("架空町", " ".join(rows))
            self.assertNotIn("77-88-99", " ".join(rows))
            self.assertNotIn("35.0,139.0", " ".join(rows))
        finally:
            ledger.close()

    def test_notifications_from_other_apps_are_ignored(self):
        self.state.write_text(json.dumps({"cursor": 0, "sent": []}))
        delivered = self.when.astimezone(timezone.utc).timestamp() - MAC_EPOCH
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO record VALUES(1,'other-fixture',?,?,2)",
                       (plistlib.dumps({"req": {"titl": "出発：#3"}}), delivered))
        self.assertTrue(self.reader().once()["ok"])
        self.assertEqual(self.sent, [])

    def test_only_http_200_and_woken_state_are_marked_successful(self):
        self.state.write_text(json.dumps({"cursor": 0, "sent": []}))
        self.insert(1, "到着：#4", uuid="retry-fixture")
        self.responses = [{"state": "起こし済み", "http": 503}, {"state": "起こし済み", "http": 200}]
        self.assertFalse(self.reader().once()["ok"])
        self.assertEqual(json.loads(self.state.read_text())["sent"], [])
        self.assertTrue(self.reader().once()["ok"])
        self.assertEqual(len(json.loads(self.state.read_text())["sent"]), 1)

    def test_unreadable_database_uses_existing_failure_heartbeat_and_retry_result(self):
        self.cfg["notification_db"] = str(self.root / "missing.sqlite")
        self.assertFalse(self.reader().once()["ok"])
        self.assertEqual(self.heartbeats[-1], ("fixture-heartbeat", False))
        self.assertEqual(self.sent, [])

    def test_unreadable_database_backoff_is_at_least_thirty_seconds(self):
        self.cfg["notification_db"] = str(self.root / "missing.sqlite")
        delays = []
        class StopLoop(Exception):
            pass
        def stop_after_retry_delay(seconds):
            delays.append(seconds)
            raise StopLoop
        reader = Reader(self.cfg, self.state, wake_fn=self.wake, heartbeat_fn=self.heartbeat,
                        sleep=stop_after_retry_delay, now=lambda: self.when)
        with self.assertRaises(StopLoop):
            reader.run(interval_s=5)
        self.assertEqual(delays, [30])
        self.assertEqual(self.heartbeats, [("fixture-heartbeat", False)])

    def test_reader_uses_dedicated_heartbeat_when_configured(self):
        self.cfg["reader_heartbeat_url"] = "fixture-reader-heartbeat"
        self.assertTrue(self.reader().once()["ok"])
        self.assertEqual(self.heartbeats[-1], ("fixture-reader-heartbeat", True))
        data=json.loads((self.root/"heartbeat_reader.json").read_text())
        self.assertTrue(data["dedicated"])

    def test_classifier_recognizes_only_exact_signal_formats(self):
        self.assertEqual(classify("出発の時間：#0012")["summary"], "出発の知らせ：#12")
        self.assertEqual(classify("出発：#13")["kind"], "出発の合図")
        self.assertEqual(classify("到着：#14")["kind"], "到着の合図")
        point = classify("到着：#0012-03")
        self.assertEqual((point["kind"], point["case_number"], point["point_number"]), ("到着の合図", "12", "3"))
        self.assertEqual(classify("終わった？：#12")["kind"], "end_check")
        self.assertEqual(classify("出発：#13 架空駅")["kind"], "その他")

    def test_installer_disables_legacy_agent_before_login_item_and_leaves_watch_alone(self):
        script = (ROOT / "redesign/mac/reminder_reader/install.sh").read_text()
        self.assertLess(script.index("com.yoteiban.reminder-notifications"), script.index("osascript"))
        self.assertNotIn('bootout "gui/$(id -u)/com.yoteiban.watch"', script)
        self.assertIn('[[ "$choice" == "使う" ]] ||', script)
