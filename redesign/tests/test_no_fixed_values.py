"""Gitに入れるファイルに固有値（アドレス・番号・ID類・名前）が無いことを確かめる。
名前の一覧はGitに入れず、箱の外の一覧（YRD_DENYLIST）があるときだけ使う。"""
import os, re, unittest
import _paths  # noqa

ROOT = _paths.ROOT
SKIP_DIRS = {"findings", "__pycache__", ".git"}
SKIP_FILES = {"GIT_MANIFEST.md"}  # ファイルの照合値（16進）の一覧なので番号の形に当たる
PATTERNS = {
    "メール": re.compile(r"[A-Za-z0-9._%+-]+@(?![A-Za-z0-9.-]*example\.(?:com|test)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # 試験用の example.com・example.test は除く
    "電話": re.compile(r"(?<!\d)(?:\+81|0)\d{1,4}-?\d{2,4}-?\d{3,4}(?!\d)"),
    "UUID": re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I),
    "長い16進ID": re.compile(r"(?<![0-9a-f])[0-9a-f]{24,}(?![0-9a-f])"),
    "ストアID・スキルID": re.compile(r"store-[0-9a-f]{6,}|skill-\d{10,}"),
    "WebhookのURL": re.compile("automations" + "/webhook/"),
}


def files():
    for d, ds, fs in os.walk(ROOT):
        ds[:] = [x for x in ds if x not in SKIP_DIRS and not x.startswith(".")]
        for f in fs:
            if f in SKIP_FILES or f.startswith(".") or f.endswith((".pyc", ".jsonl", ".db")):
                continue
            yield os.path.join(d, f)


class NoFixedValues(unittest.TestCase):
    def test_patterns(self):
        bad = []
        for p in files():
            t = open(p, encoding="utf-8", errors="replace").read()
            for k, rx in PATTERNS.items():
                for m in rx.finditer(t):
                    bad.append((os.path.relpath(p, ROOT), k, m.group(0)[:20]))
        self.assertEqual(bad, [])

    def test_denylist(self):
        dl = os.environ.get("YRD_DENYLIST") or os.path.join(ROOT, ".local_denylist.txt")
        if not os.path.exists(dl):
            self.skipTest("名前の一覧が無い")
        words = [w.strip() for w in open(dl, encoding="utf-8") if w.strip()]
        bad = []
        for p in files():
            t = open(p, encoding="utf-8", errors="replace").read()
            bad += [(os.path.relpath(p, ROOT), w) for w in words if w in t]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()


class NoStorePaths(unittest.TestCase):
    """ボット固有ストアの ID や置き場所を、Git に入るどのファイルにも書かない（GIT_MANIFEST.md も含む）。"""

    def test_no_store_ids(self):
        pat = re.compile("/cursor" + "/stores|store-[0-9a-f]{8}-[0-9a-f]{4}")
        bad = []
        for d, ds, fs in os.walk(ROOT):
            ds[:] = [x for x in ds if x not in SKIP_DIRS and not x.startswith(".")]
            for f in fs:
                if f.startswith(".") or f.endswith((".pyc", ".jsonl", ".db")):
                    continue
                p = os.path.join(d, f)
                with open(p, encoding="utf-8", errors="ignore") as fh:
                    found = pat.search(fh.read())
                if found:
                    bad.append(os.path.relpath(p, ROOT))
        self.assertEqual(bad, [])

REPO_ROOT = os.path.dirname(ROOT)


def repo_files():
    skip_dirs = SKIP_DIRS | {".venv", "node_modules"}
    skip_files = set(SKIP_FILES)
    for d, ds, fs in os.walk(REPO_ROOT):
        ds[:] = [x for x in ds if x not in skip_dirs and not x.startswith(".")]
        for f in fs:
            if f in skip_files or f.startswith(".") or f.endswith((".pyc", ".jsonl", ".db")):
                continue
            yield os.path.join(d, f)


class NoPrivateOrMachinePaths(unittest.TestCase):
    """家族のボットの固有名、箱の絶対パス、箱以外のホームパス、実在しそうなカレンダーIDが Git に戻らないことを見張る。
    試験用の example ドメイン・プレースホルダ・/Users/example は残す。"""

    def _read(self, path):
        return open(path, encoding="utf-8", errors="replace").read()

    def test_no_family_bot_proper_name(self):
        name = "伝書" + "鳩"
        bad = []
        for p in repo_files():
            if os.path.abspath(p) == os.path.abspath(__file__):
                continue
            if name in self._read(p):
                bad.append(os.path.relpath(p, REPO_ROOT))
        self.assertEqual(bad, [])

    def test_no_box_data_absolute_path(self):
        needle = "/home/box/" + "agent-data"
        bad = []
        for p in repo_files():
            if os.path.abspath(p) == os.path.abspath(__file__):
                continue
            if needle in self._read(p):
                bad.append(os.path.relpath(p, REPO_ROOT))
        self.assertEqual(bad, [])

    def test_no_foreign_home_paths(self):
        users = re.compile(r"/Users/([A-Za-z0-9._-]+)")
        homes = re.compile(r"/home/([A-Za-z0-9._-]+)")
        allow_users = {"example", "user"}
        allow_homes = {"box"}
        bad = []
        for p in repo_files():
            if os.path.abspath(p) == os.path.abspath(__file__):
                continue
            text = self._read(p)
            for m in users.finditer(text):
                if m.group(1) in allow_users:
                    continue
                bad.append((os.path.relpath(p, REPO_ROOT), "Users", m.group(0)[:40]))
            for m in homes.finditer(text):
                if m.group(1) in allow_homes:
                    continue
                bad.append((os.path.relpath(p, REPO_ROOT), "home", m.group(0)[:40]))
        self.assertEqual(bad, [])

    def test_no_real_looking_calendar_ids(self):
        cal = re.compile(r"(?<![<\w.-])[A-Za-z0-9._%+-]+@(?:group\.)?calendar\.google\.com")
        bad = []
        for p in repo_files():
            if os.path.abspath(p) == os.path.abspath(__file__):
                continue
            text = self._read(p)
            text = re.sub(r"[A-Za-z0-9._%+-]+@example\.(?:com|test)", "", text, flags=re.I)
            for m in cal.finditer(text):
                bad.append((os.path.relpath(p, REPO_ROOT), m.group(0)[:40]))
        self.assertEqual(bad, [])
