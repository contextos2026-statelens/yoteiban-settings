#!/usr/bin/env python3
"""Fail when a change touches more than one logical skill across mirrors."""
from __future__ import annotations

import argparse
import subprocess
import sys


def changed_skill_names(paths: list[str]) -> set[str]:
    names = set()
    for path in paths:
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] in {"skills", "redesign"}:
            if parts[0] == "skills":
                names.add(parts[1])
            elif len(parts) >= 4 and parts[1] == "skills":
                names.add(parts[2])
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(argv)
    result = subprocess.run(["git", "diff", "--name-only", "--diff-filter=ACMRT", f"{args.base}...HEAD"],
                            check=False, capture_output=True, text=True)
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    names = changed_skill_names(result.stdout.splitlines())
    if len(names) > 1:
        print("複数のスキルを変更しています: " + ", ".join(sorted(names)), file=sys.stderr)
        return 1
    print("変更スキル: " + (", ".join(sorted(names)) if names else "なし"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
