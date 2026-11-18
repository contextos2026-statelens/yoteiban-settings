"""台帳に保存された入力の全文検索。"""
from __future__ import annotations

import argparse
import json
import sys

from .config import load
from .ledger import Ledger


def _match_snippet(raw: str, summary: str, query: str) -> str:
    for text in (raw or "", summary or ""):
        pos = text.casefold().find(query.casefold())
        if pos >= 0:
            return text[max(0, pos - 30): min(len(text), pos + len(query) + 30)]
    return ""


def search(ledger, query, since=None, until=None, sender_role=None, case_id=None, limit=20):
    if not isinstance(query, str) or not query.strip():
        return []
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit は正の整数で指定してください") from exc
    if not 1 <= limit <= 1000:
        raise ValueError("limit は1〜1000で指定してください")
    q = query.strip()
    clauses, params = [], []
    if since is not None:
        clauses.append("r.received_at>=?"); params.append(since)
    if until is not None:
        clauses.append("r.received_at<=?"); params.append(until)
    if sender_role is not None:
        clauses.append("r.sender_role=?"); params.append(sender_role)
    if case_id is not None:
        clauses.append("EXISTS (SELECT 1 FROM cases cf WHERE cf.cause_input_id=r.input_id AND cf.case_id=?)")
        params.append(case_id)
    where = " AND ".join(clauses) if clauses else "1=1"
    if len(q) >= 3:
        # Quote the input as a phrase so FTS operators cannot change the query.
        fts_query = '"' + q.replace('"', '""') + '"'
        sql = ("SELECT r.* FROM raw_inputs_fts f JOIN raw_inputs r ON r.rowid=f.rowid "
               f"WHERE raw_inputs_fts MATCH ? AND {where} ORDER BY r.received_at DESC,r.input_id LIMIT ?")
        rows = ledger.conn.execute(sql, [fts_query, *params, limit]).fetchall()
    else:
        sql = ("SELECT r.* FROM raw_inputs r WHERE (instr(r.body_text,?)>0 OR instr(r.summary_masked,?)>0) "
               f"AND {where} ORDER BY r.received_at DESC,r.input_id LIMIT ?")
        rows = ledger.conn.execute(sql, [q, q, *params, limit]).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        related = ledger.conn.execute("SELECT case_id FROM cases WHERE cause_input_id=? ORDER BY created_at,case_id", (row["input_id"],)).fetchall()
        ids = [r[0] for r in related]
        if case_id is not None:
            ids = [x for x in ids if x == case_id]
        result.append({"input_id": row["input_id"], "received_at": row["received_at"],
                       "summary_masked": row["summary_masked"],
                       "snippet": _match_snippet(row["body_text"], row["summary_masked"], q),
                       "case_ids": ids})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="予定ばんの入力を検索")
    parser.add_argument("--config", required=True)
    parser.add_argument("query")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--sender-role")
    parser.add_argument("--case-id")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    ledger = Ledger(load(args.config)["db_path"])
    try:
        print(json.dumps(search(ledger, args.query, args.since, args.until, args.sender_role, args.case_id, args.limit), ensure_ascii=False))
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
