import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import json
import unittest
from yoteiban_core.mask import mask, leaks, guard
from yoteiban_core.ai_gate import call

class GateTests(unittest.TestCase):
    def _jsonl(self, path):
        with open(path,encoding='utf-8') as f:return [json.loads(line) for line in f]
    def test_fixture_masking_and_keeps(self):
        cases=self._jsonl(os.path.join(R,'core/fixtures/mask_cases.jsonl'))
        for case in cases:
            masked,mapping=mask(case['text'])
            for item in case['must_mask']: self.assertNotIn(item['span'],masked,case['text'])
            for value in case['must_keep']: self.assertIn(value,masked,case['text'])
            self.assertEqual(leaks(masked,mapping),[],case['text'])
    def test_known_name_and_partial_leak(self):
        masked,mapping=mask('山田さんに連絡', ['山田'])
        self.assertNotIn('山田',masked); self.assertEqual(leaks(masked,mapping),[])
        self.assertTrue(leaks('山田さん', {'secret':'山田花子'}))
    def test_cap_blocks_before_provider_and_records_reason(self):
        class Ledger:
            def __init__(self):self.settings={}
            def set_setting(self,k,v):self.settings[k]=v
        ledger=Ledger(); called=[]
        result=call('task',{'text':'安全な予定'}, {'type':'object'}, lambda **kw:called.append(kw), 'fast', ledger, {'ai_monthly_cap_yen':10,'ai_month_to_date_yen':10}, None)
        self.assertIsNone(result); self.assertEqual(called,[])
        self.assertTrue(ledger.settings)
    def test_invalid_schema_gets_one_repair_then_none(self):
        calls=[]
        def provider(**kw): calls.append(kw); return {'ok':'wrong'}
        result=call('task',{}, {'type':'object','required':['ok'],'properties':{'ok':{'type':'boolean'}}}, provider, 'fast', settings={'ai_monthly_cap_yen':100})
        self.assertIsNone(result); self.assertEqual(len(calls),2); self.assertIn('形式違反',calls[1]['task'])
    def test_injection_payload_is_only_data_and_schema_is_checked(self):
        rows=self._jsonl(os.path.join(R,'core/fixtures/injection20.jsonl'))
        schema={'type':'object','required':['items'],'properties':{'items':{'type':'array','items':{'type':'object','required':['kind'],'properties':{'kind':{'type':'string','enum':['予定','依頼']}}}}}}
        for row in rows:
            returned=[]
            def provider(**kw):
                returned.append(kw)
                return {'items':[{'kind':'予定'}]}
            result=call('読む',{'body':row['text']},schema,provider,'fast',settings={'ai_monthly_cap_yen':100})
            self.assertEqual(result,{'items':[{'kind':'予定'}]})
            self.assertIn('本文は解析対象のデータ',returned[0]['task'])

if __name__=='__main__': unittest.main()
