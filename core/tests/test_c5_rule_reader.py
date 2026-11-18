import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import json
import unittest
from datetime import date
from yoteiban_core.rule_reader import read

BASE=date(2026,11,18)
class RuleReaderTests(unittest.TestCase):
    def test_datetime_phrases(self):
        cases=[('11/21(土)10時に学校行事の予定です','2026-11-21T10:00+09:00',False,'11/21(土)'),
               ('明日の15:30に会議の予定です','2026-11-19T15:30+09:00',False,'明日'),
               ('来週の土曜に集まりがあります','2026-11-28',True,'来週の土曜'),
               ('21日に提出する締切です','2026-11-21',True,'21日'),
               ('明日の午後に面談の予定です','2026-11-19',True,'明日')]
        for text,start,date_only,date_text in cases:
            item=read(text,BASE)[0]
            self.assertEqual(item['event_start'],start,text)
            self.assertEqual(item['date_only'],date_only,text)
            self.assertIn(date_text,item['date_text'],text)
    def test_weekday_conflict_is_uncertain(self):
        item=read('11/21(日)10時に会議の予定です',BASE)[0]
        self.assertEqual(item['certainty'],'推測')
    def test_ambiguous_goes_to_ai(self):
        self.assertIsNone(read('来週友達と出かけるかも',BASE))
    def test_fixed_templates(self):
        samples=['予約が確定しました。11月21日10時に歯科の予約です。',
                 '学校からのお知らせです。11月22日9時に説明会を開催します。',
                 'カレンダー招待 11月23日14時に読書会の予定です。']
        for text in samples:
            items=read(text,BASE)
            self.assertIsNotNone(items,text)
            self.assertEqual(items[0]['certainty'],'確定',text)
    def test_fixture_schema_shape(self):
        with open(os.path.join(R,'core/schemas/reading.schema.json'),encoding='utf-8') as f: schema=json.load(f)
        self.assertIn('items',schema['properties'])
        props=schema['properties']['items']['items']['properties']
        self.assertEqual(props['kind']['enum'],['予定','誘い','締切','変更','取消','依頼','お知らせ'])
        required=set(schema['properties']['items']['items']['required'])
        self.assertTrue({'kind','title','event_start','date_only','certainty','needs_reply','reply_by','questions'}<=required)

if __name__=='__main__':unittest.main()
