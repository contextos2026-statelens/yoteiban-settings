import os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in ("core", "redesign/lib", "redesign/mac", "tools/stage2"): sys.path.insert(0, os.path.join(R, p))
import importlib.util, json, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from yoteiban_core.backup import BackupError, MAGIC, restore, rotate, snapshot, keygen
from yoteiban_core.importer import (
    INPUT_ONLY, count_extra, extra_needs_review, from_casebook,
    from_notion_exceptions, from_notion_export, from_notion_history,
    from_notion_sources, infer_urgency, map_certainty, map_mute_state,
    map_status, map_tentative, normalize_inferred_fields, parse_duration_min,
)
from yoteiban_core.ledger import Ledger

HAS_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None

class BackupImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.db_path=self.root/'source.db'; self.ledger=Ledger(self.db_path)
        self.ledger.upsert_case({"case_id":"base","kind":"予定","title":"学校X行事"},now="2026-11-18T10:00:00+09:00")
    def tearDown(self): self.ledger.close(); self.tmp.cleanup()

    @unittest.skipUnless(HAS_CRYPTOGRAPHY, "cryptography が無い環境では暗号試験をskip")
    def test_encrypted_snapshot_restore_and_reject_mismatch(self):
        os.environ['YOTEIBAN_BACKUP_PASSPHRASE']='test-passphrase'
        keys=keygen(self.root/'keys')
        backup=self.root/'sample.yoteiban'; target=self.root/'restored.db'
        result=snapshot(self.db_path,backup,keys['public_key'])
        self.assertTrue((self.root/'sample.yoteiban.sha256').exists())
        restore(backup,keys['private_key'],target,'YOTEIBAN_BACKUP_PASSPHRASE')
        con=__import__('sqlite3').connect(target)
        try:self.assertEqual(con.execute("select title from cases where case_id='base'").fetchone()[0],'学校X行事')
        finally:con.close()
        other=keygen(self.root/'other-keys')
        with self.assertRaises(BackupError): restore(backup,other['private_key'],self.root/'wrong.db','YOTEIBAN_BACKUP_PASSPHRASE')
        damaged=self.root/'damaged.yoteiban'; damaged.write_bytes(backup.read_bytes()+b'x')
        (self.root/'damaged.yoteiban.sha256').write_text(result['sha256'],encoding='ascii')
        with self.assertRaises(BackupError): restore(damaged,keys['private_key'],self.root/'damaged.db','YOTEIBAN_BACKUP_PASSPHRASE')
        self.assertFalse((self.root/'wrong.db').exists()); self.assertFalse((self.root/'damaged.db').exists())

    def test_rotate_keeps_recent_days_and_months(self):
        root=self.root/'archive'; root.mkdir()
        specs=[('nov18a',datetime(2026,11,18,10,tzinfo=timezone.utc)),('nov18b',datetime(2026,11,18,8,tzinfo=timezone.utc)),('nov17',datetime(2026,11,17,10,tzinfo=timezone.utc)),('oct',datetime(2026,10,4,10,tzinfo=timezone.utc)),('sep',datetime(2026,9,4,10,tzinfo=timezone.utc))]
        for name,dt in specs:
            path=root/(name+'.snapshot'); path.write_bytes(MAGIC+b'payload'); ts=dt.timestamp(); os.utime(path,(ts,ts))
            Path(str(path)+'.sha256').write_text('hash',encoding='ascii')
        outcome=rotate(root,keep_daily=1,keep_monthly=2)
        kept={p.name for p in root.iterdir() if p.name.endswith('.snapshot')}
        self.assertEqual(kept,{'nov18a.snapshot','oct.snapshot'})
        self.assertFalse((root/'sep.snapshot.sha256').exists())
        self.assertEqual(outcome['kept'],2)

    def test_casebook_import_idempotent_mapping_and_dry_run(self):
        payload={"version":1,"requests":{"request-a":{"request_id":"request-a","source":"mail","received_at":"2026-11-18 09:00 JST","case_ids":["request-a#event"]}},
                 "cases":{"request-a#event":{"case_id":"request-a#event","request_id":"request-a","case_key":"event","summary":"友達Aの行事","case_status":"提案中"},
                          "request-a#other":{"case_id":"request-a#other","request_id":"request-a","case_key":"other","summary":"期限","case_status":"未対応の状態"}}}
        path=self.root/'casebook.json'; path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        now='2026-11-20T10:00:00+09:00'
        dry=from_casebook(self.ledger,path,now,dry_run=True)
        self.assertEqual(dry['imported'],2); self.assertEqual(self.ledger.conn.execute('select count(*) from raw_inputs').fetchone()[0],0)
        first=from_casebook(self.ledger,path,now)
        self.assertEqual(first['imported'],2); self.assertEqual(len(first['unmapped']),1)
        case=self.ledger.get_case('request-a#event')
        self.assertEqual(case['status'],'確認中'); self.assertEqual(case['title'],'友達Aの行事')
        raw=self.ledger.conn.execute('select body_text,body_masked,received_at from raw_inputs').fetchone()
        self.assertEqual(tuple(raw),('', '', '2026-11-18T09:00:00+09:00'))
        second=from_casebook(self.ledger,path,now)
        self.assertEqual(second['imported'],0); self.assertEqual(second['skipped'],2)
        self.assertEqual(self.ledger.conn.execute('select count(*) from cases').fetchone()[0],2)

    def test_notion_import_uses_native_ref_and_dry_run(self):
        rows=[{'ID':'row-1','タイトル':'学校Xの予定','状態':'未確定','要約':'メモ'}, {'ID':'row-1','タイトル':'重複','状態':'未確定'}]
        mapping={'ID':'native_ref','タイトル':'title','状態':'status','要約':'summary'}
        now='2026-11-20T10:00:00+09:00'
        dry=from_notion_export(self.ledger,rows[:1],mapping,now,dry_run=True)
        self.assertEqual(dry['imported'],1); self.assertEqual(self.ledger.conn.execute('select count(*) from cases').fetchone()[0],1)
        first=from_notion_export(self.ledger,rows,mapping,now)
        self.assertEqual(first['imported'],1); self.assertEqual(first['skipped'],1)
        second=from_notion_export(self.ledger,rows[:1],mapping,now)
        self.assertEqual(second['imported'],0); self.assertEqual(second['skipped'],1)
        row=self.ledger.conn.execute("select body_text,body_masked,summary_masked from raw_inputs where source='notion'").fetchone()
        self.assertEqual(tuple(row),('', '', ''))


    def test_status_and_certainty_maps(self):
        self.assertEqual(map_status("予定あり"), "未確定")
        self.assertIs(map_status("予定なし"), INPUT_ONLY)
        self.assertEqual(map_status("迷い"), "未確定")
        self.assertEqual(map_status("確認待ち"), "確認中")
        self.assertEqual(map_status("返事待ち"), "確認中")
        self.assertEqual(map_status("登録済み"), "決まった")
        self.assertEqual(map_status("未確定"), "未確定")
        self.assertEqual(map_status(""), "未確定")
        with self.assertRaises(ValueError):
            map_status("未知")
        self.assertEqual(map_certainty("高"), "確定")
        self.assertEqual(map_certainty("中"), "推測")
        self.assertEqual(map_certainty("低"), "未定")
        self.assertEqual(map_certainty("確定"), "確定")
        self.assertEqual(map_certainty(None), "推測")
        with self.assertRaises(ValueError):
            map_certainty("普通")
        self.assertEqual(parse_duration_min(90), 90)
        self.assertEqual(parse_duration_min("1時間半"), 90)
        self.assertEqual(parse_duration_min("1時間30分"), 90)
        self.assertEqual(parse_duration_min("1.5時間"), 90)
        self.assertEqual(parse_duration_min("45分"), 45)
        self.assertEqual(parse_duration_min("30"), 30)
        self.assertEqual(parse_duration_min("30分程度"), 30)
        self.assertEqual(parse_duration_min("10分（仮）"), 10)
        self.assertEqual(parse_duration_min("1時間半くらい"), 90)
        self.assertIsNone(parse_duration_min(""))
        with self.assertRaises(ValueError):
            parse_duration_min("長い")
        self.assertEqual(map_mute_state("自動印のみ"), "自動（印のみ）")
        self.assertEqual(map_mute_state("本人が明示（通知しない）"), "本人が明示")
        self.assertEqual(map_tentative("__YES__"), 1)
        self.assertEqual(map_tentative("いいえ"), 0)
        self.assertEqual(normalize_inferred_fields(["場所", "所要"]), '["場所", "所要"]')

    def test_notion_import_maps_new_columns_and_skips_no_schedule(self):
        rows = [
            {
                "ID": "row-yes", "タイトル": "説明会", "状態": "予定あり", "確実度": "高",
                "場所": "会場A", "所要": "1時間半", "相手": "担当者", "仮": "はい",
                "確認期限": "2026-11-21T18:00:00+09:00", "推測項目": "場所,所要",
                "宛先": "本人", "一対一": "はい", "名前入り": "はい", "予定関連": "はい",
                "ミュート": "自動印のみ",
            },
            {
                "ID": "row-no", "タイトル": "お知らせだけ", "状態": "予定なし",
                "宛先": "本人", "予定関連": "いいえ",
            },
            {
                "ID": "row-wait", "タイトル": "返事待ちの件", "状態": "返事待ち", "確実度": "低",
            },
        ]
        mapping = {
            "ID": "native_ref", "タイトル": "title", "状態": "status", "確実度": "certainty",
            "場所": "location", "所要": "duration_min", "相手": "counterpart", "仮": "tentative",
            "確認期限": "reply_by", "推測項目": "inferred_fields", "宛先": "addressed_to",
            "一対一": "is_direct", "名前入り": "named", "予定関連": "schedule_related",
            "ミュート": "mute_state",
        }
        now = "2026-11-20T10:00:00+09:00"
        result = from_notion_export(self.ledger, rows, mapping, now)
        self.assertEqual(result["imported"], 3)
        self.assertEqual(result["unmapped"], [])
        yes = self.ledger.conn.execute(
            "SELECT c.status, c.certainty, c.location, c.duration_min, c.counterpart, c.tentative, c.reply_by, c.inferred_fields, r.addressed_to, r.is_direct, r.named, r.schedule_related, r.mute_state "
            "FROM cases c JOIN raw_inputs r ON c.cause_input_id=r.input_id WHERE r.native_ref='row-yes'"
        ).fetchone()
        self.assertEqual(tuple(yes), (
            "未確定", "確定", "会場A", 90, "担当者", 1, "2026-11-21T18:00:00+09:00", '["場所", "所要"]',
            "本人", "はい", "はい", "はい", "自動（印のみ）",
        ))
        self.assertIsNone(self.ledger.conn.execute(
            "SELECT c.case_id FROM cases c JOIN raw_inputs r ON c.cause_input_id=r.input_id WHERE r.native_ref='row-no'"
        ).fetchone())
        no_row = self.ledger.conn.execute(
            "SELECT addressed_to, schedule_related FROM raw_inputs WHERE native_ref='row-no'"
        ).fetchone()
        self.assertEqual(tuple(no_row), ("本人", "いいえ"))
        wait = self.ledger.conn.execute(
            "SELECT c.status, c.certainty FROM cases c JOIN raw_inputs r ON c.cause_input_id=r.input_id WHERE r.native_ref='row-wait'"
        ).fetchone()
        self.assertEqual(tuple(wait), ("確認中", "未定"))

    def test_notion_import_unmapped_certainty_and_duration_still_imports_duration_blank(self):
        rows = [
            {"ID": "bad-cert", "タイトル": "不明な確実度", "状態": "迷い", "確実度": "普通"},
            {"ID": "odd-dur", "タイトル": "所要が読めない", "状態": "予定あり", "所要": "長い"},
        ]
        mapping = {"ID": "native_ref", "タイトル": "title", "状態": "status", "確実度": "certainty", "所要": "duration_min"}
        now = "2026-11-20T10:00:00+09:00"
        result = from_notion_export(self.ledger, rows, mapping, now)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(result["unmapped"]), 1)
        self.assertEqual(result["unmapped"][0]["native_ref"], "bad-cert")
        row = self.ledger.conn.execute(
            "SELECT c.duration_min, c.status FROM cases c JOIN raw_inputs r ON c.cause_input_id=r.input_id WHERE r.native_ref='odd-dur'"
        ).fetchone()
        self.assertEqual(tuple(row), (None, "未確定"))


    def test_empty_native_ref_is_unmapped_without_url_fallback(self):
        rows = [{"ID": "", "タイトル": "広告", "状態": "予定なし", "url": "https://example.test/row"}]
        mapping = {"ID": "native_ref", "タイトル": "title", "状態": "status"}
        result = from_notion_export(self.ledger, rows, mapping, "2026-11-20T10:00:00+09:00")
        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["unmapped"]), 1)
        self.assertIn("native_ref", result["unmapped"][0]["reason"])
        self.assertEqual(self.ledger.conn.execute("SELECT COUNT(*) FROM raw_inputs WHERE source='notion'").fetchone()[0], 0)

    def test_schedule_related_overrides_no_schedule(self):
        rows = [
            {"ID": "rel-yes", "タイトル": "関連あり", "状態": "予定なし", "予定関連": "はい"},
            {"ID": "rel-maybe", "タイトル": "迷い", "状態": "予定なし", "予定関連": "迷い"},
        ]
        mapping = {"ID": "native_ref", "タイトル": "title", "状態": "status", "予定関連": "schedule_related"}
        result = from_notion_export(self.ledger, rows, mapping, "2026-11-20T10:00:00+09:00")
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["unmapped"], [])
        rows_out = self.ledger.conn.execute(
            "SELECT r.native_ref, c.status FROM cases c JOIN raw_inputs r ON c.cause_input_id=r.input_id ORDER BY r.native_ref"
        ).fetchall()
        self.assertEqual([tuple(r) for r in rows_out], [("rel-maybe", "未確定"), ("rel-yes", "未確定")])

    def test_unknown_column_goes_to_extra_and_does_not_stop(self):
        rows = [{"ID": "x1", "タイトル": "説明会", "状態": "予定あり", "謎の列": "控え", "別の未知": "メモ", "作成日": "2026-11-19T10:00:00+09:00", "date:日時:is_datetime": 1}]
        mapping = {"ID": "native_ref", "タイトル": "title", "状態": "status"}
        result = from_notion_export(self.ledger, rows, mapping, "2026-11-20T10:00:00+09:00")
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["unmapped"], [])
        extra = json.loads(self.ledger.conn.execute(
            "SELECT c.extra FROM cases c JOIN raw_inputs r ON c.cause_input_id=r.input_id WHERE r.native_ref='x1'"
        ).fetchone()[0])
        self.assertEqual(extra, {"謎の列": "控え", "別の未知": "メモ"})
        self.assertEqual(count_extra(self.ledger)["cases"], 1)
        self.assertEqual(count_extra(self.ledger)["fields"], 2)

    def test_routed_columns_and_retention(self):
        rows = [{
            "ID": "route-1", "タイトル": "面談", "状態": "登録済み",
            "確認する項目": "場所を聞く", "カレンダー登録": "済",
            "登録先のイベント参照": "evt-1", "14日後の消去対象か": "__YES__",
            "date:受信日時:start": "2026-11-16T17:00:00+09:00",
        }]
        mapping = {
            "ID": "native_ref", "タイトル": "title", "状態": "status",
            "date:受信日時:start": "received_at",
        }
        now = "2026-11-20T10:00:00+09:00"
        result = from_notion_export(self.ledger, rows, mapping, now)
        self.assertEqual(result["imported"], 1)
        q = self.ledger.conn.execute("SELECT text, fill_method FROM questions").fetchall()
        self.assertEqual([tuple(r) for r in q], [("場所を聞く", "本人に聞く")])
        op = self.ledger.conn.execute("SELECT kind, target_id, state FROM operations").fetchone()
        self.assertEqual(tuple(op), ("cal_personal", "evt-1", "成功（読み戻し済み）"))
        until = self.ledger.conn.execute("SELECT retention_until FROM raw_inputs WHERE native_ref='route-1'").fetchone()[0]
        self.assertTrue(until.startswith("2026-11-30"))

    def test_urgency_from_whole_row_before_extra_and_review_question(self):
        self.assertEqual(infer_urgency({"タイトル": "通常", "メモ": "買い物"}), "朝でよい")
        self.assertEqual(infer_urgency({"タイトル": "緊急の件"}), "すぐ")
        self.assertEqual(infer_urgency({"備考": "今日中に返信"}), "今日中")
        self.assertEqual(extra_needs_review({"メモ": "特になし"}), [])
        self.assertEqual(extra_needs_review({"メモ": "病院へ"}), ["メモ"])
        self.assertEqual(extra_needs_review({"日程": "3月1日"}), ["日程"])
        rows = [{
            "ID": "urg-1", "タイトル": "通常の件", "状態": "予定あり",
            "隠しメモ": "緊急で病院",
        }]
        mapping = {"ID": "native_ref", "タイトル": "title", "状態": "status"}
        result = from_notion_export(self.ledger, rows, mapping, "2026-11-20T10:00:00+09:00")
        self.assertEqual(result["imported"], 1)
        case = self.ledger.conn.execute(
            "SELECT c.urgency, c.extra FROM cases c JOIN raw_inputs r ON c.cause_input_id=r.input_id WHERE r.native_ref='urg-1'"
        ).fetchone()
        self.assertEqual(case[0], "すぐ")
        extra = json.loads(case[1])
        self.assertEqual(extra, {"隠しメモ": "緊急で病院"})
        self.assertNotIn("urgency", extra)
        q = self.ledger.conn.execute("SELECT text FROM questions").fetchone()[0]
        self.assertIn("確かめる", q)
        self.assertIn("隠しメモ", q)
        self.assertGreaterEqual(count_extra(self.ledger)["review"], 1)

    def test_input_only_urgent_extra_gets_outlook_mark(self):
        rows = [{
            "ID": "only-1", "タイトル": "お知らせ", "状態": "予定なし", "予定関連": "いいえ",
            "備考": "今日中",
        }]
        mapping = {"ID": "native_ref", "タイトル": "title", "状態": "status", "予定関連": "schedule_related"}
        result = from_notion_export(self.ledger, rows, mapping, "2026-11-20T10:00:00+09:00")
        self.assertEqual(result["imported"], 1)
        self.assertIsNone(self.ledger.conn.execute(
            "SELECT c.case_id FROM cases c JOIN raw_inputs r ON c.cause_input_id=r.input_id WHERE r.native_ref='only-1'"
        ).fetchone())
        extra = json.loads(self.ledger.conn.execute(
            "SELECT extra FROM raw_inputs WHERE native_ref='only-1'"
        ).fetchone()[0])
        self.assertEqual(extra.get("備考"), "今日中")
        finding = self.ledger.conn.execute(
            "SELECT type, text FROM outlook_findings WHERE type='extra_review'"
        ).fetchone()
        self.assertEqual(tuple(finding), ("extra_review", "その他の箱を確かめる"))

    def test_history_and_exceptions_and_sources(self):
        hist = [
            {"内容": "提案の記録", "種類": "提案", "結果": "出した", "url": "hist-1", "date:時間:start": "2026-11-17T08:00:00+09:00"},
            {"内容": "種類なし", "種類": "", "結果": "残す", "url": "hist-2", "date:時間:start": "2026-11-17T09:00:00+09:00"},
        ]
        now = "2026-11-20T10:00:00+09:00"
        dry = from_notion_history(self.ledger, hist, now, dry_run=True)
        self.assertEqual(dry["imported"], 2)
        self.assertEqual(self.ledger.conn.execute("SELECT COUNT(*) FROM history WHERE actor='移行'").fetchone()[0], 0)
        result = from_notion_history(self.ledger, hist, now)
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["empty_kind"], 1)
        second = from_notion_history(self.ledger, hist, now)
        self.assertEqual(second["skipped"], 2)
        kinds = sorted(json.loads(r[0])["kind"] for r in self.ledger.conn.execute(
            "SELECT change FROM history WHERE actor='移行'"
        ))
        self.assertEqual(kinds, ["その他", "提案"])
        unlinked = self.ledger.conn.execute(
            "SELECT COUNT(*) FROM history WHERE actor='移行' AND case_id LIKE 'history:unlinked:%'"
        ).fetchone()[0]
        self.assertEqual(unlinked, 2)

        exceptions = [{"入力の特徴": "学校の案内", "正しい扱い": "本人の予定として数える"}]
        ex = from_notion_exceptions(self.ledger, exceptions, now)
        self.assertEqual(ex["imported"], 1)
        pref = self.ledger.conn.execute("SELECT topic,state FROM twin_prefs WHERE topic LIKE 'exception:%'").fetchone()
        self.assertIsNotNone(pref)
        self.assertEqual(pref[1], "確定")
        self.assertEqual(from_notion_exceptions(self.ledger, exceptions, now)["skipped"], 1)

        sources = [
            {"出どころ名": "郵便テスト", "種類": "メール", "状態": "有効", "カーソル": "2026-11-19T08:55:00+09:00（印 12）", "信頼度": "不明"},
            {"出どころ名": "未知の源", "種類": "手動", "状態": "有効"},
        ]
        source_map = {
            "郵便テスト": {"source_id": "mail-test", "kind": "email", "reader": "mail-reader", "read_only": True},
        }
        src = from_notion_sources(self.ledger, sources, source_map, now)
        self.assertEqual(src["imported"], 1)
        self.assertEqual(len(src["unmapped"]), 1)
        row = self.ledger.conn.execute(
            "SELECT source_id,kind,reader,read_only,status,last_seen_at FROM sources WHERE source_id='mail-test'"
        ).fetchone()
        self.assertEqual(row[0], "mail-test")
        self.assertEqual(row[1], "email")
        self.assertEqual(row[2], "mail-reader")
        self.assertEqual(row[3], 1)
        self.assertEqual(row[4], "動いている")
        self.assertTrue(row[5].startswith("2026-11-19T08:55:00"))

if __name__=='__main__': unittest.main()
