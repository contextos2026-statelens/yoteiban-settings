#!/usr/bin/env python3
"""Create and validate relay-bot update coordination records; never sends messages."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

def request_text(version: str, capabilities: str) -> str:
    return ("更新確認の依頼案（送信前に本人へ表示）\n"
            f"対象バージョン: {version}\n"
            f"確認したい構成・更新権限: {capabilities}\n"
            "この依頼案の作成だけでは送信も適用も行いません。")


def validate_result(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("result must be an object")
    required = {"bundle_version", "target_version", "result", "tests", "rolled_back", "detail_code"}
    if set(value) != required:
        raise ValueError("result fields mismatch")
    if value["result"] not in {"success", "failed", "not_applied"}:
        raise ValueError("invalid result")
    tests = value["tests"]
    if not isinstance(tests, dict) or set(tests) != {"fixture_cases", "passed", "failed"}:
        raise ValueError("invalid test summary")
    if any(type(tests[k]) is not int or tests[k] < 0 for k in tests):
        raise ValueError("invalid test counts")
    if tests["passed"] + tests["failed"] > tests["fixture_cases"]:
        raise ValueError("test totals exceed cases")
    if not isinstance(value["rolled_back"], bool):
        raise ValueError("invalid rollback flag")
    if not all(isinstance(value[k], str) for k in ("bundle_version", "target_version", "detail_code")):
        raise ValueError("invalid text field")
    return value


def verify_manifest(root: Path) -> list[str]:
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("manifest has no files")
    errors = []
    for relative, expected in files.items():
        path = (root / relative).resolve()
        if root.resolve() not in path.parents:
            errors.append("PATH_OUTSIDE_BUNDLE")
            continue
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            errors.append("FILE_MISSING")
            continue
        if actual != expected:
            errors.append("HASH_MISMATCH")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    req = sub.add_parser("request")
    req.add_argument("--version", required=True)
    req.add_argument("--capabilities", required=True)
    result = sub.add_parser("validate-result")
    result.add_argument("file", type=Path)
    verify = sub.add_parser("verify-manifest")
    verify.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "request":
            print(request_text(args.version, args.capabilities))
        elif args.command == "validate-result":
            print(json.dumps(validate_result(json.loads(args.file.read_text())), ensure_ascii=False))
        else:
            errors = verify_manifest(args.directory)
            if errors:
                print("manifest: failed (" + ",".join(errors) + ")")
                return 1
            print("manifest: verified")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
