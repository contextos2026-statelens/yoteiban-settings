"""Gitに入れるファイルに固有値（アドレス・番号・ID類・名前）が無いことを確かめる。
名前の一覧はGitに入れず、箱の外の一覧（YRD_DENYLIST）があるときだけ使う。"""
import os, re, unittest
import _paths  # noqa

ROOT = _paths.ROOT
SKIP_DIRS = {"findings", "__pycache__", ".git"}
SKIP_FILES = {"GIT_MANIFEST.md"}  # ファイルの照合値（16進）の一覧なので番号の形に当たる
PATTERNS = {
    "メール": re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
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
                if pat.search(open(p, encoding="utf-8", errors="ignore").read()):
                    bad.append(os.path.relpath(p, ROOT))
        self.assertEqual(bad, [])
