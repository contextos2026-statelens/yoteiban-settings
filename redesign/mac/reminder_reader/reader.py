"""Forward only safe Reminders notification signals through the existing waker."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from yoteiban_watch.heartbeat import ping
from yoteiban_watch.waker import wake

DB_RELATIVE = "Library/Group Containers/group.com.apple.usernoted/db2/db"
REMINDERS_BUNDLE = "com.apple.reminders"
MAC_EPOCH = 978307200


def stable_ref(raw_id, title: str, received_at: str) -> str:
    if isinstance(raw_id, bytes) and len(raw_id) == 16:
        raw_id = str(uuid.UUID(bytes=raw_id))
    try:
        identifier = str(uuid.UUID(str(raw_id))).lower()
    except (ValueError, TypeError, AttributeError):
        identifier = hashlib.sha256(f"{title}\0{received_at}".encode()).hexdigest()[:24]
    return "notif:" + identifier


def read_fixture_or_db(path: str | Path, after: int = 0) -> tuple[int, list[dict]]:
    """Read notification rows read-only; fixtures use the same record/app schema."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=3) as conn:
        conn.execute("PRAGMA query_only=ON")
        high = conn.execute("SELECT COALESCE(MAX(rec_id),0) FROM record").fetchone()[0]
        rows = conn.execute(
            "SELECT r.rec_id,r.uuid,r.data,r.delivered_date FROM record r "
            "JOIN app a USING(app_id) WHERE a.identifier=? AND r.rec_id>? AND r.rec_id<=? "
            "ORDER BY r.rec_id", (REMINDERS_BUNDLE, after, high)
        ).fetchall()
    notices = []
    for rec_id, raw_id, data, delivered in rows:
        try:
            obj = plistlib.loads(data)
            title = obj.get("req", {}).get("titl")
            if not isinstance(title, str) or not title.strip():
                continue
            moment = datetime.fromtimestamp(float(delivered) + MAC_EPOCH, timezone.utc).astimezone()
            notices.append({"rec_id": rec_id, "raw_id": raw_id, "title": title.strip(),
                            "received_at": moment.isoformat(timespec="seconds")})
        except (ValueError, TypeError, plistlib.InvalidFileException, AttributeError, OSError):
            continue
    return high, notices


def payload_for(notice: dict) -> dict:
    title = notice.get("title", "")
    native_ref = stable_ref(notice.get("raw_id"), title, notice["received_at"])
    request_id = "rq-rem-" + hashlib.sha256(native_ref.encode()).hexdigest()[:20]
    return {"kind": "yoteiban-wake", "request_id": request_id,
            "source": "reminder_notification", "native_ref": native_ref,
            "received_at": notice["received_at"], "title": title}


class Reader:
    def __init__(self, config: dict, state_path: str | Path, *, read=read_fixture_or_db,
                 wake_fn=wake, heartbeat_fn=ping, sleep=time.sleep, now=None):
        self.config, self.state_path = config, Path(state_path)
        self.read, self.wake_fn, self.heartbeat_fn = read, wake_fn, heartbeat_fn
        self.sleep, self.now = sleep, now or (lambda: datetime.now().astimezone())

    def _state(self):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(state.get("cursor"), int) and isinstance(state.get("sent"), list):
                return state
        except (OSError, ValueError, AttributeError):
            pass
        high, _ = self.read(self.config["notification_db"], 0)
        state = {"cursor": high, "sent": []}
        self._save(state)
        return state

    def _save(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.state_path)

    def _heartbeat(self, ok: bool) -> bool:
        dedicated = bool(self.config.get("reader_heartbeat_url"))
        url = self.config.get("reader_heartbeat_url") or self.config.get("heartbeat_url", "")
        delivered = self.heartbeat_fn(url, ok)
        state_path = Path(self.config.get("state_dir") or self.state_path.parent) / "heartbeat_reader.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": self.now().isoformat(), "ok": bool(ok and delivered),
                                   "dedicated": dedicated}, ensure_ascii=False), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(state_path)
        return bool(ok and delivered)

    def once(self) -> dict:
        try:
            state = self._state()
            high, notices = self.read(self.config["notification_db"], state["cursor"])
            for notice in notices:
                payload = payload_for(notice)
                native_ref = payload["native_ref"]
                if native_ref not in state["sent"]:
                    age = abs((self.now() - datetime.fromisoformat(notice["received_at"])).total_seconds())
                    if age <= int(self.config.get("max_age_s", 180)):
                        result = self.wake_fn(self.config.get("webhook_url", ""),
                                              os.environ.get(self.config.get("webhook_key_env", "YOTEIBAN_WEBHOOK_KEY"), ""),
                                              payload,
                                              authorization_header=os.environ.get("YOTEIBAN_WEBHOOK_AUTH"))
                        if result.get("state") != "起こし済み" or result.get("http") != 200:
                            raise RuntimeError("wake_failed")
                        state["sent"].append(native_ref)
                        state["sent"] = state["sent"][-10000:]
                state["cursor"] = notice["rec_id"]
                self._save(state)
            state["cursor"] = max(state["cursor"], high)
            self._save(state)
            self._heartbeat(True)
            return {"ok": True, "sent": len(notices)}
        except Exception:
            try:
                self._heartbeat(False)
            except Exception:
                pass
            return {"ok": False, "sent": 0}

    def run(self, interval_s=5):
        while True:
            result = self.once()
            self.sleep(interval_s if result["ok"] else max(interval_s, 30))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    cfg["notification_db"] = str(Path.home() / DB_RELATIVE)
    state_path = Path.home() / "yoteiban-watch" / "state" / "reminder-notification-reader.json"
    Reader(cfg, state_path).run()


if __name__ == "__main__":
    main()
