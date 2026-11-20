"""リマインダー通知を、場所を含まない短い型へ分類する。"""
from __future__ import annotations

import re

_PATTERNS = (
    ("出発の知らせ", re.compile(r"^出発の時間：#([0-9]{1,8})$")),
    ("end_check", re.compile(r"^終わった？：#([0-9]{1,8})$")),
    ("出発の合図", re.compile(r"^出発：#([0-9]{1,8})(?:-([0-9]{1,4}))?$")),
    ("到着の合図", re.compile(r"^到着：#([0-9]{1,8})(?:-([0-9]{1,4}))?$")),
)


def classify(title: object) -> dict:
    text = title if isinstance(title, str) else ""
    for kind, pattern in _PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            number = str(int(match.group(1)))
            point_number = str(int(match.group(2))) if match.re.groups > 1 and match.group(2) else None
            if kind == "出発の知らせ":
                summary = f"出発の知らせ：#{number}"
                safe_title = summary
            elif kind == "end_check":
                summary, safe_title = f"終了確認：#{number}", f"終了確認：#{number}"
            else:
                suffix = f"-{point_number}" if point_number is not None else ""
                summary = f"{kind}：#{number}{suffix}"
                safe_title = summary
            return {"kind": kind, "case_number": number, "point_number": point_number,
                    "summary": summary, "safe_title": safe_title}
    return {"kind": "その他", "case_number": None, "summary": "その他のリマインダー通知",
            "safe_title": "リマインダー通知（その他）", "point_number": None}
