import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "mac"))
# 第2段階の delegation.py の場所（リポジトリでは tools/stage2/）。環境変数で差し替えられる。
DELEGATION_PY = os.environ.get("YRD_DELEGATION_PY") or next(
    (p for p in (os.path.join(ROOT, "..", "tools", "stage2", "delegation.py"),
                 "/home/box/agent-data/yoteiban-tools/stage2/delegation.py") if os.path.exists(p)), "")
SEIHON = os.environ.get("YRD_SEIHON") or next(
    (p for p in (os.path.join(ROOT, "..", "seihon.md"), "/home/box/agent-data/yoteiban-rules/seihon.md")
     if os.path.exists(p)), "")
