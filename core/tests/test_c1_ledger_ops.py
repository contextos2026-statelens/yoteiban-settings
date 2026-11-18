import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import hashlib, json, os as _os, tempfile, threading, unittest
from yoteiban_core.ledger import Ledger, InvalidTransition, SchemaError

class LedgerOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=os.path.join(self.tmp.name,'db.sqlite'); self.ledger=Ledger(self.path)
        self.ledger.upsert_case({'case_id':'case-a','kind':'予定','title':'学校行事'}, now='2026-11-18T09:00:00+09:00')
    def tearDown(self): self.ledger.close(); self.tmp.cleanup()
    def test_ingest_idempotent_and_retention(self):
        self.ledger.set_setting('retention_days',30,now='2026-11-18T09:00:00+09:00')
        raw={'source':'mail','native_ref':'message-1','received_at':'2026-11-01T09:00:00+09:00','body_text':'学校行事'}
        input_id,created=self.ledger.ingest(raw)
        self.assertTrue(created)
        self.assertEqual(input_id,hashlib.sha256(b'mail|message-1').hexdigest()[:24])
        stored=self.ledger.conn.execute('select retention_until from raw_inputs where input_id=?',(input_id,)).fetchone()[0]
        self.assertEqual(stored,'2026-12-01T09:00:00+09:00')
        self.assertEqual(self.ledger.ingest(raw),(input_id,False))
    def test_record_reading_multiple_and_empty(self):
        iid,_=self.ledger.ingest({'source':'message','native_ref':'abc','received_at':'2026-11-18T09:00:00+09:00','body_text':'行事'})
        items=[{'kind':'予定','title':'学校行事','event_start':'2026-11-21T10:00:00+09:00','certainty':'確定','questions':[{'text':'場所','fill_method':'本人に聞く'}]}, {'kind':'締切','title':'提出','due_at':'2026-11-22T23:00:00+09:00'}]
        ids=self.ledger.record_reading(iid,items,'プログラム',now='2026-11-18T10:00:00+09:00')
        self.assertEqual(len(ids),2)
        self.assertTrue(all(self.ledger.get_case(cid)['status']=='未確定' for cid in ids))
        self.assertEqual(self.ledger.conn.execute('select recv_state from raw_inputs where input_id=?',(iid,)).fetchone()[0],'読み取り済み')
        iid2,_=self.ledger.ingest({'source':'message','native_ref':'empty','received_at':'2026-11-18T09:00:00+09:00'})
        self.assertEqual(self.ledger.record_reading(iid2,[],'未読'),[])
        self.assertEqual(self.ledger.conn.execute('select recv_state from raw_inputs where input_id=?',(iid2,)).fetchone()[0],'受け取り済み')
    def test_operation_dedup_and_state_handoffs(self):
        args=('send','recipient','case-a','abc123')
        approval=self.ledger.grant_approval('send','abc123',5,'送信を許可',now='2026-11-18T09:59:00+09:00')
        op_id,prior=self.ledger.reserve_op(*args,approval_id=approval,now='2026-11-18T10:00:00+09:00')
        self.assertIsNone(prior)
        self.assertEqual(self.ledger.reserve_op(*args,approval_id=approval)[0:2],(op_id,'予約'))
        self.ledger.use_approval(approval,now='2026-11-18T10:00:00+09:00')
        self.ledger.set_op_state(op_id,'実行中',handoff_state='渡し済み（未確認）',now='2026-11-18T10:01:00+09:00')
        self.ledger.set_op_state(op_id,'成功（読み戻し済み）',handoff_state='送信完了',result={'ok':True},now='2026-11-18T10:02:00+09:00')
        self.assertEqual(self.ledger.get_op(op_id)['result'],{'ok':True})
        with self.assertRaises(InvalidTransition): self.ledger.set_op_state(op_id,'失敗')
        with self.assertRaises(InvalidTransition): self.ledger.set_op_state(op_id,'成功（読み戻し済み）',handoff_state='渡し前（未送信）')
        self.ledger.set_op_state(op_id,'成功（読み戻し済み）',handoff_state='返事あり')
    def test_approval_expiry_and_single_use(self):
        approval=self.ledger.grant_approval('cal_personal','fp',5,'確認',now='2026-11-18T10:00:00+09:00')
        self.ledger.use_approval(approval,now='2026-11-18T10:04:59+09:00')
        self.assertEqual(self.ledger.get_approval(approval)['state'],'使用済み')
        with self.assertRaises(InvalidTransition): self.ledger.use_approval(approval,now='2026-11-18T10:05:00+09:00')
        expired=self.ledger.grant_approval('send','fp2',1,now='2026-11-18T10:00:00+09:00')
        with self.assertRaises(InvalidTransition): self.ledger.use_approval(expired,now='2026-11-18T10:01:00+09:00')
        self.assertEqual(self.ledger.get_approval(expired)['state'],'期限切れ')
    def test_personal_calendar_requires_matching_approval(self):
        with self.assertRaises(InvalidTransition): self.ledger.reserve_op('cal_personal','target','case-a','fp')
        approval=self.ledger.grant_approval('cal_personal','fp',10,now='2026-11-18T10:00:00+09:00')
        with self.assertRaises(InvalidTransition): self.ledger.reserve_op('cal_personal','target','case-a','other',approval_id=approval,now='2026-11-18T10:01:00+09:00')
        op_id,_=self.ledger.reserve_op('cal_personal','target','case-a','fp',approval_id=approval,now='2026-11-18T10:01:00+09:00')
        with self.assertRaises(InvalidTransition): self.ledger.set_op_state(op_id,'実行中')
        self.ledger.use_approval(approval,now='2026-11-18T10:02:00+09:00')
        self.ledger.set_op_state(op_id,'実行中',now='2026-11-18T10:02:01+09:00')
    def test_plans_sources_and_latency(self):
        pid=self.ledger.add_plan('case-a',{'option':1},'2026-11-20T00:00:00+09:00',now='2026-11-18T10:00:00+09:00')
        self.ledger.set_plan_status(pid,'提示'); self.ledger.set_plan_status(pid,'採用')
        self.ledger.conn.execute("insert into sources(source_id,kind,reader,read_only,status) values('src','mail','fake',1,'未接続')")
        self.ledger.source_seen('src',True,'2026-11-18T10:02:00+09:00')
        self.assertEqual(self.ledger.conn.execute("select status from sources where source_id='src'").fetchone()[0],'動いている')
        self.ledger.record_latency('req','read','2026-11-18T10:00:00+09:00','2026-11-18T10:00:02+09:00','v1','fast',2)
        self.assertEqual(self.ledger.conn.execute("select cost_yen from latency where req_id='req'").fetchone()[0],2)
    def test_invalid_shapes_rejected(self):
        with self.assertRaises(SchemaError): self.ledger.ingest({'source':'mail'})
        iid,_=self.ledger.ingest({'source':'mail','native_ref':'bad-item','received_at':'2026-11-18T10:00:00+09:00'})
        with self.assertRaises(SchemaError): self.ledger.record_reading(iid,[{'title':'missing-kind'}],'x')
    def test_concurrent_case_updates_preserve_history(self):
        barrier=threading.Barrier(8); outcomes=[]; errors=[]
        def worker(i):
            try:
                local=Ledger(self.path); version=local.get_case('case-a')['version']; barrier.wait()
                try:
                    local.upsert_case({'case_id':'case-a','summary':f'change-{i}'},expected_version=version,now=f'2026-11-18T10:00:0{i}+09:00')
                    outcomes.append('ok')
                except Exception as e:
                    if e.__class__.__name__=='VersionConflict': outcomes.append('conflict')
                    else: errors.append(repr(e))
                local.close()
            except Exception as e: errors.append(repr(e))
        threads=[threading.Thread(target=worker,args=(i,)) for i in range(8)]
        for t in threads:t.start()
        for t in threads:t.join(20)
        self.assertFalse(errors,errors)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(len(outcomes),8)
        expected=1+outcomes.count('ok')
        self.assertEqual(self.ledger.get_case('case-a')['version'],expected)
        self.assertEqual(self.ledger.conn.execute("select count(*) from history where case_id='case-a'").fetchone()[0],expected)
if __name__=='__main__': unittest.main()
