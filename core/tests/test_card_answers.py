import os, tempfile, unittest
from yoteiban_core.ledger import Ledger

class CardAnswerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(os.path.join(self.tmp.name, 'ledger.sqlite'))
        self.ledger.upsert_case({'case_id':'fictional-case','kind':'締切','title':'架空の提出','status':'確認中','due_at':'2026-11-20T09:00:00+09:00'}, now='2026-11-18T09:00:00+09:00')
    def tearDown(self):
        self.ledger.close(); self.tmp.cleanup()
    def test_done_writeback_is_idempotent_and_has_history(self):
        args=('fictional-key','済んだ','2026-11-22T08:00:00+09:00',3,'fictional-case')
        self.ledger.record_card_answer(*args)
        again=self.ledger.record_card_answer('fictional-key','済んだ','2026-11-22T08:01:00+09:00',3,'fictional-case')
        self.assertFalse(again['inserted'])
        self.assertEqual(self.ledger.get_case('fictional-case')['status'],'済んだ')
        self.assertEqual(self.ledger.conn.execute("select count(*) from history where case_id='fictional-case' and basis=?",(args[2],)).fetchone()[0],1)
        self.assertEqual(len(self.ledger.list_card_answers()),1)
    def test_still_moves_next_review(self):
        self.ledger.record_card_answer('fictional-key','まだ','2026-11-22T08:00:00+09:00',4,'fictional-case')
        self.assertEqual(self.ledger.get_case('fictional-case')['next_review_at'],'2026-11-26T08:00:00+09:00')
    def test_unanswered_days_are_counted_consecutively(self):
        for day in ('2026-11-20','2026-11-21','2026-11-22'):
            self.ledger.record_card_presentations(['fictional-key'],day)
        self.assertEqual(self.ledger.unanswered_presentation_streak('2026-11-22'),3)
        self.ledger.record_card_answer('fictional-key','当たり','2026-11-21T08:00:00+09:00')
        self.assertEqual(self.ledger.unanswered_presentation_streak('2026-11-22'),1)
    def test_question_answer_uses_set_question_and_reads_back(self):
        question_id=self.ledger.add_question('fictional-case','架空の場所','本人に聞く',now='2026-11-18T09:00:00+09:00')
        result=self.ledger.record_card_answer('question-key','出して','2026-11-22T08:00:00+09:00',question_id=question_id)
        self.assertTrue(result['ok'])
        self.assertEqual(result['question']['status'],'埋まった')
        self.assertEqual(result['question']['answer'],'出して')

if __name__ == '__main__': unittest.main()
