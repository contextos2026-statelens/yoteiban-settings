"""委任送信の流れ（第6段）。第2段階の delegation.py をそのまま使い、その前後の守りをここで足す。

順番：文案を作る → 本人に文面を見せる → 本人のOK（ボタンの記録）→ 確定 → 1回だけ引き渡す
→ 相手側の確認があれば送信完了 → 返事あり。結果が分からないときは「渡し済み（未確認）」のまま、再送しない。
- 文面の確認は省略しない（delegation.py が「確認省略可」と判定しても、ここでは必ず本人のOKを要る）。
- 実際の送信はしない。引き渡し先（代わりに送るボット・Mac miniのiMessage）への受け渡しは呼び出し側の役目で、
  ここでは「渡した」記録だけを作る。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


class SendFlowError(Exception):
    pass


def resolve_send_prefix(template: str, nickname: str) -> str:
    """Resolve the public default shape with the private owner's chosen nickname."""
    if not isinstance(template, str) or not template.strip():
        raise SendFlowError("送る文の名乗りが未設定です")
    if "〈呼び名〉" in template:
        if not isinstance(nickname, str) or not nickname.strip():
            raise SendFlowError("名乗りに使う呼び名がありません")
        template = template.replace("〈呼び名〉", nickname.strip())
    if "〈" in template or "〉" in template:
        raise SendFlowError("名乗りに未設定の項目があります")
    return template


class SendFlow:
    def __init__(self, delegation_py: str, store: str, actor: str = "yoteiban",
                 required_prefix: str = "") -> None:
        if not Path(delegation_py).exists():
            raise SendFlowError("delegation.py が見つかりません")
        self.py, self.store, self.actor = delegation_py, store, actor
        self.required_prefix = required_prefix

    def _check_prefix(self, shown: dict) -> None:
        if not self.required_prefix:
            raise SendFlowError("送る文の名乗り設定が無いため、送信の道へ渡せません")
        body = ((shown.get("draft") or {}).get("body") if isinstance(shown, dict) else None)
        if not isinstance(body, str) or not body.startswith(self.required_prefix):
            raise SendFlowError("送る文の先頭に設定済みの名乗りがありません")

    def _run(self, *args: str) -> dict:
        p = subprocess.run([sys.executable, self.py, "--file", self.store, "--actor", self.actor, *args],
                           capture_output=True, text=True, timeout=30)
        out = (p.stdout or "").strip()
        try:
            data = json.loads(out) if out else {}
        except json.JSONDecodeError:
            data = {"raw": out}
        data["_rc"] = p.returncode
        if p.returncode != 0:
            data["_err"] = (p.stderr or "").strip()[:300]
        return data

    def prepare(self, draft_id: str, delegation_id: str, template_id: str, scope: str,
                counterpart_id: str, variables: dict, at: str) -> dict:
        d = self._run("draft", "--draft-id", draft_id, "--delegation-id", delegation_id,
                      "--template-id", template_id, "--scope", scope, "--counterpart-id", counterpart_id,
                      "--variables", json.dumps(variables, ensure_ascii=False), "--at", at)
        if d["_rc"] != 0:
            raise SendFlowError("文案を作れません: " + d.get("_err", ""))
        shown = self._run("show", "--draft-id", draft_id)
        if shown.get("_rc") != 0:
            raise SendFlowError("本人に見せる文案を確認できません")
        self._check_prefix(shown)
        return {"draft": d, "show_to_owner": shown}

    def approve_and_handoff(self, draft_id: str, owner_ok_ref: str, at: str) -> dict:
        """owner_ok_ref：本人が文面を見て押した「OK」ボタンの記録の参照。無ければ進めない。"""
        if not owner_ok_ref or not owner_ok_ref.strip():
            raise SendFlowError("本人の文面のOKがありません。送れません")
        can = self._run("can-retry-send", "--draft-id", draft_id)
        if can.get("_rc") != 0:
            raise SendFlowError("送信案が見つかりません")
        if can.get("outcome_unknown") or can.get("handoff_status") not in (None, "", "渡し前（未送信）"):
            raise SendFlowError("もう引き渡しているか結果不明です。再送しません")
        shown = self._run("show", "--draft-id", draft_id)
        if shown.get("_rc") != 0:
            raise SendFlowError("送信案を再確認できません")
        self._check_prefix(shown)
        f = self._run("finalize", "--draft-id", draft_id, "--confirmed", "--at", at)
        if f["_rc"] != 0:
            raise SendFlowError("確定できません: " + f.get("_err", ""))
        h = self._run("handoff", "--draft-id", draft_id, "--at", at)
        if h["_rc"] != 0:
            raise SendFlowError("引き渡しの記録ができません: " + h.get("_err", ""))
        return {"finalized": f, "handoff": h, "next": "相手側の確認を待つ（来なければ未確認のまま、再送しない）"}

    def confirmed(self, draft_id: str, confirmation_ref: str, at: str) -> dict:
        return self._run("confirm-sent", "--draft-id", draft_id, "--confirmation-ref", confirmation_ref, "--at", at)

    def unknown(self, draft_id: str, reason: str) -> dict:
        return self._run("mark-outcome-unknown", "--draft-id", draft_id, "--reason", reason)

    def may_resend(self, draft_id: str) -> bool:
        r = self._run("can-retry-send", "--draft-id", draft_id)
        return bool(r.get("_rc") == 0 and r.get("retry_send") is True)

    def check(self) -> dict:
        return self._run("check", "--json")
