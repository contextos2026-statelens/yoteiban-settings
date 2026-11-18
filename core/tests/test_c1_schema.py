import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import json, sqlite3, tempfile, unittest
from yoteiban_core.db import connect, migrate, seed_settings
from yoteiban_core.config import load

class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.conn = connect(os.path.join(self.tmp.name, "db.sqlite")); migrate(self.conn)
    def tearDown(self): self.conn.close(); self.tmp.cleanup()
    def test_migrate_twice(self):
        migrate(self.conn); migrate(self.conn)
        self.assertEqual(self.conn.execute('select count(*) from schema_version').fetchone()[0], 1)
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
if __name__ == '__main__': unittest.main()
