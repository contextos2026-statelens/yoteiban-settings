"""入力本文の保持期限処理と台帳サイズの集計。"""
from __future__ import annotations

import os


def purge(ledger, now):
    rows = ledger.conn.execute(
        "SELECT input_id,body_text,body_masked,summary_masked,retention_until FROM raw_inputs "
        "WHERE retention_until IS NOT NULL AND retention_until<=? ORDER BY input_id", (now,)
    ).fetchall()
    purged, needs_summary = [], []
    with ledger.tx():
        for row in rows:
            if not row["summary_masked"]:
                needs_summary.append({"input_id": row["input_id"], "retention_until": row["retention_until"]})
                continue
            if row["body_text"] or row["body_masked"]:
                ledger.conn.execute("UPDATE raw_inputs SET body_text='',body_masked='' WHERE input_id=?", (row["input_id"],))
                ledger._record_history("input:" + row["input_id"], {
                    "body_text": {"before": "保持本文", "after": ""},
                    "body_masked": {"before": "保持本文", "after": ""},
                }, "保持期限により本文を消去", now)
                purged.append(row["input_id"])
    return {"purged": purged, "needs_summary": needs_summary, "count": len(purged)}


def stats(ledger):
    counts = {"raw_inputs": ledger.conn.execute("SELECT COUNT(*) FROM raw_inputs").fetchone()[0],
              "cases": ledger.conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0],
              "operations": ledger.conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0]}
    page_count = ledger.conn.execute("PRAGMA page_count").fetchone()[0]
    page_size = ledger.conn.execute("PRAGMA page_size").fetchone()[0]
    counts["db_bytes"] = int(page_count * page_size)
    return counts
