import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import json
import tempfile
import unittest
from yoteiban_core.ledger import Ledger
from yoteiban_core.interview import QUESTIONS_GOAL, QUESTIONS_TWIN, import_answers, confirm, gaps
from yoteiban_core.twin import rank, predict, observe

class InterviewTwinTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.ledger=Ledger(os.path.join(self.temp.name,'twin.db'))
    def tearDown(self):self.ledger.close();self.temp.cleanup()
    def test_answer_import_and_confirmation(self):
        self.assertEqual(len(QUESTIONS_GOAL),13); self.assertEqual(len(QUESTIONS_TWIN),12)
        goals=import_answers(self.ledger,[{'q_no':1,'answer':'家族との夕食'},{'q_no':2,'answer':'学び'}],'goal','2026-11-18T10:00:00+09:00')
        prefs=import_answers(self.ledger,[{'q_no':1,'answer':'朝'},{'q_no':3,'answer':'余裕を持つ'}],'twin','2026-11-18T10:00:00+09:00')
        self.assertEqual([self.ledger.conn.execute('SELECT state FROM goal_nodes WHERE node_id=?',(x,)).fetchone()[0] for x in goals],['推測','推測'])
        self.assertEqual([self.ledger.conn.execute('SELECT state FROM twin_prefs WHERE pref_id=?',(x,)).fetchone()[0] for x in prefs],['推測','推測'])
        self.assertEqual(import_answers(self.ledger,[{'q_no':1,'answer':'家族との夕食'}],'goal','2026-11-18T10:00:00+09:00'),[goals[0]])
        confirmed=confirm(self.ledger,[goals[0],prefs[0]],'2026-11-19T10:00:00+09:00')
        self.assertEqual(confirmed,[goals[0],prefs[0]])
        self.assertEqual(self.ledger.conn.execute('SELECT state FROM goal_nodes WHERE node_id=?',(goals[0],)).fetchone()[0],'確定')
        self.assertEqual(self.ledger.conn.execute('SELECT state FROM twin_prefs WHERE pref_id=?',(prefs[0],)).fetchone()[0],'確定')
    def test_gaps_reports_goal_branches_without_two_week_activity(self):
        old=self.ledger.add_goal(None,'健康','体調を整える','確定',now='2026-10-01T10:00:00+09:00')
        recent=self.ledger.add_goal(None,'学び','学びを続ける','確定',now='2026-11-18T10:00:00+09:00')
        paused=self.ledger.add_goal(None,'休息','休息','休み中',now='2026-10-01T10:00:00+09:00')
        self.ledger.conn.execute('INSERT INTO goal_progress VALUES(?,?,?)',(recent,'2026-11-17T10:00:00+09:00','進行'))
        result=gaps(self.ledger,[], '2026-11-18T10:00:00+09:00')
        self.assertEqual([x['node_id'] for x in result],[old])
        self.assertEqual(result[0]['last_activity'],'2026-10-01T10:00:00+09:00')
    def test_rank_and_predict_use_preference_state(self):
        prefs=[{'topic':'集中しやすい時間','value':'朝','state':'確定'}, {'topic':'移動の余裕','value':'余裕','state':'推測'}]
        ranked=rank([{'id':'evening','集中しやすい時間':'夜'}, {'id':'morning','集中しやすい時間':'朝','移動の余裕':'余裕'}],prefs)
        self.assertEqual(ranked[0]['option']['id'],'morning'); self.assertIn('集中しやすい時間',ranked[0]['why'])
        self.assertEqual(predict('集中しやすい時間',prefs)['predicted'],'朝')
        self.assertIsNone(predict('食事の時間',prefs)['predicted'])
    def test_observe_updates_pending_prediction_and_observation(self):
        self.ledger.conn.execute('INSERT INTO twin_predictions(id,at,topic,predicted) VALUES(?,?,?,?)',('pred-1','2026-11-18T10:00:00+09:00','集中しやすい時間',json.dumps('朝',ensure_ascii=False)))
        result=observe(self.ledger,'集中しやすい時間','朝','2026-11-19T10:00:00+09:00')
        self.assertEqual(result['hit'],1)
        row=self.ledger.conn.execute('SELECT actual,hit FROM twin_predictions WHERE id=?',('pred-1',)).fetchone()
        self.assertEqual(row['hit'],1); self.assertEqual(json.loads(row['actual']),'朝')
        self.assertEqual(self.ledger.conn.execute('SELECT COUNT(*) FROM twin_observations').fetchone()[0],1)
        unmatched=observe(self.ledger,'移動の余裕','余裕','2026-11-19T10:00:00+09:00')
        self.assertIsNone(unmatched['hit'])

if __name__=='__main__':unittest.main()
