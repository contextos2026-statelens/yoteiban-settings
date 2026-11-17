"""見張り番の本体。python3 -m yoteiban_watch.run --config <設定> [--once] [--shadow]
--shadow：起こさずに、起こすはずだったものを記録だけする（並べて試す期間用）。"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .sorter import AIClient, MORNING, NONE, WAKE, sort_item
from .sources import SourceError, read_calendar_changes, read_gmail, read_imessage
from .store import Store
from .waker import wake

_stop = False


def _on_term(*_):
    global _stop
    _stop = True


def load_config(path: str) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    for k in ("state_dir", "sources"):
        if k not in cfg:
            raise SystemExit(f"設定に {k} がありません")
    cfg["state_dir"] = os.path.expanduser(cfg["state_dir"])
    im = cfg["sources"].get("imessage")
    if im and im.get("db_path"):
        im["db_path"] = os.path.expanduser(im["db_path"])
    return cfg


def log(cfg: dict, rec: dict) -> None:
    rec = {"at": datetime.now(timezone.utc).isoformat(), **rec}
    p = Path(cfg["state_dir"]) / "watch.log.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def role_of(cfg: dict, inc) -> str:
    if inc.from_me:
        return "本人"
    roles = cfg.get("roles", {})  # 送り主の識別子 → 役割（家族・友だち・学校など）
    return roles.get(inc.sender) or roles.get(inc.chat) or "登録外"


def poll_once(cfg: dict, store: Store, ai: AIClient | None, shadow: bool, wake_fn=wake) -> dict:
    stats = {"read": 0, "new": 0, WAKE: 0, MORNING: 0, NONE: 0, "unreadable": []}
    src = cfg["sources"]
    batches = []
    if src.get("imessage", {}).get("enabled"):
        cur = int(store.cursor("imessage") or src["imessage"].get("start_rowid", 0))
        try:
            items = read_imessage(src["imessage"]["db_path"], cur)
            batches.append(("imessage", items, str(max([int(i.native_id) for i in items], default=cur))))
        except SourceError as e:
            stats["unreadable"].append(("imessage", str(e)))
    if src.get("gmail", {}).get("enabled"):
        try:
            items = read_gmail(src["gmail"]["cmd"], store.cursor("gmail"))
            batches.append(("gmail", items, max([i.at for i in items], default=store.cursor("gmail") or "")))
        except SourceError as e:
            stats["unreadable"].append(("gmail", str(e)))
    if src.get("calendar", {}).get("enabled"):
        try:
            known = json.loads(store.cursor("calendar") or "{}")
            items, now_map = read_calendar_changes(src["calendar"]["cmd"], known)
            batches.append(("calendar", items, json.dumps(now_map)))
        except SourceError as e:
            stats["unreadable"].append(("calendar", str(e)))
    month = datetime.now().strftime("%Y-%m")
    cap = float(cfg.get("ai_monthly_cap_yen", 0))
    for source, items, new_cursor in batches:
        for inc in items:
            stats["read"] += 1
            role = role_of(cfg, inc)
            rid, created = store.add(source, inc.native_id, role)
            if not created:
                continue
            stats["new"] += 1
            r = sort_item(inc.text, role, source, muted_roles=set(cfg.get("muted_roles", [])),
                          names=cfg.get("mask_names", {}), ai=ai, cap_left_yen=cap - store.month_cost(month))
            if r.yen:
                store.add_cost(month, r.yen)
            store.set_state(rid, "仕分け済み", sort_json=r.__dict__)
            stats[r.decision] += 1
            if r.decision == WAKE:
                payload = {"request_id": rid, "source": source, "ref": inc.native_id, "role": role,
                           "sort": {"by": r.by, "related": r.related, "urgent": r.urgent, "to_owner": r.to_owner},
                           "received_at": inc.at, "kind": "yoteiban-wake"}
                if shadow:
                    log(cfg, {"shadow_wake": payload})
                    store.set_state(rid, "朝へ", wake_status="shadow")
                else:
                    res = wake_fn(cfg.get("webhook_url") or os.environ.get("YOTEIBAN_WEBHOOK_URL", ""),
                                  os.environ.get("YOTEIBAN_WEBHOOK_KEY"), payload)
                    store.set_state(rid, res["state"], wake_status=res, attempt=True)
                    log(cfg, {"wake": rid, **res})
            else:
                store.set_state(rid, "朝へ" if r.decision == MORNING else "何もしない")
        # 全件を記録してから読み位置を進める（途中で止まっても取りこぼさない）
        store.set_cursor(source, new_cursor)
    if stats["unreadable"]:
        log(cfg, {"unreadable": stats["unreadable"]})
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--shadow", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    Path(cfg["state_dir"]).mkdir(parents=True, exist_ok=True)
    lock = open(Path(cfg["state_dir"]) / "watch.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("もう動いています", file=sys.stderr)
        return 1
    store = Store(Path(cfg["state_dir"]) / "watch.db")
    ai_cfg = cfg.get("ai") or {}
    ai = AIClient(ai_cfg["url"], ai_cfg["model"], os.environ.get("YOTEIBAN_AI_KEY"),
                  ai_cfg.get("timeout_s", 6.0), ai_cfg.get("yen_per_1k_in", 0.03),
                  ai_cfg.get("yen_per_1k_out", 0.12)) if ai_cfg.get("enabled") else None
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    interval = float(cfg.get("interval_s", 3))
    while True:
        st = poll_once(cfg, store, ai, a.shadow or cfg.get("shadow", False))
        if st["new"]:
            log(cfg, {"poll": st})
        if a.once or _stop:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
