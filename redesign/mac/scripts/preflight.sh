#!/usr/bin/env bash
set -euo pipefail

MAC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/yoteiban-watch"
PYTHON_BIN="$(bash "$MAC_DIR/scripts/select_python.sh" "$DEST")"
DOMAIN="gui/$(id -u)"

"$PYTHON_BIN" - "$DEST" "$PYTHON_BIN" "$DOMAIN" <<'PY'
import json, os, pathlib, platform, re, subprocess, sys
dest=pathlib.Path(sys.argv[1]); python_bin=sys.argv[2]; domain=sys.argv[3]
state_dir=dest/'state'; state_dir.mkdir(parents=True, exist_ok=True)
checks={
 "macos_version": subprocess.run(["sw_vers","-productVersion"],capture_output=True,text=True).stdout.strip() or "unknown",
 "python_version": platform.python_version(),
 "python_executable_valid": pathlib.Path(python_bin).is_file(),
 "venv_present": (dest/'venv/bin/python').is_file(),
 "launchctl_available": bool(subprocess.run(["/bin/bash","-lc","command -v launchctl >/dev/null"],capture_output=True).returncode == 0),
 "watch_loaded": subprocess.run(["launchctl","print",f"{domain}/com.yoteiban.watch"],capture_output=True).returncode == 0,
 "clock_loaded": subprocess.run(["launchctl","print",f"{domain}/com.yoteiban.clock"],capture_output=True).returncode == 0,
 "legacy_reader_loaded": subprocess.run(["launchctl","print",f"{domain}/com.yoteiban.reminder-notifications"],capture_output=True).returncode == 0,
 "config_present": (dest/'config.json').is_file(),
 "env_present": (dest/'env').is_file(),
}
labels=subprocess.run(["launchctl","list"],capture_output=True,text=True).stdout
checks["yoteiban_job_count"]=sum(1 for line in labels.splitlines() if "com.yoteiban." in line)
checks["other_bot_label_count"]=sum(1 for line in labels.splitlines() if re.search(r"\bcom\.[^\s]*bot[^\s]*\b",line,re.I))
bot_roots=[pathlib.Path.home()/"Applications", pathlib.Path.home()/"Library"/"Application Support"]
checks["other_bot_folder_count"]=sum(1 for root in bot_roots if root.is_dir() for item in root.iterdir()
                                    if item.is_dir() and re.search(r"bot",item.name,re.I))
config_path=dest/'config.json'
if checks["config_present"]:
 try:
  cfg=json.loads(config_path.read_text(encoding='utf-8'))
  checks["webhook_url_configured"]=bool(cfg.get('webhook_url'))
  checks["heartbeat_url_configured"]=bool(cfg.get('heartbeat_url'))
  checks["reader_heartbeat_url_configured"]=bool(cfg.get('reader_heartbeat_url'))
 except (ValueError,OSError): checks["config_valid"]=False
 else: checks["config_valid"]=True
env_path=dest/'env'; env_keys={}
if checks["env_present"]:
 for line in env_path.read_text(encoding='utf-8',errors='replace').splitlines():
  if not line.strip() or line.lstrip().startswith('#') or '=' not in line: continue
  name,value=line.split('=',1); env_keys[name.strip()]=bool(value.strip().strip('"').strip("'"))
checks["webhook_secret_configured"]=env_keys.get('YOTEIBAN_WEBHOOK_KEY',False)
checks["webhook_auth_configured"]=env_keys.get('YOTEIBAN_WEBHOOK_AUTH',False)
checks["ai_key_configured"]=env_keys.get('YOTEIBAN_AI_KEY',False)
checks["full_disk_access"]="needs_live_read_test"
out={"schema_version":1,"checked_at":__import__('datetime').datetime.now().astimezone().isoformat(),"checks":checks}
target=state_dir/'preflight.json'; tmp=target.with_suffix('.tmp')
tmp.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); os.chmod(tmp,0o600); os.replace(tmp,target)
print("事前点検を保存しました（値・URL・鍵は出力していません）。")
for key,value in checks.items(): print(f"{key}: {value}")
PY
