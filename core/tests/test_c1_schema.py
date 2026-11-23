import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import json, sqlite3, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from yoteiban_core.db import connect, migrate, seed_settings
from yoteiban_core import db as dbmod
from yoteiban_core.config import load

class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.conn = connect(os.path.join(self.tmp.name, "db.sqlite")); migrate(self.conn)
    def tearDown(self): self.conn.close(); self.tmp.cleanup()
    def test_migrate_twice(self):
        migrate(self.conn); migrate(self.conn)
        self.assertEqual(self.conn.execute('select count(*) from schema_version').fetchone()[0], 5)
    def test_reject_unknown_status(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("insert into cases(case_id,kind,title,status,created_at,updated_at) values('c','予定','予定','unknown','now','now')")
    def test_trigram_japanese_search(self):
        self.conn.execute("insert into raw_inputs(input_id,source,native_ref,received_at,body_text) values('i','mail','r','now','学校説明会のお知らせ')")
        self.assertTrue(self.conn.execute("select rowid from raw_inputs_fts where raw_inputs_fts match '説明会'").fetchone())
    def test_seed_does_not_overwrite(self):
        migrate(self.conn); self.conn.execute("insert into settings values('rain_prob','12')"); seed_settings(self.conn)
        self.assertEqual(self.conn.execute("select value from settings where key='rain_prob'").fetchone()[0], '12')
    def test_missing_config_keys(self):
        p = os.path.join(self.tmp.name,'config.json')
        with open(p,'w') as f: f.write('{}')
        with self.assertRaisesRegex(ValueError, 'db_path'): load(p)

    def test_upgrade_from_001_adds_columns_and_empty_corrections(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            conn = connect(os.path.join(tmp.name, "old.sqlite"))
            schema_dir = Path(dbmod.__file__).resolve().parent.parent / "schema"
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            script = (schema_dir / "001_init.sql").read_text(encoding="utf-8")
            script = script.replace("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);", "")
            conn.executescript("BEGIN IMMEDIATE;\n" + script + "\nINSERT INTO schema_version(version,applied_at) VALUES(1,'2026-11-19T00:00:00+09:00');\nCOMMIT;")
            conn.execute("INSERT INTO cases(case_id,kind,title,status,created_at,updated_at) VALUES('c1','予定','行事','未確定','t','t')")
            conn.commit()
            names = {row[1] for row in conn.execute("PRAGMA table_info(cases)")}
            self.assertNotIn("location", names)
            migrate(conn)
            versions = [row[0] for row in conn.execute("SELECT version FROM schema_version ORDER BY version")]
            self.assertEqual(versions, [1, 2, 3, 4, 5])
            cases = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(cases)")}
            inputs = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(raw_inputs)")}
            for col in ("location", "duration_min", "counterpart", "inferred_fields", "tentative", "reply_by"):
                self.assertIn(col, cases)
            for col in ("addressed_to", "is_direct", "named", "schedule_related", "mute_state"):
                self.assertIn(col, inputs)
            row = conn.execute("SELECT location, duration_min, counterpart, inferred_fields, tentative, reply_by, extra FROM cases WHERE case_id='c1'").fetchone()
            self.assertEqual(tuple(row), (None, None, None, "[]", 0, None, "{}"))
            self.assertIn("extra", {r[1] for r in conn.execute("PRAGMA table_info(cases)")})
            self.assertIn("extra", {r[1] for r in conn.execute("PRAGMA table_info(raw_inputs)")})
            self.assertEqual(conn.execute("SELECT count(*) FROM urgency_corrections").fetchone()[0], 0)
            conn.close()
        finally:
            tmp.cleanup()

    def test_reject_new_column_checks(self):
        self.conn.execute("INSERT INTO cases(case_id,kind,title,status,created_at,updated_at) VALUES('ok','予定','行事','未確定','t','t')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE cases SET duration_min=-1 WHERE case_id='ok'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE cases SET tentative=2 WHERE case_id='ok'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE cases SET inferred_fields='not-json' WHERE case_id='ok'")
        self.conn.execute("INSERT INTO raw_inputs(input_id,source,native_ref,received_at) VALUES('i','mail','r','t')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE raw_inputs SET is_direct='maybe' WHERE input_id='i'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE raw_inputs SET named='不明' WHERE input_id='i'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE raw_inputs SET schedule_related='不明' WHERE input_id='i'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE raw_inputs SET mute_state='自動印のみ' WHERE input_id='i'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE cases SET extra='not-json' WHERE case_id='ok'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE raw_inputs SET extra='not-json' WHERE input_id='i'")

    def test_urgency_corrections_checks(self):
        self.conn.execute(
            "INSERT INTO urgency_corrections(corr_id,source,kind,word,adj_type,adj_value,reason,created_at,state,review_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("c1", "学校", None, "説明会", "点の加減", "+2", "試験", "2026-11-19T00:00:00+09:00", "本人の指示", "2027-02-17T00:00:00+09:00"),
        )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM urgency_corrections").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO urgency_corrections(corr_id,source,kind,word,adj_type,adj_value,created_at,state,review_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("c2", None, None, None, "点の加減", "+1", "t", "本人の指示", "t"),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO urgency_corrections(corr_id,source,kind,word,adj_type,adj_value,created_at,state,review_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("c3", "学校", None, None, "未知", "+1", "t", "本人の指示", "t"),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO urgency_corrections(corr_id,source,kind,word,adj_type,adj_value,created_at,state,review_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("c4", None, "予定", None, "夜の即時", "たぶん", "t", "本人の指示", "t"),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO urgency_corrections(corr_id,source,kind,word,adj_type,adj_value,created_at,state,review_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("c5", "学校", None, None, "点の加減", "+1", "t", "未承認", "t"),
            )
        self.conn.execute(
            "INSERT INTO urgency_corrections(corr_id,source,kind,word,adj_type,adj_value,created_at,state,review_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("c6", None, "予定", "至急", "夜の即時", "する", "t", "学習の提案を承認", "t"),
        )
if __name__ == '__main__': unittest.main()
