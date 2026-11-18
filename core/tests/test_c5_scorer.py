import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import json
import unittest
from yoteiban_core.scorer import score_reading, score_outlook, score_mask

class ScorerTests(unittest.TestCase):
    def test_reading_manual(self):
        expected=[{'input_id':'a','relevant':True,'items':[{'kind':'予定','event_start':'2026-11-20T09:00:00+09:00','title':'学校行事'}]}, {'input_id':'b','relevant':False,'items':[]}]
        actual=[{'input_id':'a','relevant':True,'items':[{'kind':'予定','event_start':'2026-11-20T10:00:00+09:00','title':'学校行事'}]}, {'input_id':'b','relevant':True,'items':[{'kind':'依頼','title':'誤検知'}]}]
        self.assertEqual(score_reading(expected,actual), {'accuracy':0.5,'missed':0,'false':1,'relevance_accuracy':0.5})
    def test_fixture_shapes_and_quotas(self):
        def load(path):
            with open(path,encoding='utf-8') as f:return [json.loads(x) for x in f]
        inputs=load(os.path.join(R,'core/fixtures/inputs100.jsonl'))
        expected=load(os.path.join(R,'core/fixtures/expected100.jsonl'))
        self.assertEqual(len(inputs),100); self.assertEqual(len(expected),100)
        self.assertEqual(sum(not x['relevant'] for x in expected),20)
        self.assertEqual(sum(len(x['items'])>1 for x in expected),15)
        self.assertEqual(sum(bool(x['notes']) for x in expected),5)
        for file in ('mask_cases.jsonl','injection20.jsonl'):
            self.assertEqual(len(load(os.path.join(R,'core/fixtures',file))),60 if file.startswith('mask') else 20)
        for i in range(1,31):
            with open(os.path.join(R,f'core/fixtures/outlook30/scene_{i:02d}.json'),encoding='utf-8') as f: scene=json.load(f)
            self.assertIn('expected',scene)
        for i in range(1,21):
            with open(os.path.join(R,f'core/fixtures/compare20/task_{i:02d}.json'),encoding='utf-8') as f: task=json.load(f)
            self.assertEqual(set(task['rubric']),{'0','1','2','3','4','5'})
    def test_outlook_manual(self):
        self.assertEqual(score_outlook([{'scene_id':'s','expected':[{'type':'x','date':'d'}]}, {'scene_id':'z','expected':[]}], [{'scene_id':'s','findings':[{'type':'x','date':'d'},{'type':'y','date':'d'}]}]), {'recall':1.0,'false_per_scene':{'s':1,'z':0}})
    def test_mask(self):
        result=score_mask([{'text':'友人Aと連絡','must_mask':[{'span':'友人A','kind':'name'}],'must_keep':['連絡']}], lambda x:x.replace('友人A','[名前]'))
        self.assertEqual(result,{'leaks':[],'kept':[]})

if __name__=='__main__': unittest.main()
