"""安全の線（夜の表・外部AIへの伏せ字・月の上限・書き込みの道が無いこと）と、取りこぼし（スキルの章の照合・決まりの一覧）。"""
import json, os, re, threading, unittest
import _paths  # noqa
from http.server import BaseHTTPRequestHandler, HTTPServer
from yoteiban_rd.calc import parse_time as P
from yoteiban_rd.urgency import Item, Context, decide
from yoteiban_rd.rules_slice import TASK_CHAPTERS, uncovered_refs
from yoteiban_watch.mask import mask
from yoteiban_watch.sorter import AIClient, sort_item

NOW, NEXT = P("2026-11-18 23:30 JST"), P("2026-11-19 07:00 JST")


def night(setting, **item):
    for k in ("time", "end", "recover_by"):
        if k in item:
            item[k] = P(item[k])
    return decide(Item(title="x", **item), Context(NOW, NEXT, night_setting=setting)).deliver


class NightTable(unittest.TestCase):
    """正本18・19章：夜の設定2種 × 届いたものの種類。"""
    CASES = {
        "(c)": (dict(family_emergency=True), "now", "now"),
        "(c)登録外・時刻なし": (dict(family_emergency=True, registered_source=False, time_in_text=False), "now", "now"),
        "体・安全（登録）": (dict(body_safety=True, time="2026-11-19 10:00 JST"), "morning_top", "now"),
        "体・安全（登録・時刻なし）": (dict(body_safety=True), "morning_top", "now"),
        "体・安全（登録外・時刻なし）": (dict(body_safety=True, registered_source=False, time_in_text=False), "morning_top", "morning_top"),
        "待てない（登録）": (dict(cannot_wait=True, time="2026-11-19 08:00 JST"), "morning_top", "now"),
        "待てない（登録外・時刻なし）": (dict(cannot_wait=True, registered_source=False, time_in_text=False, time="2026-11-19 07:10 JST"), "morning_top", "morning_top"),
        "余裕30分（時刻から）": (dict(time="2026-11-19 07:30 JST"), "morning_top", "now"),
        "過ぎた(a)": (dict(time="2026-11-18 23:00 JST", end="2026-11-19 00:30 JST", importance="中"), "morning_top", "now"),
        "過ぎた(b)": (dict(time="2026-11-18 22:00 JST", importance="高", recover_by="2026-11-19 01:00 JST"), "morning_top", "now"),
        "過ぎた（ふつう）": (dict(time="2026-11-18 20:00 JST"), "morning_top", "morning_top"),
        "高（待てる）": (dict(time="2026-11-19 18:00 JST", importance="高"), "morning_top", "morning_top"),
        "ふつう": (dict(time="2026-11-25 18:00 JST"), "morning", "morning"),
        "補正：夜の即時する": (dict(time="2026-11-19 18:00 JST", importance="高", night_now=True), "morning_top", "now"),
    }

    def test_table(self):
        bad = []
        for name, (item, want_safety, want_urgent) in self.CASES.items():
            got = (night("safety_only", **dict(item)), night("urgent_only", **dict(item)))
            if got != (want_safety, want_urgent):
                bad.append((name, got, (want_safety, want_urgent)))
        self.assertEqual(bad, [])

    def test_safety_only_rings_only_c(self):
        for name, (item, _, _) in self.CASES.items():
            if night("safety_only", **dict(item)) == "now":
                self.assertTrue(name.startswith("(c)"), name)


class MaskTest(unittest.TestCase):
    CASES = [
        ("鈴木先生から連絡", "（人）先生から連絡"), ("ミナトさんも来ます", "（人）さんも来ます"),
        ("田中くんと山田ちゃん", "（人）くんと（人）ちゃん"), ("さくらちゃんと遊ぶ", "（人）ちゃんと遊ぶ"),
        ("Johnさんに渡す", "（人）さんに渡す"), ("佐藤氏と高橋コーチ", "（人）氏と（人）コーチ"),
        ("お父さんとおじいちゃん", "お父さんとおじいちゃん"), ("担任の先生に", "担任の先生に"), ("皆さんへ", "皆さんへ"),
        ("2026-11-19 10:00に集合", "2026-11-19 10:00に集合"), ("11/19(木) 15:30〜16:30 面談", "11/19(木) 15:30〜16:30 面談"),
        ("明日10時半に駅で", "明日10時半に駅で"), ("電話は" + "090" + "-1234-" + "5678まで", "電話は（番号）まで"),
        ("注文番号 12345678", "注文番号 （番号）"), ("a.b@example.com まで", "（メール） まで"),
    ]

    def test_cases(self):
        bad = [(s, mask(s), w) for s, w in self.CASES if mask(s) != w]
        self.assertEqual(bad, [])

    def test_registered_names_first(self):
        self.assertEqual(mask("太郎さんが来る", {"太郎": "家族"}), "（家族）さんが来る")


class _H(BaseHTTPRequestHandler):
    body = b"{}"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200); self.end_headers(); self.wfile.write(_H.body)

    def log_message(self, *a):
        pass


class CostTest(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.ai = AIClient(f"http://127.0.0.1:{self.srv.server_port}/", "m", "k", 2.0)

    def tearDown(self):
        self.srv.shutdown()

    def kw(self, cap):
        return dict(muted_roles=set(), names={}, ai=self.ai, cap_left_yen=cap)

    def test_missing_usage_counts_estimate(self):
        _H.body = json.dumps({"output_text": '{"related": true, "urgent": false, "to_owner": "yes"}'}).encode()
        r = sort_item("来週ごはん", "友だち", "imessage", **self.kw(100))
        self.assertEqual(r.by, "ai"); self.assertGreater(r.yen, 0)

    def test_bad_shape_still_charged(self):
        _H.body = json.dumps({"output_text": "no json", "usage": {"input_tokens": 100, "output_tokens": 10}}).encode()
        r = sort_item("来週ごはん", "友だち", "imessage", **self.kw(100))
        self.assertEqual(r.by, "fallback"); self.assertGreater(r.yen, 0)

    def test_server_error_charged(self):
        dead = AIClient("http://127.0.0.1:9/", "m", "k", 0.5)
        r = sort_item("来週ごはん", "友だち", "imessage", muted_roles=set(), names={}, ai=dead, cap_left_yen=100)
        self.assertEqual(r.by, "fallback"); self.assertGreater(r.yen, 0)

    def test_no_call_when_one_call_could_exceed(self):
        p = "x" * 100
        cap = self.ai.max_yen(p) / 10
        r = sort_item("来週ごはん", "友だち", "imessage", **self.kw(cap))
        self.assertIn("上限", r.reason); self.assertEqual(r.yen, 0)

    def test_config_example_cap_500(self):
        cfg = json.load(open(os.path.join(_paths.ROOT, "mac", "config.example.json"), encoding="utf-8"))
        self.assertEqual(cfg["ai_monthly_cap_yen"], 500)
        names = [c["name"] for c in cfg["sources"]["calendars"]]
        self.assertGreaterEqual(len(names), 3)  # 個人用・家用（primary）・ファミリー


class NoWritePath(unittest.TestCase):
    """新しい部品（計算係・見直し・朝のまとめ・見分け・切り出し）に、カレンダー・送信・Notion へ書く道が無い。"""

    def test_no_write_calls(self):
        bad = re.compile(r"urlopen|requests\.|subprocess|smtplib|socket\.|insert_event|events\(\)\.insert|send_message|notion")
        for f in ("calc.py", "urgency.py", "nightly.py", "morning.py", "household.py", "rules_slice.py", "selfcheck.py"):
            t = open(os.path.join(_paths.ROOT, "lib", "yoteiban_rd", f), encoding="utf-8").read()
            self.assertIsNone(bad.search(t), f)


class SkillCoverage(unittest.TestCase):
    """取りこぼし：スキルが参照する章が切り出しに入る／決まりの一覧が本文にある／道具の置き場が1つ。"""
    TASK_OF = {"intake": "intake", "write": "write", "notify": "notify", "nightly": "nightly", "infer": "infer",
               "adjust": "adjust", "interval": "interval", "setup": "setup", "delegate": "delegate"}
    LIB = "/home/box/agent-data/yoteiban-redesign/lib"

    def text(self, d):
        with open(os.path.join(_paths.ROOT, "skills", d, "SKILL.md"), encoding="utf-8") as f:
            return f.read()

    def test_r1_refs_covered(self):
        bad = {d: uncovered_refs(t, self.text(d)) for d, t in self.TASK_OF.items()}
        bad = {d: v for d, v in bad.items() if v}
        self.assertEqual(bad, {})
        # 運用ルール：本文が参照する章は、仕事の章（fix）か「場面で足す章」の --add にある
        self.assertEqual(uncovered_refs("fix", self.text("common")), [])

    def test_r2_rules_present(self):
        must = {
            "notify": ["(1)載せるのは持ち主本人の予定と全員の予定だけ", "(2)予定の名前は短くせず、元の件名のまま書く",
                       "(3)見出し（例：🌤天気・📅予定・✅確認してね）", "(4)無かったもの", "(5)誰のものか分からないもの",
                       "(5の2)（2026-11-15 本人承認）", "(6)「✅確認してね」の各項目には、どこにあった情報かを書く",
                       "(7)予定ではない、ただの連絡や説明", " 1 すぐ全部：", " 2 時間を決めてまとめて：", " 3 大事なものだけすぐ（おすすめ）：",
                       " 4 静かに：", " 5 おまかせ多め：", " 6 全部確認：", "出どころに家用カレンダーと書く", "読む先の一覧",
                       "当日の出発前の確認", "coverage.all_ok が false"],
            "write": ["【説明欄の書き方】", "「いつ：日時／どこで：場所", "文例：「11/18(水) 14:00に『テスト塾 面談』を書きました」",
                      "『個人用カレンダーID』", "check-write", "weekday", "所要に2割や×1.2を足して余裕にはしない"],
            "intake": ["【媒体別の振り分けの手がかり】", "queue.py record", "「〇〇が読めていません（最後に読めたのは△時）」",
                       "「〇〇は未確認」", "item.end", "item.late_ok", "item.recover_by", "item.night_now"],
            "common": ["現在地は「どこで」を省くかの判定にだけ使い", "読む先の一覧", "終了コードが3", "selfcheck",
                       "「夜は鳴らさない」なら、夜に即時で出すのは家族・近い人の急変（第18章の(c)）だけ", "登録外の名前も伏せる",
                       "【安全の線"],
            "nightly": ["previous", "feedback", "weekly", "suppressed", "当たり外れ", "家用だけ"],
            "setup": ["R10 毎晩の見直し", "記憶の『読む先の一覧』のカレンダーを全部読み"],
            "selftest": ["【X60", "【X64", "【X66", "【X67", "【X68"],
        }
        missing = [(d, s) for d, ss in must.items() for s in ss if s not in self.text(d)]
        self.assertEqual(missing, [])

    def test_r4_one_lib_place(self):
        for d in os.listdir(os.path.join(_paths.ROOT, "skills")):
            t = self.text(d)
            for m in re.finditer(r"cd (\S+) && python3 -m yoteiban_rd", t):
                self.assertEqual(m.group(1), self.LIB, d)
            self.assertNotIn("<redesign/lib>", t, d)

    def test_every_skill_reads_common_first(self):
        for d in os.listdir(os.path.join(_paths.ROOT, "skills")):
            if d in ("common", "selftest"):
                continue
            self.assertIn("「予定ばんの運用ルール」を読み", self.text(d), d)


if __name__ == "__main__":
    unittest.main()


class PartialAdd(unittest.TestCase):
    """--add の節だけ（4.3）・段落だけ（21:見出し語）と、見つからないときの章全部への戻し。"""
    TEXT = ("# 正本\n\n## 第2章 原則\n\n原則。\n\n"
            "## 4. 表\n\n前書き。\n\n### 4.3 送信\n\n送信の3状態。\n\n### 4.4 列\n\n列の3系統。\n\n"
            "## 21. 秘書\n\n前書き。\n\n余裕のとり方。何分で足す。\n\n1. 続きの手順。\n\n暮らしの型。別の話。\n")

    def test_parts(self):
        from yoteiban_rd.rules_slice import slice_for, add_specs, add_refs
        r = slice_for("fix", self.TEXT, ["4.3", "21:余裕のとり方"])
        self.assertIn("送信の3状態", r["text"])
        self.assertNotIn("列の3系統", r["text"])
        self.assertIn("1. 続きの手順", r["text"])
        self.assertNotIn("暮らしの型", r["text"])
        self.assertEqual(r["widened"], [])
        r = slice_for("fix", self.TEXT, ["21:ない見出し"])
        self.assertIn("暮らしの型", r["text"])  # 見つからなければ章全部
        self.assertEqual(r["widened"], ["21:ない見出し"])
        self.assertEqual(add_specs("→ --add 21:余裕のとり方／--add 4.3,29。"), ["21:余裕のとり方", "4.3", "29"])
        self.assertEqual(add_refs("--add 21:余裕のとり方／--add 4.3,29"), {4, 21, 29})

    def test_labels_exist_in_skills(self):
        """スキルに書いた「章:見出し語」が本物の正本で見つかる（箱に正本があるときだけ）。"""
        import glob
        from yoteiban_rd.rules_slice import add_specs, slice_for, DEFAULT_SEIHON
        if not os.path.exists(DEFAULT_SEIHON):
            self.skipTest("正本なし")
        text = open(DEFAULT_SEIHON, encoding="utf-8").read()
        root = os.path.join(os.path.dirname(__file__), "..", "skills")
        for f in glob.glob(os.path.join(root, "*", "SKILL.md")):
            for s in add_specs(open(f, encoding="utf-8").read()):
                if ":" in s or "." in s:
                    self.assertEqual(slice_for("fix", text, [s])["widened"], [], f"{f}: {s}")
