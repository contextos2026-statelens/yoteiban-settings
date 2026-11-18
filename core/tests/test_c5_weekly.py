import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import json
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from yoteiban_core.ledger import Ledger
from yoteiban_core.weekly import week_report, to_text, record_feedback

WEEK='2026-11-16'
NOW='2026-11-24T10:00:00+09:00'
class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.ledger=Ledger(os.path.join(self.temp.name,'weekly.db'))
    def tearDown(self): self.ledger.close(); self.temp.cleanup()
    def case(self,case_id,kind='依頼',title='手続き',due=None,event_start=None):
        self.ledger.upsert_case({'case_id':case_id,'kind':kind,'title':title,'status':'未確定','due_at':due,'event_start':event_start},actor='ボット',now='2026-11-18T09:00:00+09:00')
    def test_feedback_missed_edits_and_feedback_recorder(self):
        record_feedback(self.ledger,'助かった','役立った','2026-11-17T08:00:00+09:00')
        record_feedback(self.ledger,'通知が多い','うるさい','2026-11-19T08:00:00+09:00')
        record_feedback(self.ledger,'古い感想','足りない','2026-11-15T08:00:00+09:00')
        self.ledger.conn.execute("INSERT INTO history(h_id,case_id,at,actor,change,reason,basis) VALUES(?,?,?,?,?,?,?)",('h1','case','2026-11-20T08:00:00+09:00','ボット','{}','見落とし',''))
        self.ledger.conn.execute("INSERT INTO history(h_id,case_id,at,actor,change,reason,basis) VALUES(?,?,?,?,?,?,?)",('h2','case','2026-11-20T08:00:00+09:00','本人','{}','直し',''))
        report=week_report(self.ledger,WEEK,NOW)
        self.assertEqual(report['feedback']['役立った']['text'],['助かった'])
        self.assertEqual(report['feedback']['うるさい']['count'],1)
        self.assertEqual(report['feedback']['足りない']['count'],0)
        self.assertEqual(report['missed_count'],1); self.assertEqual(report['user_edit_count'],1)
        with self.assertRaises(ValueError):record_feedback(self.ledger,'x','unknown',NOW)
    def test_reply_delay_and_week_window(self):
        self.case('reply','誘い','会う返事','2026-11-20T10:00:00+09:00')
        self.ledger.upsert_case({'case_id':'reply','status':'確認中'},expected_version=1,now='2026-11-19T10:00:00+09:00')
        self.ledger.upsert_case({'case_id':'reply','status':'返事なし'},expected_version=2,now='2026-11-21T10:00:00+09:00')
        self.ledger.upsert_case({'case_id':'reply','status':'確認中'},expected_version=3,reason='返事あり',now='2026-11-22T10:00:00+09:00')
        report=week_report(self.ledger,WEEK,NOW)
        self.assertEqual(report['reply_delay']['count'],1)
        self.assertAlmostEqual(report['reply_delay']['average_days_late'],2.0)
    def test_tech_latency_cost_goals_and_adoption(self):
        for i,seconds in enumerate((1,2,3,4)):
            day=f'2026-11-{18+i:02d}'
            start=day+'T10:00:00+09:00'
            end=day+f'T10:00:{seconds:02d}+09:00'
            self.ledger.record_latency(f'r{i}','read',start,end,ai_kind='fast',cost_yen=5)
        self.ledger.conn.execute("INSERT INTO success_tech VALUES(?,?,?)",('2026-11-20T10:00:00+09:00','read_accuracy',0.9))
        self.ledger.conn.execute("INSERT INTO success_tech VALUES(?,?,?)",('2026-11-20T10:00:00+09:00','double',2))
        root=self.ledger.add_goal(None,'健康','生活を整える','確定',now='2026-11-18T09:00:00+09:00')
        self.ledger.conn.execute("INSERT INTO goal_progress VALUES(?,?,?)",(root,'2026-11-21T10:00:00+09:00','少し前進'))
        self.case('plan-case')
        plan=self.ledger.add_plan('plan-case',{'option':'A'},'2026-11-30T00:00:00+09:00',now='2026-11-18T09:00:00+09:00')
        self.ledger.set_plan_status(plan,'提示',now='2026-11-18T10:00:00+09:00')
        self.ledger.set_plan_status(plan,'採用',now='2026-11-19T10:00:00+09:00')
        report=week_report(self.ledger,WEEK,NOW)
        tech=report['technical']
        self.assertEqual(tech['latency']['count'],4); self.assertEqual(tech['latency']['median_seconds'],2.5)
        self.assertEqual(tech['latency']['p90_seconds'],4); self.assertEqual(tech['cost_yen'],20)
        self.assertEqual(tech['reading_accuracy'],0.9); self.assertEqual(tech['duplicate_count'],2)
        self.assertEqual(report['purpose']['goal_progress'][0]['notes'],['少し前進'])
        self.assertEqual(report['purpose']['goal_progress'][0]['count'],1)
        self.assertEqual(report['purpose']['proposal_adoption']['rate'],1.0)
    def test_late_reminder_clue_uses_no_location(self):
        self.case('event',kind='予定',title='会議',event_start='2026-11-21T10:00:00+09:00')
        op,_=self.ledger.reserve_op('reminder','reminders','event','hash',now='2026-11-20T09:00:00+09:00')
        self.ledger.set_op_state(op,'実行中',now='2026-11-20T09:01:00+09:00')
        self.ledger.set_op_state(op,'成功（読み戻し済み）',result={'completed_at':'2026-11-21T10:05:00+09:00'},now='2026-11-21T10:05:00+09:00')
        report=week_report(self.ledger,WEEK,NOW)
        self.assertEqual(report['late_reminder_clues'],1)
        self.assertNotIn('location',str(report).lower())
    def test_text_is_japanese_and_at_most_five_lines(self):
        text=to_text(week_report(self.ledger,WEEK,NOW))
        self.assertTrue(text.startswith('感想:'))
        self.assertLessEqual(len(text.splitlines()),5)
        self.assertIn('技術:',text); self.assertIn('目的:',text)

if __name__=='__main__':unittest.main()
