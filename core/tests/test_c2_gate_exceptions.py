import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))

import os as _os
import json
import tempfile
from pathlib import Path
import unittest

from yoteiban_core.gate import check, close_tentative, payload_hash, reconcile, result
from yoteiban_core.ledger import Ledger


NOW = "2026-11-19T10:00:00+09:00"


class GateExceptionTests(unittest.TestCase):
    """強制追加と［仮］：条件がそろえば承認なしで個人用カレンダーへ追加でき、1つでも欠けたら拒否する。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(_os.path.join(self.tmp.name, "exceptions.sqlite"))
        for i in range(8):
            self.ledger.upsert_case({"case_id": f"case-{i}", "kind": "予定", "title": "用事", "status": "決まった"}, now=NOW)
        self.cfg = {"cal_personal_id": "calendar-personal", "cal_draft_id": "calendar-draft",
                    "cal_readonly_ids": ["calendar-household"], "reminder_list_id": "reminder-dedicated",
                    "reminder_readonly_list_ids": ["reminder-shared"], "notion_db_ids": ["notion-db"],
                    "trusted_source_ids": ["source-school"]}

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def forced(self, case="case-0", target="calendar-personal", title="［自動］保護者会", basis=None, **extra):
        value = {"kind": "cal_personal", "target_id": target, "case_id": case, "grounds": "forced_add",
                 "payload": {"title": title, "event_start": "2026-11-25T14:00:00+09:00"},
                 "basis": {"source_id": "source-school", "datetime_in_text": True, "guessed": False,
                           "forwarded": False, "group": False, "has_instruction": False, "is_ad": False,
                           "notify_style": "標準", **(basis or {})}}
        value.update(extra)
        return value

    def tentative(self, case="case-0", title="［仮］部屋の片付け", basis=None, start="2026-11-22T10:00:00+09:00", **extra):
        value = {"kind": "cal_personal", "target_id": "calendar-personal", "case_id": case, "grounds": "tentative",
                 "payload": {"title": title, "event_start": start},
                 "basis": {"requested_by_owner": True, "owner_words": "週末に片付けしたい",
                           "from_received": False, "other_request": False, "needs_others": False,
                           "needs_reservation": False, "notify_style": "標準", **(basis or {})}}
        value.update(extra)
        return value

    def assert_denied(self, op, cfg=None, why=None):
        out = check(self.ledger, cfg or self.cfg, op, NOW)
        self.assertFalse(out["allow"], out)
        if why:
            self.assertIn(why, out["reason"])
        return out

    # 通る
    def test_forced_add_passes_without_approval_and_keeps_write_order(self):
        out = check(self.ledger, self.cfg, self.forced(), NOW)
        self.assertTrue(out["allow"], out)
        self.assertEqual(out["grounds"], "forced_add")
        self.assertEqual(out["notify_level"], "通常")
        row = self.ledger.get_op(out["op_id"])
        self.assertEqual(row["state"], "実行中")
        self.assertIsNone(row["approval_id"])
        self.assertEqual(row["result"]["grounds"], "forced_add")
        result(self.ledger, out["op_id"], "ok", True, NOW)
        again = check(self.ledger, self.cfg, self.forced(), NOW)
        self.assertFalse(again["allow"])
        self.assertEqual(again["reason"], "成功済み")

    def test_forced_add_quiet_style_is_minimal_and_only_fixed_time(self):
        quiet = self.forced(basis={"notify_style": "静かに"})
        self.assert_denied(quiet, why="静かに")
        ok = check(self.ledger, self.cfg, self.forced(basis={"notify_style": "静かに", "fixed_time": True}), NOW)
        self.assertTrue(ok["allow"], ok)
        self.assertEqual(ok["notify_level"], "最小")

    def test_tentative_passes_without_approval_and_sends_nothing(self):
        out = check(self.ledger, self.cfg, self.tentative(), NOW)
        self.assertTrue(out["allow"], out)
        self.assertEqual(out["grounds"], "tentative")
        self.assertEqual(out["notify_level"], "なし")
        invite = self.tentative(case="case-1")
        invite["payload"]["attendees"] = ["someone"]
        self.assert_denied(invite, why="送信")

    def test_unresolved_exception_is_not_rerun(self):
        out = check(self.ledger, self.cfg, self.forced(), NOW)
        result(self.ledger, out["op_id"], "timeout", None, NOW)
        self.assertEqual(self.ledger.get_op(out["op_id"])["state"], "実行結果不明（要照合）")
        self.assert_denied(self.forced(), why="すでに")
        other = self.forced(title="［自動］保護者会（場所あり）")
        self.assert_denied(other, why="照合")
        self.assertEqual(reconcile(self.ledger, out["op_id"], True, NOW)["state"], "成功（読み戻し済み）")

    # 強制追加：1つ欠けたら拒否
    def test_forced_add_denied_when_any_condition_is_missing(self):
        cases = {
            "推測": {"guessed": True},
            "なりすまし": {"has_instruction": True},
            "広告": {"is_ad": True},
            "文面に書かれて": {"datetime_in_text": False},
            "信頼する出どころ": {"source_id": "source-unknown"},
            "全部確認": {"notify_style": "全部確認"},
        }
        for why, change in cases.items():
            with self.subTest(why=why):
                self.assert_denied(self.forced(basis=change), why=why)
        missing_ad = self.forced()
        del missing_ad["basis"]["is_ad"]
        self.assert_denied(missing_ad, why="広告")
        missing_ins = self.forced()
        del missing_ins["basis"]["has_instruction"]
        self.assert_denied(missing_ins, why="なりすまし")
        self.assert_denied(self.forced(title="保護者会"), why="［自動］")
        no_list = dict(self.cfg)
        del no_list["trusted_source_ids"]
        self.assert_denied(self.forced(), cfg=no_list, why="信頼する出どころ")
        no_time = self.forced()
        no_time["payload"]["event_start"] = "2026-11-25"
        self.assert_denied(no_time, why="時刻")
        self.assertEqual(self.ledger.list_ops(), [])

    def test_forced_add_allows_forwarded_and_group(self):
        # 2026-11-19 本人決定：転送とグループは強制追加を断る理由にしない。
        cases = (
            {"forwarded": True},
            {"group": True},
            {"forwarded": True, "group": True},
        )
        for i, change in enumerate(cases):
            with self.subTest(change=change):
                req = self.forced(case=f"case-{i}", title=f"［自動］保護者会{i}", basis=change)
                out = check(self.ledger, self.cfg, req, NOW)
                self.assertTrue(out["allow"], out)
        missing = self.forced(case="case-3", title="［自動］保護者会欠け")
        del missing["basis"]["forwarded"]
        del missing["basis"]["group"]
        out = check(self.ledger, self.cfg, missing, NOW)
        self.assertTrue(out["allow"], out)

    def test_owner_setting_in_config_wins_over_basis(self):
        cfg = dict(self.cfg, notify_style="全部確認")
        self.assert_denied(self.forced(), cfg=cfg, why="全部確認")
        cfg = dict(self.cfg, tentative_use="使わない")
        self.assert_denied(self.tentative(basis={"tentative_use": "使う"}), cfg=cfg, why="使わない")

    # ［仮］：1つ欠けたら拒否
    def test_tentative_denied_when_any_condition_is_missing(self):
        cases = {
            "全部確認": {"notify_style": "全部確認"},
            "使わない": {"tentative_use": "使わない"},
            "受け取った": {"from_received": True},
            "他の人の頼み": {"other_request": True},
            "他の人の確認": {"needs_others": True},
            "予約": {"needs_reservation": True},
            "自分の言葉": {"requested_by_owner": False},
        }
        for why, change in cases.items():
            with self.subTest(why=why):
                self.assert_denied(self.tentative(basis=change), why=why)
        self.assert_denied(self.tentative(basis={"owner_words": ""}), why="自分の言葉")
        self.assert_denied(self.tentative(title="部屋の片付け"), why="［仮］")
        self.assertEqual(self.ledger.list_ops(), [])

    def test_sixth_tentative_is_denied_and_closing_frees_a_slot(self):
        ids = []
        for i in range(5):
            out = check(self.ledger, self.cfg, self.tentative(case=f"case-{i}", title=f"［仮］用事{i}"), NOW)
            self.assertTrue(out["allow"], out)
            result(self.ledger, out["op_id"], "ok", True, NOW)
            ids.append(out["op_id"])
        self.assert_denied(self.tentative(case="case-5", title="［仮］用事5"), why="5件")
        self.assertTrue(close_tentative(self.ledger, ids[0], "削除", NOW)["ok"])
        sixth = check(self.ledger, self.cfg, self.tentative(case="case-5", title="［仮］用事5"), NOW)
        self.assertTrue(sixth["allow"], sixth)
        self.assertFalse(close_tentative(self.ledger, ids[1], "そのまま", NOW)["ok"])

    # 書き先・操作の種類
    def test_household_calendar_and_other_targets_are_denied(self):
        self.assert_denied(self.forced(target="calendar-household"), why="読み取り専用")
        self.assert_denied(self.forced(target="calendar-draft"))
        self.assert_denied(self.forced(target="unconfigured"))
        home_tentative = self.tentative()
        home_tentative["target_id"] = "calendar-household"
        self.assert_denied(home_tentative, why="読み取り専用")
        draft = self.tentative()
        draft.update({"kind": "cal_draft", "target_id": "calendar-draft"})
        self.assert_denied(draft, why="下書き")
        reminder = self.forced(kind="reminder", target="reminder-dedicated")
        self.assert_denied(reminder, why="個人用カレンダーだけ")
        self.assertEqual(self.ledger.list_ops(), [])

    def test_change_move_delete_are_not_exceptions(self):
        for action in ("update", "move", "delete"):
            with self.subTest(action=action):
                self.assert_denied(self.forced(action=action), why="追加だけ")
        edit = self.tentative()
        edit["payload"]["event_id"] = "event-1"
        self.assert_denied(edit, why="追加だけ")

    def test_unknown_grounds_or_mixed_with_approval_are_denied(self):
        self.assert_denied(self.forced(grounds="owner_said_ok"), why="forced_add")
        op = self.forced()
        approval = self.ledger.grant_approval("cal_personal", payload_hash(op["payload"]), 10, now=NOW)
        op["approval_id"] = approval
        self.assert_denied(op, why="一緒に")
        self.assertEqual(self.ledger.get_approval(approval)["state"], "有効")
        no_basis = self.forced()
        del no_basis["basis"]
        self.assert_denied(no_basis, why="basis")
    def test_forced_add_allows_trusted_sender_email(self):
        senders = Path(self.tmp.name) / "senders.json"
        senders.write_text(json.dumps({
            "senders": [{"name": "例", "ids": ["not-an-email", "Sender@example.com", "表示名"]}]
        }), encoding="utf-8")
        cfg = dict(self.cfg, trusted_source_ids=["source-school"], trusted_senders_file=str(senders))
        op = self.forced(basis={"source_id": "source-unknown", "sender_email": " sender@EXAMPLE.com "})
        out = check(self.ledger, cfg, op, NOW)
        self.assertTrue(out.get("allow"), out)

    def test_forced_add_denies_display_name_or_unknown_sender(self):
        senders = Path(self.tmp.name) / "senders.json"
        senders.write_text(json.dumps({
            "senders": [{"name": "例", "ids": ["Sender@example.com", "表示名"]}]
        }), encoding="utf-8")
        cfg = dict(self.cfg, trusted_senders_file=str(senders))
        self.assert_denied(self.forced(basis={"source_id": "source-unknown", "sender_email": "表示名"}),
                           cfg=cfg, why="信頼する出どころ")
        self.assert_denied(self.forced(basis={"source_id": "source-unknown", "sender_email": "other@example.com"}),
                           cfg=cfg, why="信頼する出どころ")
        self.assert_denied(self.forced(basis={"source_id": "source-unknown"}),
                           cfg=cfg, why="信頼する出どころ")

    def test_forced_add_household_copy_requires_self_class(self):
        cfg = dict(self.cfg, trusted_source_ids=["source-school", "calendar-household", "calendar-family"])
        base = {"source_id": "calendar-household", "copy_from_household": True, "household_class": "self"}
        out = check(self.ledger, cfg, self.forced(case="case-0", title="［自動］自習", basis=base), NOW)
        self.assertTrue(out.get("allow"), out)
        self.assert_denied(self.forced(case="case-1", title="［自動］面談",
                                       basis={"source_id": "calendar-household"}),
                           cfg=cfg, why="本人の予定の写しだけ")
        self.assert_denied(self.forced(case="case-2", title="［自動］授業",
                                       basis={"source_id": "calendar-household",
                                               "copy_from_household": True, "household_class": "other"}),
                           cfg=cfg, why="本人の予定だけ")
        self.assert_denied(self.forced(case="case-3", title="［自動］外食",
                                       basis={"source_id": "calendar-family",
                                               "copy_from_household": True, "household_class": "all"}),
                           cfg=cfg, why="本人の予定だけ")


if __name__ == "__main__":
    unittest.main()
