"""送信前に朝の本文と根拠項目の対応を確かめる。"""
from __future__ import annotations


REQUIRED_BODY_TYPES = {"予定", "食い違い", "取れなかったもの", "確認すること"}
HEADINGS = {"📅予定", "✅確認してね"}


def validate_morning_send(body: str, items: list[dict], ledger_items: list[dict] | None = None) -> dict:
    """根拠にない本文行と、本文に載っていない必須項目を返す。"""
    known = {str(x.get("text", "")).strip() for x in [*(items or []), *(ledger_items or [])]
             if str(x.get("text", "")).strip()}
    lines = [line.strip() for line in str(body or "").splitlines() if line.strip()]
    content = [line for line in lines if line not in HEADINGS]
    unexpected = [line for line in content if line not in known]
    visible = {x for x in content if x in known}
    missing = [{"type": x.get("type"), "text": x.get("text"), "values": x.get("values")}
               for x in items or [] if x.get("type") in REQUIRED_BODY_TYPES and x.get("text") not in visible]
    return {"ok": not unexpected and not missing,
            "unmatched_lines": unexpected, "missing_items": missing}
