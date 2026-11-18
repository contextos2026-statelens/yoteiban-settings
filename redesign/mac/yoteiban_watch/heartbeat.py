"""監視サービスへの読み取り専用の心拍。"""
from __future__ import annotations

import urllib.request


def ping(url: str, ok: bool, fetch=None) -> bool:
    """成功時はURL、失敗時は /fail へGETする。通信失敗は呼び出し元へ出さない。"""
    if not url:
        return False
    target = url.rstrip("/") + ("" if ok else "/fail")
    fetch = fetch or urllib.request.urlopen
    try:
        response = fetch(target)
        if hasattr(response, "close"):
            response.close()
        return True
    except Exception:
        return False
