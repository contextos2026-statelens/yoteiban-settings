"""アップデート後の追加設定：版ごとの足した質問の差を出す。個人の値は扱わない。"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

TENTATIVE_MARK = "仮（未回答）"
PREV_SUFFIX = "（前の値）"
SAID_RULE_KEYS = frozenset({"家用の見分けの表", "本人が言ったルール"})
# 人ごとの一覧にあるキーも上書き禁止。判定は stated_rules.protect_key。

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(value: str) -> tuple[int, int, int]:
    text = (value or "").strip()
    match = _VERSION_RE.fullmatch(text)
    if not match:
        raise ValueError(f"版の番号の形ではありません: {value!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def version_less(left: str, right: str) -> bool:
    return parse_version(left) < parse_version(right)


def version_less_or_equal(left: str, right: str) -> bool:
    return parse_version(left) <= parse_version(right)


def default_catalog_path() -> Path:
    env = os.environ.get("YOTEIBAN_SETUP_QUESTIONS") or os.environ.get("YRD_SETUP_QUESTIONS")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "docs" / "setup_questions.json"


def load_catalog(path: str | Path | None = None) -> dict:
    catalog_path = Path(path) if path else default_catalog_path()
    try:
        with catalog_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"質問の一覧を読み込めません: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("質問の一覧はJSONオブジェクトである必要があります")
    required = {"baseline_version", "releases", "questions"}
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError("質問の一覧に不足しているキー: " + ", ".join(missing))
    parse_version(data["baseline_version"])
    if not isinstance(data["releases"], list) or not isinstance(data["questions"], list):
        raise ValueError("releases と questions は配列である必要があります")
    seen: set[str] = set()
    for item in data["questions"]:
        if not isinstance(item, dict) or "id" not in item or "added_in" not in item or "memory_key" not in item:
            raise ValueError("質問の項目に id・added_in・memory_key が必要です")
        if "aliases" in item and (
            not isinstance(item["aliases"], list)
            or any(not isinstance(alias, str) for alias in item["aliases"])
        ):
            raise ValueError("aliases は文字列の配列である必要があります")
        if "answer_keys" in item and (
            not isinstance(item["answer_keys"], list)
            or not item["answer_keys"]
            or any(not isinstance(key, str) for key in item["answer_keys"])
        ):
            raise ValueError("answer_keys は空でない文字列の配列である必要があります")
        parse_version(item["added_in"])
        if item["id"] in seen:
            raise ValueError(f"質問の id が重複しています: {item['id']}")
        seen.add(item["id"])
    for rel in data["releases"]:
        if not isinstance(rel, dict) or "version" not in rel:
            raise ValueError("releases の各要素に version が必要です")
        parse_version(rel["version"])
        ids = rel.get("added_question_ids", [])
        if not isinstance(ids, list):
            raise ValueError("added_question_ids は配列である必要があります")
        unknown = [qid for qid in ids if qid not in seen]
        if unknown:
            raise ValueError("一覧に無い質問 id です: " + ", ".join(unknown))
        expected = {q["id"] for q in data["questions"] if q["added_in"] == rel["version"]}
        listed = set(ids)
        if expected != listed:
            raise ValueError(
                f"{rel['version']} の added_question_ids が一覧の added_in と一致しません"
            )
    return data


def resolved_completed_version(catalog: dict, completed: str | None) -> str:
    text = (completed or "").strip()
    if not text:
        return catalog["baseline_version"]
    parse_version(text)
    return text


def questions_added_since(
    catalog: dict, completed: str | None, current: str
) -> list[dict]:
    done = resolved_completed_version(catalog, completed)
    parse_version(current)
    if not version_less(done, current):
        return []
    added = [
        item
        for item in catalog["questions"]
        if version_less(done, item["added_in"]) and version_less_or_equal(item["added_in"], current)
    ]
    return added


def questions_up_to(catalog: dict, current: str) -> list[dict]:
    """Return every catalog question whose added_in is no later than current."""
    parse_version(current)
    return [
        item
        for item in catalog["questions"]
        if version_less_or_equal(item["added_in"], current)
    ]


def is_tentative(value: object) -> bool:
    return isinstance(value, str) and TENTATIVE_MARK in value


def already_answered(memory: dict, key: str) -> bool:
    if key not in memory:
        return False
    value = memory[key]
    if value is None or value == "":
        return False
    return not is_tentative(value)


def pending_questions(
    questions: list[dict],
    memory: dict | None = None,
    connected: set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    mem = memory or {}
    linked = set(connected or ())
    pending: list[dict] = []
    for item in questions:
        keys = [item["memory_key"], *item.get("aliases", [])]
        answered = any(already_answered(mem, key) or is_tentative(mem.get(key)) for key in keys)
        answer_keys = item.get("answer_keys", [])
        if answer_keys and all(already_answered(mem, key) for key in answer_keys):
            answered = True
        if answered:
            continue
        # 条件付き質問は親の回答が確定してからだけ提示する。falseなら枝を閉じる。
        condition = item.get("when")
        if condition:
            clauses = condition if isinstance(condition, list) else [condition]
            eligible = True
            unresolved = False
            for clause in clauses:
                if not isinstance(clause, dict) or not clause.get("key"):
                    raise ValueError("when は key/equals を持つ条件である必要があります")
                key = clause["key"]
                if not already_answered(mem, key):
                    unresolved = True
                    break
                expected = clause.get("equals")
                values = expected if isinstance(expected, list) else [expected]
                actual = mem[key]
                if actual not in values:
                    eligible = False
                    break
            if unresolved or not eligible:
                continue
        alternatives = item.get("when_any")
        if alternatives:
            outcomes = []
            for clause in alternatives:
                key = clause.get("key", "")
                if not key or not already_answered(mem, key):
                    outcomes.append(None)
                    continue
                expected = clause.get("equals")
                values = expected if isinstance(expected, list) else [expected]
                outcomes.append(mem[key] in values)
            if True not in outcomes:
                continue
        integration = item.get("integration")
        if integration and integration in linked:
            continue
        pending.append(item)
    return pending


def default_memory_value(question: dict) -> str:
    default = question.get("default")
    if default is None or default == "":
        return TENTATIVE_MARK
    return f"{default}：{TENTATIVE_MARK}"


def record_answer(memory: dict, key: str, new_value: str) -> dict:
    updated = dict(memory)
    old = updated.get(key)
    if old and old != new_value:
        updated[f"{key}{PREV_SUFFIX}"] = old
    updated[key] = new_value
    return updated


def revert_answer(memory: dict, key: str) -> tuple[dict, bool]:
    updated = dict(memory)
    prev_key = f"{key}{PREV_SUFFIX}"
    if prev_key not in updated:
        return updated, False
    updated[key] = updated.pop(prev_key)
    return updated, True


def is_said_rule_key(key: str) -> bool:
    if key in SAID_RULE_KEYS:
        return True
    return key.startswith("本人が言った")


def may_learn_overwrite(key: str, question: dict | None = None) -> bool:
    if is_said_rule_key(key):
        return False
    if question and question.get("said_rule"):
        return False
    return True


def declined_unavailable_text(question: dict) -> str:
    return str(question.get("unavailable_if_declined") or "")


def build_report(
    catalog: dict,
    completed: str | None,
    current: str,
    memory: dict | None = None,
    connected: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict:
    added = questions_added_since(catalog, completed, current)
    all_current_questions = questions_up_to(catalog, current)
    pending = pending_questions(all_current_questions, memory, connected)
    added_ids = {item["id"] for item in added}
    pending = [item for item in pending if item["id"] in added_ids] + [
        item for item in pending if item["id"] not in added_ids
    ]
    completed_text = (completed or "").strip()
    additional_setup_required = (
        not completed_text
        or version_less(completed_text, current)
        or bool(pending)
    )
    return {
        "completed_version": resolved_completed_version(catalog, completed),
        "current_version": current,
        "additional_setup_required": additional_setup_required,
        "added": [item["id"] for item in added],
        "pending": [item["id"] for item in pending],
        "questions": pending,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="追加設定で聞く質問の差を出す")
    parser.add_argument("--completed-version", default="", help="記憶の完了した設定の版。空なら基準の版")
    parser.add_argument("--current-version", required=True, help="今の Git の VERSION")
    parser.add_argument("--catalog", default="", help="質問一覧の道。空なら既定")
    parser.add_argument("--memory-json", default="{}", help="記憶の値（JSONオブジェクト）。個人の値の試験には使わない")
    parser.add_argument("--connected", default="", help="すでにつながっている連携の id（カンマ区切り）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    catalog = load_catalog(args.catalog or None)
    try:
        memory = json.loads(args.memory_json)
    except json.JSONDecodeError as exc:
        print(f"memory-json を読めません: {exc}", file=sys.stderr)
        return 2
    if not isinstance(memory, dict):
        print("memory-json はオブジェクトである必要があります", file=sys.stderr)
        return 2
    connected = {part.strip() for part in args.connected.split(",") if part.strip()}
    report = build_report(catalog, args.completed_version, args.current_version, memory, connected)
    # 質問の本文に個人値は無いが、出し過ぎないよう id を主にする
    out = {
        "completed_version": report["completed_version"],
        "current_version": report["current_version"],
        "added": report["added"],
        "pending": report["pending"],
        "questions": [
            {
                "id": item["id"],
                "title": item.get("title", ""),
                "memory_key": item["memory_key"],
                "answer_keys": item.get("answer_keys", []),
                "integration": item.get("integration", ""),
                "unavailable_if_declined": item.get("unavailable_if_declined", ""),
                "default": item.get("default", ""),
            }
            for item in report["questions"]
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
