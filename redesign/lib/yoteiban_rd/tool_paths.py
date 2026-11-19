"""道具の置き場を、環境変数または設定ファイルから決める。

Git の本文には箱の絶対パスを書かない。場所は次の順で決める。
1. 専用の環境変数（YOTEIBAN_* または同じ意味の YRD_*）
2. 環境変数 YOTEIBAN_SETTINGS / YRD_SETTINGS が指す設定ファイルの tools
3. リポジトリの既定（このファイルから見た rules/seihon.md や、lib 自身）
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_LIB_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _LIB_DIR.parent.parent


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def load_tools() -> dict[str, Any]:
    path = _first_env("YOTEIBAN_SETTINGS", "YRD_SETTINGS")
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    tools = data.get("tools") if isinstance(data, dict) else None
    return tools if isinstance(tools, dict) else {}


def resolve_seihon() -> str:
    value = _first_env("YOTEIBAN_SEIHON", "YRD_SEIHON")
    if value:
        return value
    tools = load_tools()
    if tools.get("seihon"):
        return str(tools["seihon"])
    repo = _REPO_ROOT / "rules" / "seihon.md"
    if repo.is_file():
        return str(repo)
    return "rules/seihon.md"


def resolve_lib_home() -> str:
    value = _first_env("YOTEIBAN_REDESIGN_LIB", "YRD_REDESIGN_LIB")
    if value:
        return value
    tools = load_tools()
    if tools.get("redesign_lib"):
        return str(tools["redesign_lib"])
    return str(_LIB_DIR)


def resolve_queue() -> Path:
    value = _first_env("YOTEIBAN_QUEUE", "YRD_QUEUE")
    if value:
        return Path(value)
    tools = load_tools()
    if tools.get("queue"):
        return Path(str(tools["queue"]))
    if tools.get("state"):
        return Path(str(tools["state"])) / "processing.jsonl"
    return Path("processing.jsonl")
