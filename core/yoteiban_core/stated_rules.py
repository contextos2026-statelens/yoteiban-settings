"""本人が言ったルールの枠。値は人ごとのファイルにだけ置く。Gitには枠だけ。"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

STORE_VERSION = 1
ALLOWED_SOURCES = frozenset({"owner_chat"})
REJECTED_SOURCES = frozenset({"other_person", "input_body", "email", "group_chat", "unknown"})
STATUSES = frozenset({"active", "pending_conflict", "revoked", "rejected"})
SUMMARY_MAX = 80
AREAS = frozenset({
    "household_classify", "notify", "interval", "times", "body_routines", "other",
})


def default_path() -> Path:
    env = os.environ.get("YOTEIBAN_STATED_RULES") or os.environ.get("YRD_STATED_RULES")
    if env:
        return Path(env)
    state = os.environ.get("YOTEIBAN_STATE") or os.environ.get("YRD_STATE")
    if state:
        return Path(state) / "stated_rules.json"
    return Path("stated_rules.json")


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def empty_store() -> dict:
    return {"version": STORE_VERSION, "rules": []}


def load(path: str | Path | None = None) -> dict:
    store_path = Path(path) if path else default_path()
    if not store_path.exists():
        return empty_store()
    try:
        with store_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"言ったルールの一覧を読み込めません: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("言ったルールの一覧の形が違います")
    return data


def save(store: dict, path: str | Path | None = None) -> Path:
    store_path = Path(path) if path else default_path()
    payload = json.dumps(
        {"version": int(store.get("version") or STORE_VERSION), "rules": list(store.get("rules") or [])},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    _atomic_write(store_path, payload.encode("utf-8"), 0o600)
    return store_path


def _next_id(store: dict) -> str:
    used = []
    for item in store.get("rules") or []:
        match = re.fullmatch(r"r-(\d+)", str(item.get("id") or ""))
        if match:
            used.append(int(match.group(1)))
    return f"r-{(max(used) if used else 0) + 1}"


def _clip_summary(summary: str) -> str:
    text = " ".join((summary or "").split())
    if not text:
        raise ValueError("要約が空です")
    if len(text) > SUMMARY_MAX:
        text = text[: SUMMARY_MAX - 1] + "…"
    return text


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def validate_source(source: str) -> str:
    value = (source or "").strip()
    if value not in ALLOWED_SOURCES:
        raise ValueError("本人のチャット以外は、言ったルールの根拠にできません")
    return value


def topic_of(rule: dict) -> tuple[str, str]:
    area = str(rule.get("area") or "other")
    key = str(rule.get("key") or "")
    if key:
        return area, key
    return area, _norm(str(rule.get("summary") or ""))


def active_rules(store: dict) -> list[dict]:
    return [item for item in store.get("rules") or [] if item.get("status") == "active"]


def conflicting(store: dict, rule: dict) -> dict | None:
    topic = topic_of(rule)
    if not topic[1]:
        return None
    for item in active_rules(store):
        if item.get("id") == rule.get("id"):
            continue
        if topic_of(item) == topic and _norm(item.get("summary") or "") != _norm(rule.get("summary") or ""):
            return item
    return None


def list_for_owner(store: dict, include_inactive: bool = False) -> list[dict]:
    """本人に見せる用。日付と要約1行だけ。"""
    out = []
    for item in store.get("rules") or []:
        status = item.get("status") or ""
        if not include_inactive and status != "active":
            continue
        out.append({
            "id": item.get("id"),
            "said_on": item.get("said_on"),
            "summary": item.get("summary"),
            "status": status,
        })
    return out


def protect_key(store: dict, key: str) -> bool:
    """この記憶のキーは、学習や既定で上書きしてはいけない。"""
    if not key:
        return False
    from yoteiban_core.setup_delta import is_said_rule_key

    if is_said_rule_key(key):
        return True
    for item in active_rules(store):
        if item.get("key") == key:
            return True
    return False


def prepare(store: dict, *, said_on: str, summary: str, source: str,
            area: str = "other", key: str = "", approved: bool = False) -> dict:
    """保存の前の形を作る。approved が偽なら、まだ書かない。"""
    validate_source(source)
    if area not in AREAS:
        raise ValueError(f"領域の値が違います: {area}")
    day = (said_on or "").strip() or date.today().isoformat()
    date.fromisoformat(day)
    rule = {
        "id": _next_id(store),
        "said_on": day,
        "summary": _clip_summary(summary),
        "area": area,
        "key": key or "",
        "source": "owner_chat",
        "status": "active",
        "replaces": None,
        "asked_conflict": False,
    }
    if not approved:
        rule["status"] = "awaiting_approval"
        return {"ok": False, "reason": "needs_approval", "rule": rule}
    rival = conflicting(store, rule)
    if rival:
        rule["status"] = "pending_conflict"
        rule["replaces"] = rival["id"]
        rule["asked_conflict"] = False
        return {"ok": False, "reason": "conflict", "rule": rule, "existing": rival}
    return {"ok": True, "rule": rule}


def add_approved(store: dict, prepared: dict) -> dict:
    """「はい」のあと。衝突が無ければ active で足す。衝突なら候補として残す。"""
    if prepared.get("reason") == "needs_approval":
        raise ValueError("本人の「はい」の前には保存しません")
    store = {"version": STORE_VERSION, "rules": list(store.get("rules") or [])}
    rule = dict(prepared["rule"])
    if prepared.get("reason") == "conflict":
        rule["status"] = "pending_conflict"
        store["rules"].append(rule)
        return {"store": store, "status": "pending_conflict", "rule": rule, "existing": prepared["existing"]}
    rule["status"] = "active"
    store["rules"].append(rule)
    return {"store": store, "status": "active", "rule": rule}


def resolve_conflict(store: dict, new_id: str, choose: str) -> dict:
    """1回だけ聞く。new＝新しい方、old＝今のまま。"""
    store = {"version": STORE_VERSION, "rules": [dict(item) for item in (store.get("rules") or [])]}
    new = next((item for item in store["rules"] if item.get("id") == new_id), None)
    if not new or new.get("status") != "pending_conflict":
        raise ValueError("衝突の候補が見つかりません")
    old_id = new.get("replaces")
    old = next((item for item in store["rules"] if item.get("id") == old_id), None)
    new["asked_conflict"] = True
    if choose == "new":
        if old and old.get("status") == "active":
            old["status"] = "revoked"
        new["status"] = "active"
    elif choose == "old":
        new["status"] = "rejected"
    else:
        raise ValueError("選び方が違います（new か old）")
    return store


def should_ask_conflict(rule: dict) -> bool:
    return rule.get("status") == "pending_conflict" and not rule.get("asked_conflict")


def revoke(store: dict, rule_id: str) -> dict:
    store = {"version": STORE_VERSION, "rules": [dict(item) for item in (store.get("rules") or [])]}
    found = False
    for item in store["rules"]:
        if item.get("id") == rule_id:
            item["status"] = "revoked"
            found = True
    if not found:
        raise ValueError("そのルールはありません")
    return store


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本人が言ったルールの一覧を扱う（値は人ごとのファイル）")
    parser.add_argument("--path", default="", help="一覧の道。空なら環境変数または stated_rules.json")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="本人に見せる一覧（日付と要約）")
    add = sub.add_parser("add", help="「はい」のあとで足す")
    add.add_argument("--said-on", required=True)
    add.add_argument("--summary", required=True)
    add.add_argument("--area", default="other")
    add.add_argument("--key", default="")
    add.add_argument("--source", default="owner_chat")
    add.add_argument("--approve", action="store_true", help="本人の「はい」があったときだけ付ける")
    res = sub.add_parser("resolve", help="衝突を1回だけ決める")
    res.add_argument("--id", required=True)
    res.add_argument("--choose", required=True, choices=("new", "old"))
    rev = sub.add_parser("revoke", help="取り消す")
    rev.add_argument("--id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    path = args.path or None
    store = load(path)
    if args.cmd == "list":
        print(json.dumps(list_for_owner(store, include_inactive=True), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "add":
        prepared = prepare(
            store,
            said_on=args.said_on,
            summary=args.summary,
            source=args.source,
            area=args.area,
            key=args.key,
            approved=bool(args.approve),
        )
        if not args.approve:
            print(json.dumps({"ok": False, "reason": "needs_approval"}, ensure_ascii=False), file=sys.stderr)
            return 2
        result = add_approved(store, prepared)
        save(result["store"], path)
        out = {"status": result["status"], "id": result["rule"]["id"]}
        if result["status"] == "pending_conflict":
            out["existing_id"] = result["existing"]["id"]
        print(json.dumps(out, ensure_ascii=False))
        return 0 if result["status"] == "active" else 3
    if args.cmd == "resolve":
        store = resolve_conflict(store, args.id, args.choose)
        save(store, path)
        print(json.dumps({"ok": True, "id": args.id, "choose": args.choose}, ensure_ascii=False))
        return 0
    if args.cmd == "revoke":
        store = revoke(store, args.id)
        save(store, path)
        print(json.dumps({"ok": True, "id": args.id, "status": "revoked"}, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
