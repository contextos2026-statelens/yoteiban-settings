import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import tempfile, unittest
from yoteiban_core.ledger import Ledger, CASE_TRANSITIONS, InvalidTransition, VersionConflict

class LedgerCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.ledger=Ledger(os.path.join(self.tmp.name,'db.sqlite'))
    def tearDown(self): self.ledger.close(); self.tmp.cleanup()
    def create(self, cid, status='提案前', due_at=None):
        self.ledger.upsert_case({'case_id':cid,'kind':'予定','title':'学校行事','status':status,'due_at':due_at}, now='2026-11-18T10:00:00+09:00')
    def test_all_declared_transitions(self):
        n=0
        for source, targets in CASE_TRANSITIONS.items():
            for target in targets:
                cid=f'c{n}'; n+=1; self.create(cid, source)
                version=self.ledger.get_case(cid)['version']
                self.ledger.upsert_case({'case_id':cid,'status':target}, expected_version=version, now='2026-11-18T11:00:00+09:00')
                self.assertEqual(self.ledger.get_case(cid)['status'], target)
        self.create('invalid')
        with self.assertRaises(InvalidTransition): self.ledger.upsert_case({'case_id':'invalid','status':'済んだ'}, expected_version=1)
    def test_version_conflict_carries_current_and_diff(self):
        self.create('c'); self.ledger.upsert_case({'case_id':'c','title':'変更'}, expected_version=1)
        with self.assertRaises(VersionConflict) as caught: self.ledger.upsert_case({'case_id':'c','title':'別案'}, expected_version=1)
        self.assertEqual(caught.exception.current_version, 2)
        self.assertEqual(caught.exception.diff['title']['before'], '変更')
    def test_history_per_write(self):
        self.create('c'); self.ledger.upsert_case({'case_id':'c','title':'更新'}, expected_version=1)
        q=self.ledger.add_question('c','いつですか','本人に聞く')
        self.ledger.set_question(q,'聞き中','確認中')
        self.assertEqual(self.ledger.conn.execute('select count(*) from history').fetchone()[0], 4)
    def test_due_cases(self):
        self.create('due',due_at='2026-11-18T10:00:00+09:00'); self.create('later',due_at='2026-11-19T10:00:00+09:00'); self.create('none')
        rows=self.ledger.list_due('2026-11-18T10:30:00+09:00')
        self.assertEqual([r['case_id'] for r in rows], ['due'])
    def test_question_state_and_bad_method(self):
        self.create('c'); q=self.ledger.add_question('c','日時','待つ')
        self.ledger.set_question(q,'調べ中'); self.ledger.set_question(q,'埋まった','土曜')
        self.assertEqual(tuple(self.ledger.conn.execute('select status,answer from questions where q_id=?',(q,)).fetchone()), ('埋まった','土曜'))
        with self.assertRaises(ValueError): self.ledger.add_question('c','日時','推測する')
if __name__=='__main__': unittest.main()
