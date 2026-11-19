"""道具の置き場は環境変数・設定・リポジトリの既定から決まり、箱の道を直書きしない。"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import _paths  # noqa
from yoteiban_rd.tool_paths import load_tools, resolve_lib_home, resolve_queue, resolve_seihon

REPO = Path(_paths.ROOT).parent
SEIHON = REPO / "rules" / "seihon.md"
LIB = Path(_paths.ROOT) / "lib"


class ToolPaths(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (
            "YOTEIBAN_SEIHON", "YRD_SEIHON", "YOTEIBAN_REDESIGN_LIB", "YRD_REDESIGN_LIB",
            "YOTEIBAN_QUEUE", "YRD_QUEUE", "YOTEIBAN_SETTINGS", "YRD_SETTINGS",
        )}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _clear(self):
        for k in self._saved:
            os.environ.pop(k, None)

    def test_seihon_defaults_to_repo_rules(self):
        self._clear()
        self.assertEqual(Path(resolve_seihon()).resolve(), SEIHON.resolve())

    def test_seihon_env_wins(self):
        self._clear()
        os.environ["YOTEIBAN_SEIHON"] = "/tmp/example-seihon.md"
        self.assertEqual(resolve_seihon(), "/tmp/example-seihon.md")

    def test_settings_file(self):
        self._clear()
        with tempfile.TemporaryDirectory() as td:
            settings = Path(td) / "settings.json"
            settings.write_text(json.dumps({
                "tools": {
                    "seihon": "/tmp/from-settings/seihon.md",
                    "redesign_lib": "/tmp/from-settings/lib",
                    "queue": "/tmp/from-settings/processing.jsonl",
                }
            }), encoding="utf-8")
            os.environ["YRD_SETTINGS"] = str(settings)
            self.assertEqual(resolve_seihon(), "/tmp/from-settings/seihon.md")
            self.assertEqual(resolve_lib_home(), "/tmp/from-settings/lib")
            self.assertEqual(resolve_queue(), Path("/tmp/from-settings/processing.jsonl"))

    def test_lib_home_defaults_to_package(self):
        self._clear()
        self.assertEqual(Path(resolve_lib_home()).resolve(), LIB.resolve())

    def test_queue_generic_default(self):
        self._clear()
        self.assertEqual(resolve_queue(), Path("processing.jsonl"))

    def test_source_has_no_box_path(self):
        text = Path(_paths.ROOT, "lib", "yoteiban_rd", "tool_paths.py").read_text(encoding="utf-8")
        self.assertNotIn("/home/box/" + "agent-data", text)


if __name__ == "__main__":
    unittest.main()
