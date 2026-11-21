import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"core")); sys.path.insert(0,str(ROOT/"redesign"/"mac"))
from yoteiban_watch.doctor import check_stage


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.state=Path(self.tmp.name)
        self.cfg={"state_dir":str(self.state),"webhook_url":"fixture","heartbeat_url":"fixture-heartbeat"}
        self.now=datetime(2026,11,21,10,tzinfo=timezone.utc)

    def tearDown(self): self.tmp.cleanup()

    def test_webhook_test_requires_individual_approval(self):
        calls=[]
        result=check_stage(self.cfg,"webhook",send_fn=lambda *a,**k:calls.append(a),now=self.now)
        self.assertEqual(result["detail_code"],"OWNER_APPROVAL_REQUIRED")
        self.assertEqual(calls,[])

    def test_delivery_success_does_not_mean_ledger_registration(self):
        with patch.dict("os.environ",{"YOTEIBAN_WEBHOOK_KEY":"fixture-key"}):
            result=check_stage(self.cfg,"webhook",owner_approved=True,
                              send_fn=lambda *a,**k:{"delivery_state":"success","registration_state":"unverified"},now=self.now)
        self.assertEqual((result["state"],result["detail_code"]),("接続テスト待ち","LEDGER_REGISTRATION_UNVERIFIED"))

    def test_registered_webhook_fixture_marks_only_webhook_connection(self):
        with patch.dict("os.environ",{"YOTEIBAN_WEBHOOK_KEY":"fixture-key"}):
            result=check_stage(self.cfg,"webhook",owner_approved=True,
                              send_fn=lambda *a,**k:{"delivery_state":"success","registration_state":"registered"},now=self.now)
        self.assertEqual(result["state"],"接続成功")
        saved=json.loads((self.state/"integration_status.json").read_text())
        self.assertEqual(saved["connections"]["webhook"]["state"],"接続成功")
        self.assertNotIn("fixture",json.dumps(saved))

    def test_heartbeat_requires_owner_notification_confirmation(self):
        calls=[]
        result=check_stage(self.cfg,"heartbeat",owner_approved=True,ping_fn=lambda url,ok:calls.append((url,ok)) or True,now=self.now)
        self.assertEqual(calls,[("fixture-heartbeat",False)])
        self.assertEqual(result["detail_code"],"WAITING_FOR_OWNER_NOTIFICATION_CONFIRMATION")
        result=check_stage(self.cfg,"heartbeat",owner_approved=True,owner_confirmed_delivery=True,
                           ping_fn=lambda *_:True,now=self.now)
        self.assertEqual(result["state"],"接続成功")

    def test_reader_shared_heartbeat_is_not_reported_as_connected(self):
        (self.state/"heartbeat_reader.json").write_text(json.dumps({"at":self.now.isoformat(),"ok":True,"dedicated":False}))
        result=check_stage(self.cfg,"reader",now=self.now)
        self.assertEqual((result["state"],result["detail_code"]),("接続テスト待ち","READER_HEARTBEAT_SHARED"))


if __name__=="__main__": unittest.main()
