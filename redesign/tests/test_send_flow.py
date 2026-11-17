import json, os, subprocess, sys, tempfile, unittest
import _paths  # noqa
from yoteiban_rd.send_flow import SendFlow, SendFlowError

AT = "2026-11-20 10:00 JST"


@unittest.skipUnless(_paths.DELEGATION_PY, "delegation.py が無い")
class SendFlowTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.store = os.path.join(self.d.name, "deleg.json")
        self.f = SendFlow(_paths.DELEGATION_PY, self.store, actor="test")

    def tearDown(self):
        self.d.cleanup()

    def setup_deleg(self, send_allowed=True, approve=True):
        args = ["add-delegation", "--delegation-id", "dg1", "--counterpart-id", "role-family", "--scope", "schedule-check",
                "--valid-from", "2026-11-01 00:00 JST", "--valid-until", "2026-11-30 23:59 JST"]
        args.append("--send-allowed" if send_allowed else "--no-send-allowed")
        self.assertEqual(self.f._run(*args)["_rc"], 0)
        t = ["add-template", "--template-id", "tp1", "--delegation-id", "dg1", "--body", "{date}の予定はどうですか？",
             "--allowed-vars", json.dumps({"date": ["11/22"]})]
        if approve:
            t.append("--approve")
        self.assertEqual(self.f._run(*t)["_rc"], 0)

    def prep(self):
        return self.f.prepare("dr1", "dg1", "tp1", "schedule-check", "role-family", {"date": "11/22"}, AT)

    def test_normal_then_no_resend(self):
        self.setup_deleg()
        p = self.prep()
        self.assertIn("11/22の予定はどうですか？", json.dumps(p["show_to_owner"], ensure_ascii=False))
        with self.assertRaises(SendFlowError):
            self.f.approve_and_handoff("dr1", "", AT)  # 本人のOKが無い
        r = self.f.approve_and_handoff("dr1", "button:ok-1", AT)
        self.assertEqual(r["handoff"]["_rc"], 0)
        with self.assertRaises(SendFlowError):
            self.f.approve_and_handoff("dr1", "button:ok-2", AT)  # 二重送信しない
        self.assertFalse(self.f.may_resend("dr1"))
        c = self.f.confirmed("dr1", "peer-ack-1", AT)
        self.assertEqual(c["_rc"], 0)
        self.assertEqual(self.f.check()["_rc"], 0)

    def test_unknown_is_not_resent(self):
        self.setup_deleg()
        self.prep()
        self.f.approve_and_handoff("dr1", "button:ok", AT)
        self.assertEqual(self.f.unknown("dr1", "通信が切れた")["_rc"], 0)
        self.assertFalse(self.f.may_resend("dr1"))
        with self.assertRaises(SendFlowError):
            self.f.approve_and_handoff("dr1", "button:ok", AT)

    def test_no_send_permission(self):
        self.setup_deleg(send_allowed=False)
        self.prep()
        with self.assertRaises(SendFlowError):
            self.f.approve_and_handoff("dr1", "button:ok", AT)

    def test_unapproved_template_and_bad_vars(self):
        self.setup_deleg(approve=False)
        self.prep()
        with self.assertRaises(SendFlowError):
            self.f.approve_and_handoff("dr1", "button:ok", AT)
        with self.assertRaises(SendFlowError):
            self.f.prepare("dr2", "dg-none", "tp1", "schedule-check", "role-family", {"date": "11/22"}, AT)

    def test_missing_tool(self):
        with self.assertRaises(SendFlowError):
            SendFlow("/nonexistent/delegation.py", self.store)


if __name__ == "__main__":
    unittest.main()
