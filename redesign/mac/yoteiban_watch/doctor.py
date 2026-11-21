"""Connection diagnostics. Network effects require an explicit per-run approval flag."""
from __future__ import annotations

import argparse
import json
import os
import shlex
from datetime import datetime, timezone
from pathlib import Path

from yoteiban_core.receiver_status import update_status
from .heartbeat import ping
from .waker import build_signed_body, wake

STAGES = ("preflight", "configuration", "signature", "webhook", "heartbeat", "reader")


def check_stage(cfg: dict, stage: str, *, owner_approved: bool = False,
                owner_confirmed_delivery: bool = False, send_fn=wake, ping_fn=ping,
                now: datetime | None = None) -> dict:
    if stage not in STAGES:
        raise ValueError("unknown stage")
    now = now or datetime.now(timezone.utc)
    state_dir = Path(cfg["state_dir"]).expanduser()
    route = {"preflight":"mac_preflight", "configuration":"webhook", "signature":"webhook",
             "webhook":"webhook", "heartbeat":"heartbeat_watch", "reader":"reminder_reader"}[stage]
    state, detail = "接続成功", "OK"
    step = stage
    if stage == "preflight":
        try:
            data=json.loads((state_dir/"preflight.json").read_text(encoding="utf-8"))
            if not data.get("checks",{}).get("config_valid",False):
                state,detail="再確認が必要","PREFLIGHT_CONFIG_INVALID"
        except (OSError,ValueError): state,detail="本人の操作待ち","PREFLIGHT_NOT_RUN"
    elif stage == "configuration":
        env_ok=bool(os.environ.get("YOTEIBAN_WEBHOOK_KEY"))
        url_ok=bool(cfg.get("webhook_url") or os.environ.get("YOTEIBAN_WEBHOOK_URL"))
        if not env_ok or not url_ok: state,detail="本人の操作待ち","WEBHOOK_CONFIG_MISSING"
    elif stage == "signature":
        try:
            for envelope in (False,True):
                body,signature=build_signed_body({"kind":"yoteiban-doctor","request_id":"fixture"},"fixture-key",envelope=envelope)
                if not body or len(signature)!=64: raise ValueError
        except Exception: state,detail="接続失敗","SIGNATURE_SELF_TEST_FAILED"
    elif stage == "webhook":
        if not owner_approved: state,detail="本人の操作待ち","OWNER_APPROVAL_REQUIRED"
        else:
            url=cfg.get("webhook_url") or os.environ.get("YOTEIBAN_WEBHOOK_URL","")
            key=os.environ.get("YOTEIBAN_WEBHOOK_KEY","")
            if not url or not key: state,detail="本人の操作待ち","WEBHOOK_CONFIG_MISSING"
            else:
                payload={"kind":"yoteiban-wake","request_id":"doctor:"+now.strftime("%Y%m%dT%H%M%SZ"),
                         "source":"yoteiban_doctor","native_ref":"fixture:"+now.strftime("%Y%m%dT%H%M%SZ"),
                         "received_at":now.isoformat(),"body_text":"架空の接続試験","sender_role":"試験"}
                result=send_fn(url,key,payload,authorization_header=os.environ.get("YOTEIBAN_WEBHOOK_AUTH"))
                if result.get("delivery_state")!="success":
                    state,detail="接続失敗",str(result.get("detail_code") or "WEBHOOK_DELIVERY_FAILED")
                elif result.get("registration_state")!="registered":
                    state,detail="接続テスト待ち","LEDGER_REGISTRATION_UNVERIFIED"
    elif stage == "heartbeat":
        if owner_confirmed_delivery:
            try:
                prior=json.loads((state_dir/"integration_status.json").read_text(encoding="utf-8"))
                entry=prior.get("connections",{}).get(route,{})
                if entry.get("detail_code")=="WAITING_FOR_OWNER_NOTIFICATION_CONFIRMATION":
                    state,detail="接続成功","OWNER_CONFIRMED_NOTIFICATION_DELIVERY"
                else: state,detail="本人の操作待ち","NO_PENDING_HEARTBEAT_CONFIRMATION"
            except (OSError,ValueError): state,detail="本人の操作待ち","NO_PENDING_HEARTBEAT_CONFIRMATION"
        elif not owner_approved: state,detail="本人の操作待ち","OWNER_APPROVAL_REQUIRED"
        else:
            url=cfg.get("heartbeat_url","")
            if not url: state,detail="本人の操作待ち","HEARTBEAT_URL_MISSING"
            elif not ping_fn(url,False): state,detail="接続失敗","HEARTBEAT_FAIL_PING_FAILED"
            elif not owner_confirmed_delivery: state,detail="接続テスト待ち","WAITING_FOR_OWNER_NOTIFICATION_CONFIRMATION"
    elif stage == "reader":
        try:
            hb=json.loads((state_dir/"heartbeat_reader.json").read_text(encoding="utf-8"))
            at=datetime.fromisoformat(hb["at"])
            if (now-at).total_seconds()>max(180,int(cfg.get("interval_s",5))*3):
                state,detail="再確認が必要","READER_HEARTBEAT_STALE"
            elif not hb.get("ok"):
                state,detail="接続失敗","READER_HEARTBEAT_FAILED"
            elif not hb.get("dedicated"):
                state,detail="接続テスト待ち","READER_HEARTBEAT_SHARED"
        except (OSError,ValueError,KeyError,TypeError): state,detail="本人の操作待ち","READER_HEARTBEAT_NOT_FOUND"
    update_status(state_dir/"integration_status.json",route,state,step=step,detail_code=detail,at=now.isoformat())
    return {"stage":stage,"state":state,"detail_code":detail}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="ローカル接続状態を診断します。本文やURLは出力しません。")
    parser.add_argument("--config",required=True)
    parser.add_argument("--only",choices=STAGES)
    parser.add_argument("--send-webhook-test",action="store_true",help="架空の1件を受け口へ送ることを本人が許可した")
    parser.add_argument("--send-heartbeat-fail",action="store_true",help="監視先へ/fail通知を送ることを本人が許可した")
    parser.add_argument("--confirm-heartbeat-delivery",action="store_true",help="fail通知が届いたことを本人が確認した")
    args=parser.parse_args(argv)
    cfg=json.loads(Path(args.config).expanduser().read_text(encoding="utf-8"))
    # Load only the expected webhook environment keys without evaluating the env file.
    env_path=Path(args.config).expanduser().parent/"env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped=line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped: continue
            key,value=stripped.split("=",1)
            if key.strip() not in {"YOTEIBAN_WEBHOOK_URL","YOTEIBAN_WEBHOOK_KEY","YOTEIBAN_WEBHOOK_AUTH"}: continue
            try: parsed=shlex.split(value.strip(),comments=True,posix=True)
            except ValueError: continue
            if len(parsed)==1: os.environ.setdefault(key.strip(),parsed[0])
    except OSError:
        pass
    stages=(args.only,) if args.only else STAGES
    results=[check_stage(cfg,stage,owner_approved=((stage=="webhook" and args.send_webhook_test) or
                                                   (stage=="heartbeat" and args.send_heartbeat_fail)),
                         owner_confirmed_delivery=args.confirm_heartbeat_delivery) for stage in stages]
    for row in results: print(f"{row['stage']}: {row['state']} ({row['detail_code']})")
    return 0 if all(row["state"] in {"接続成功","接続テスト待ち","本人の操作待ち"} for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
