import unittest
import _paths
from yoteiban_rd.calc import Event, parse_time as T
from yoteiban_rd.nightly import Case, review, format_second_message

class AnswerWritebackTest(unittest.TestCase):
    def test_answered_overdue_case_is_not_repeated(self):
        now=T('2026-11-22 22:00 JST')
        first=review([Case('fictional-case','架空の提出','締切',due=T('2026-11-20 09:00 JST'))],[],now,[])
        key=first['morning'][0]['key']
        next_run=review([Case('fictional-case','架空の提出','締切',due=T('2026-11-20 09:00 JST'))],[],T('2026-11-23 22:00 JST'),[],answers={key:{'answer':'済んだ','answered_at':'2026-11-23T08:00:00+09:00'}})
        self.assertFalse(any(x['key']==key for x in next_run['morning']+next_run['record_only']))
    def test_next_review_date_holds_overdue_case_until_review_day(self):
        case=Case('fictional-case','架空の提出','締切',due=T('2026-11-20 09:00 JST'),
                  next_review_at=T('2026-11-26 08:00 JST'))
        before=review([case],[],T('2026-11-25 22:00 JST'),[])
        after=review([case],[],T('2026-11-26 22:00 JST'),[])
        self.assertFalse(any(f['kind']=='過ぎた' for f in before['morning']+before['record_only']))
        self.assertTrue(any(f['kind']=='過ぎた' for f in after['morning']+after['record_only']))
    def test_record_only_shown_twice_moves_to_weekly(self):
        events=[Event(id='occasion', title='架空の誕生日', start=T('2026-12-10 00:00 JST'), end=T('2026-12-11 00:00 JST'), all_day=True, owner='self')]
        priority=[Case('priority-'+str(i),'架空案件'+str(i),'締切',due=T('2026-11-21 09:00 JST')) for i in range(3)]
        first=review(priority,events,T('2026-11-22 22:00 JST'),[])
        f=next(x for x in first['record_only'] if x['kind']=='機会')
        self.assertEqual(f['shown'],1)
        second=review(priority,events,T('2026-11-23 22:00 JST'),[],previous=first)
        self.assertEqual(next(x for x in second['record_only'] if x['key']==f['key'])['shown'],2)
        third=review(priority,events,T('2026-11-24 22:00 JST'),[],previous=second)
        self.assertTrue(any(x['key']==f['key'] for x in third['weekly']))
        self.assertFalse(any(x['key']==f['key'] for x in third['record_only']))
    def test_answered_opportunity_hidden_until_single_seven_day_resurface(self):
        events=[Event(id='occasion', title='架空の誕生日', start=T('2026-11-30 00:00 JST'), end=T('2026-12-01 00:00 JST'), all_day=True, owner='self')]
        priority=[Case('priority-'+str(i),'架空案件'+str(i),'締切',due=T('2026-11-19 09:00 JST')) for i in range(3)]
        first=review(priority,events,T('2026-11-20 22:00 JST'),[])
        f=next(x for x in first['record_only'] if x['kind']=='機会')
        answers={f['key']:{'answer':'いらない','answered_at':'2026-11-21T08:00:00+09:00'}}
        self.assertFalse(review(priority,events,T('2026-11-22 22:00 JST'),[],answers=answers)['record_only'])
        resurfaced=review(priority,events,T('2026-11-23 22:00 JST'),[],previous=first,answers=answers)
        # Seven days before the event is the one allowed reappearance.
        self.assertTrue(any(x['key']==f['key'] for x in resurfaced['record_only']))
        later=review(priority,events,T('2026-11-24 22:00 JST'),[],previous=resurfaced,answers=answers)
        self.assertFalse(any(x['key']==f['key'] for x in later['morning']+later['record_only']))
    def test_same_day_previous_time_after_answer_blocks_resurface(self):
        events=[Event(id='occasion', title='架空の誕生日', start=T('2026-11-30 00:00 JST'), end=T('2026-12-01 00:00 JST'), all_day=True, owner='self')]
        priority=[Case('priority-'+str(i),'架空案件'+str(i),'締切',due=T('2026-11-19 09:00 JST')) for i in range(3)]
        first=review(priority,events,T('2026-11-20 22:00 JST'),[])
        f=next(x for x in first['record_only'] if x['kind']=='機会')
        previous=dict(first, at='2026-11-21 22:00 JST')
        answers={f['key']:{'answer':'いらない','answered_at':'2026-11-21T08:00:00+09:00'}}
        result=review(priority,events,T('2026-11-23 22:00 JST'),[],previous=previous,answers=answers)
        self.assertFalse(any(x['key']==f['key'] for x in result['morning']+result['record_only']))
    def test_three_days_without_answers_adds_note(self):
        previous={'morning':[{'key':'fictional','title':'架空カード','shown':1}], 'record_only':[]}
        self.assertTrue(review([],[],T('2026-11-22 22:00 JST'),[],previous=previous,no_answer_days=3)['notes'])
        self.assertFalse(review([],[],T('2026-11-22 22:00 JST'),[],previous=previous,no_answer_days=2)['notes'])
    def test_second_message_is_markdown(self):
        table=format_second_message([{'日付':'2026-11-22','件名':'架空の予定','何があるか':'確認','出どころ':'台帳'}])
        self.assertIn('| 日付 | 件名 | 何があるか | 出どころ |',table)
        self.assertIn('|---|---|---|---|',table)
        self.assertNotIn('｜',table)
    def test_feedback_verdicts_stay_suppressed_and_miss_window_is_kept(self):
        now=T('2026-11-22 22:00 JST')
        first=review([Case('fictional-case','架空の提出','締切',due=T('2026-11-20 09:00 JST'))],[],now,[])
        key=first['morning'][0]['key']
        miss=review([Case('fictional-case','架空の提出','締切',due=T('2026-11-20 09:00 JST'))],[],now,[],
                    feedback={key:{'verdict':'外れ','at':'2026-11-22T08:00:00+09:00'}},
                    answers={key:{'answer':'外れ','answered_at':'2026-11-22T08:00:00+09:00'}})
        self.assertTrue(any(x['key']==key for x in miss['suppressed']))
        self.assertFalse(any(x['key']==key for x in miss['morning']+miss['record_only']))
        hit=review([Case('fictional-case','架空の提出','締切',due=T('2026-11-20 09:00 JST'))],[],now,[],
                   answers={key:{'answer':'当たり','answered_at':'2026-11-22T08:00:00+09:00'}})
        self.assertFalse(any(x['key']==key for x in hit['morning']+hit['record_only']))

if __name__ == '__main__': unittest.main()
