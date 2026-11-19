#!/usr/bin/env python3
"""予定ばん 処理状態ストアと未完了キュー。

入力1件（依頼ID）ごとの処理状態だけを扱う。
案件状態（承認待ちなど）とは別層であり、このキューには置かない。

使い方の配線（スキル／ルーチンから呼ぶ設定）はこのツールでは行わない。
親が別途配線する前提。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

def default_store() -> Path:
    """処理状態ファイルの場所。環境変数または設定の tools から決める。箱の道は直書きしない。"""
    for name in ("YOTEIBAN_QUEUE", "YRD_QUEUE"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    settings = os.environ.get("YOTEIBAN_SETTINGS") or os.environ.get("YRD_SETTINGS")
    if settings:
        try:
            tools = json.loads(Path(settings).read_text(encoding="utf-8")).get("tools") or {}
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            tools = {}
        if tools.get("queue"):
            return Path(str(tools["queue"]))
        if tools.get("state"):
            return Path(str(tools["state"])) / "processing.jsonl"
    return Path("processing.jsonl")

# 処理状態（この層だけ）。承認・却下の語は使わない。
STATUS_RECEIVED = "受信済み"
STATUS_ANALYZING = "分析中"
STATUS_SAVED = "保存済み"
STATUS_DONE = "完了"
STATUS_RETRY = "失敗・要再試行"

ALL_STATUSES = (
    STATUS_RECEIVED,
    STATUS_ANALYZING,
    STATUS_SAVED,
    STATUS_DONE,
    STATUS_RETRY,
)

# pending で「処理中」にまとめるもの（要再試行とは分ける）
IN_PROGRESS_STATUSES = (STATUS_RECEIVED, STATUS_ANALYZING, STATUS_SAVED)

# 処理層に置いてはいけない語（案件状態側）
FORBIDDEN_STATUSES = ("承認待ち", "承認済み", "却下", "実行結果不明（要照合）")

RECORD_FIELDS = (
    "request_id",
    "source",
    "cursor_ref",
    "received_at",
    "status",
    "saved",
    "no_save_reason",
    "failure_reason",
    "case_ids",
    "updated_at",
)

JST_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?:\:(\d{2}))?\s*JST$"
)
INT_RE = re.compile(r"^-?\d+$")


class QueueError(Exception):
    """ユーザー向けの明確なエラー。"""


def now_jst() -> str:
    """箱のローカル時刻（Asia/Tokyo）を JST ラベル付きで返す。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S JST")


def parse_jst(s: str) -> datetime:
    m = JST_RE.match(s.strip())
    if not m:
        raise ValueError(f"JST 形式ではない: {s}")
    y, mo, d, h, mi, se = m.groups()
    return datetime(int(y), int(mo), int(d), int(h), int(mi), int(se or 0))


def ordered(rec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in RECORD_FIELDS:
        if key in rec:
            out[key] = rec[key]
    for key, val in rec.items():
        if key not in out:
            out[key] = val
    return out


def empty_record(
    request_id: str,
    source: str,
    cursor_ref: str,
    received_at: str,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "source": source,
        "cursor_ref": str(cursor_ref),
        "received_at": received_at,
        "status": STATUS_RECEIVED,
        "saved": False,
        "no_save_reason": "",
        "failure_reason": "",
        "case_ids": [],
        "updated_at": now_jst(),
    }


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as e:
                raise QueueError(f"{path}:{i}: JSON として読めません: {e}") from e
            if not isinstance(rec, dict):
                raise QueueError(f"{path}:{i}: オブジェクトではありません")
            rid = rec.get("request_id")
            if not rid or not isinstance(rid, str):
                raise QueueError(f"{path}:{i}: request_id がありません")
            if rid in seen:
                raise QueueError(f"{path}:{i}: request_id が重複しています: {rid}")
            seen.add(rid)
            records.append(rec)
    return records


def atomic_write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """ストア全体を原子的に書き換える。半端なファイルを残さない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".processing-", suffix=".jsonl", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            for rec in records:
                line = json.dumps(ordered(rec), ensure_ascii=False, separators=(",", ":"))
                tmp.write(line + "\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def lock_path_for(path: Path) -> Path:
    return path.parent / (path.name + ".lock")


def with_store(
    path: Path,
    fn: Callable[[list[dict[str, Any]]], tuple[list[dict[str, Any]] | None, Any]],
) -> Any:
    """排他ロックのうえストアを読み、必要なら書き戻す。

    fn(records) -> (new_records_or_None, result)
    new_records が None なら読むだけ（書き込まない）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lp = lock_path_for(path)
    with open(lp, "a+", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            records = load_records(path)
            new_records, result = fn(records)
            if new_records is not None:
                atomic_write_jsonl(path, new_records)
            return result
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def find_record(records: list[dict[str, Any]], request_id: str) -> dict[str, Any] | None:
    for rec in records:
        if rec.get("request_id") == request_id:
            return rec
    return None


def can_complete(rec: dict[str, Any], no_save_reason: str | None) -> bool:
    """完了にしてよいか。保存済み（saved）か、保存不要理由があること。"""
    reason = no_save_reason if no_save_reason is not None else rec.get("no_save_reason") or ""
    if rec.get("saved") is True or rec.get("status") == STATUS_SAVED:
        return True
    if isinstance(reason, str) and reason.strip():
        return True
    return False


def apply_status(
    rec: dict[str, Any],
    new_status: str,
    no_save_reason: str | None,
    failure_reason: str | None,
) -> dict[str, Any]:
    current = rec.get("status")
    if new_status in FORBIDDEN_STATUSES:
        raise QueueError(
            f"「{new_status}」は処理状態ではありません。"
            "承認待ちなどの案件状態はこのキューに置かず、案件状態側で扱います。"
        )
    if new_status not in ALL_STATUSES:
        allowed = " / ".join(ALL_STATUSES)
        raise QueueError(f"未知の処理状態です: {new_status}（使える値: {allowed}）")

    if current == STATUS_DONE and new_status != STATUS_DONE:
        raise QueueError(
            f"依頼ID {rec.get('request_id')} は既に完了しているため、"
            f"「{new_status}」へは戻せません。"
        )

    if new_status == STATUS_RECEIVED and current != STATUS_RECEIVED:
        raise QueueError(
            "「受信済み」への戻しは record の再登録（冪等・既存を返す）だけです。"
            "set-status では使えません。"
        )

    if new_status == STATUS_DONE:
        if not can_complete(rec, no_save_reason):
            raise QueueError(
                f"依頼ID {rec.get('request_id')} を完了にできません。"
                "完了にするには、候補箱への保存済み（status=保存済み、または saved=true）"
                "か、保存不要の理由が必要です。"
            )

    if new_status == STATUS_SAVED:
        rec["saved"] = True
        rec["failure_reason"] = ""

    if no_save_reason is not None:
        rec["no_save_reason"] = no_save_reason.strip()

    if new_status == STATUS_RETRY:
        if failure_reason is not None:
            rec["failure_reason"] = failure_reason.strip()
    elif failure_reason is not None and new_status != STATUS_RETRY:
        # 失敗理由は要再試行専用。他状態へ進むときは残さない。
        rec["failure_reason"] = ""

    rec["status"] = new_status
    rec["updated_at"] = now_jst()
    if "case_ids" not in rec or rec["case_ids"] is None:
        rec["case_ids"] = []
    return rec


def cursor_sort_key(ref: str, *, allow_mixed: bool) -> tuple[int, Any]:
    s = str(ref).strip()
    if INT_RE.fullmatch(s):
        return (0, int(s))
    try:
        return (1, parse_jst(s).timestamp())
    except ValueError:
        pass
    try:
        iso = s.replace("Z", "+00:00")
        return (1, datetime.fromisoformat(iso).timestamp())
    except ValueError:
        pass
    return (2, s)


def classify_cursor_kind(ref: str) -> int:
    return cursor_sort_key(ref, allow_mixed=True)[0]


def sorted_by_cursor(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kinds = {classify_cursor_kind(r.get("cursor_ref", "")) for r in records}
    if len(kinds) > 1:
        # 混在時は文字列比較に揃える（順序の誤用を避けるため種類を混ぜない運用が前提）
        return sorted(records, key=lambda r: str(r.get("cursor_ref", "")))
    return sorted(
        records,
        key=lambda r: cursor_sort_key(r.get("cursor_ref", ""), allow_mixed=False),
    )


def safe_cursor_for_source(records: list[dict[str, Any]]) -> str | None:
    """完了まで連続している最後の cursor_ref。未完了を飛び越えない。

    同じ cursor_ref のレコードが複数ある場合、その位置のすべてが完了でないと
    その位置は安全ではない。
    先頭から未完了がある（完了が1件も連続していない）ときは None。
    """
    if not records:
        return None
    ordered_recs = sorted_by_cursor(records)
    # cursor_ref ごとにまとめて先頭から見る
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for rec in ordered_recs:
        ref = str(rec.get("cursor_ref", ""))
        if groups and groups[-1][0] == ref:
            groups[-1][1].append(rec)
        else:
            groups.append((ref, [rec]))

    last_safe: str | None = None
    for ref, group in groups:
        if all(r.get("status") == STATUS_DONE for r in group):
            last_safe = ref
        else:
            break
    return last_safe


def findings_for(rec: dict[str, Any]) -> list[str]:
    """完了点検の矛盾・停滞を列挙する（読むだけ）。"""
    issues: list[str] = []
    rid = rec.get("request_id", "?")
    status = rec.get("status")
    saved = rec.get("saved") is True or status == STATUS_SAVED
    reason = (rec.get("no_save_reason") or "").strip()

    if status not in ALL_STATUSES:
        issues.append(f"{rid}: 未知の処理状態です（{status}）")

    if status == STATUS_DONE:
        if not saved and not reason:
            issues.append(
                f"{rid}: 完了なのに、保存済みでも保存不要の理由でもありません"
            )
    else:
        if saved or reason:
            why = []
            if saved:
                why.append("保存済み")
            if reason:
                why.append("保存不要の理由あり")
            issues.append(
                f"{rid}: {'／'.join(why)}なのに完了にしていません（現状: {status}）"
            )
    return issues


def print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_record(args: argparse.Namespace) -> int:
    request_id = args.request_id.strip()
    source = args.source.strip()
    cursor_ref = str(args.cursor_ref).strip()
    received_at = args.received_at.strip()
    if not request_id or not source or not cursor_ref or not received_at:
        raise QueueError("request-id / source / cursor-ref / received-at はすべて必須です")

    path = Path(args.file)

    def mut(records: list[dict[str, Any]]):
        existing = find_record(records, request_id)
        if existing is not None:
            warnings: list[str] = []
            if existing.get("source") != source:
                warnings.append(
                    f"既存の source と異なります（既存: {existing.get('source')} / 指定: {source}）。既存をそのまま返します。"
                )
            if str(existing.get("cursor_ref", "")) != cursor_ref:
                warnings.append(
                    f"既存の cursor_ref と異なります（既存: {existing.get('cursor_ref')} / 指定: {cursor_ref}）。既存をそのまま返します。"
                )
            return None, {
                "ok": True,
                "created": False,
                "request_id": request_id,
                "status": existing.get("status"),
                "file": str(path),
                "note": "同じ依頼IDは既に登録済みのため追加しません（冪等）",
                "record": ordered(existing),
                "warnings": warnings,
            }
        rec = empty_record(request_id, source, cursor_ref, received_at)
        records.append(rec)
        return records, {
            "ok": True,
            "created": True,
            "request_id": request_id,
            "status": rec["status"],
            "file": str(path),
            "record": ordered(rec),
        }

    result = with_store(path, mut)
    for w in result.get("warnings") or []:
        print(f"警告: {w}", file=sys.stderr)
    print_json({k: v for k, v in result.items() if k != "warnings" or v})
    return 0


def cmd_set_status(args: argparse.Namespace) -> int:
    request_id = args.request_id.strip()
    new_status = args.status.strip()
    path = Path(args.file)

    def mut(records: list[dict[str, Any]]):
        rec = find_record(records, request_id)
        if rec is None:
            raise QueueError(f"依頼ID {request_id} は登録されていません。先に record してください。")
        apply_status(rec, new_status, args.no_save_reason, args.failure_reason)
        return records, {
            "ok": True,
            "request_id": request_id,
            "status": rec["status"],
            "saved": rec.get("saved", False),
            "no_save_reason": rec.get("no_save_reason", ""),
            "failure_reason": rec.get("failure_reason", ""),
            "file": str(path),
            "record": ordered(rec),
        }

    result = with_store(path, mut)
    print_json(result)
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    path = Path(args.file)

    def mut(records: list[dict[str, Any]]):
        in_progress = [
            ordered(r) for r in records if r.get("status") in IN_PROGRESS_STATUSES
        ]
        retry = [ordered(r) for r in records if r.get("status") == STATUS_RETRY]
        # 完了は出さない。承認待ちという状態はこの層に無い。
        return None, {
            "処理中": in_progress,
            "要再試行": retry,
            "件数": {"処理中": len(in_progress), "要再試行": len(retry)},
            "注": "未完了（完了以外）だけを返します。処理中と要再試行は分けています。承認待ちは案件状態側であり、このキューには出ません。",
        }

    result = with_store(path, mut)
    if args.json:
        print_json(result)
        return 0

    print(f"処理中: {result['件数']['処理中']}件")
    if result["処理中"]:
        for r in result["処理中"]:
            print(
                f"  - {r['request_id']}  source={r.get('source')}  "
                f"cursor_ref={r.get('cursor_ref')}  status={r.get('status')}"
            )
    else:
        print("  （なし）")
    print(f"要再試行: {result['件数']['要再試行']}件")
    if result["要再試行"]:
        for r in result["要再試行"]:
            reason = r.get("failure_reason") or ""
            extra = f"  reason={reason}" if reason else ""
            print(
                f"  - {r['request_id']}  source={r.get('source')}  "
                f"cursor_ref={r.get('cursor_ref')}  status={r.get('status')}{extra}"
            )
    else:
        print("  （なし）")
    print("（承認待ちは案件状態側であり、この一覧には出ません）")
    return 0


def cmd_safe_cursor(args: argparse.Namespace) -> int:
    path = Path(args.file)
    only = args.source.strip() if args.source else None

    def mut(records: list[dict[str, Any]]):
        by_source: dict[str, list[dict[str, Any]]] = {}
        for rec in records:
            src = rec.get("source") or ""
            by_source.setdefault(src, []).append(rec)
        sources = [only] if only else sorted(by_source)
        rows = []
        for src in sources:
            recs = by_source.get(src, [])
            safe = safe_cursor_for_source(recs)
            incomplete = [
                ordered(r) for r in sorted_by_cursor(recs) if r.get("status") != STATUS_DONE
            ]
            rows.append(
                {
                    "source": src,
                    "safe_cursor": safe,
                    "完了の連続の末尾": safe,
                    "未完了件数": len(incomplete),
                    "未完了": [
                        {
                            "request_id": r["request_id"],
                            "cursor_ref": r.get("cursor_ref"),
                            "status": r.get("status"),
                        }
                        for r in incomplete
                    ],
                }
            )
        return None, rows

    rows = with_store(path, mut)
    if only and only not in {r["source"] for r in rows}:
        # source 指定でレコードが無いときも1行返す
        rows = [
            {
                "source": only,
                "safe_cursor": None,
                "完了の連続の末尾": None,
                "未完了件数": 0,
                "未完了": [],
                "note": "この source のレコードはありません",
            }
        ]

    if args.json:
        print_json(rows if not only else rows[0])
        return 0

    if not rows:
        print("レコードがありません。safe_cursor はありません（進められません）。")
        return 0
    for row in rows:
        safe = row["safe_cursor"]
        if safe is None:
            print(
                f"source={row['source']}  safe_cursor=（なし）  "
                "完了まで連続した位置が無いので、このストア上では先へ進めません。"
            )
        else:
            print(
                f"source={row['source']}  safe_cursor={safe}  "
                "これより先には進めません（未完了があれば、その手前で止まります）。"
            )
        if row["未完了件数"]:
            for u in row["未完了"]:
                print(
                    f"  未完了: {u['request_id']}  cursor_ref={u['cursor_ref']}  status={u['status']}"
                )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    path = Path(args.file)

    def mut(records: list[dict[str, Any]]):
        issues: list[str] = []
        for rec in records:
            issues.extend(findings_for(rec))
        return None, {"件数": len(records), "問題": issues, "問題件数": len(issues)}

    result = with_store(path, mut)
    if args.json:
        print_json(result)
    else:
        print(f"レコード: {result['件数']}件")
        print(f"問題: {result['問題件数']}件")
        if result["問題"]:
            for issue in result["問題"]:
                print(f"  - {issue}")
        else:
            print("  矛盾はありません。")
    return 1 if result["問題件数"] else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="queue.py",
        description=(
            "予定ばん 処理状態ストア（受信済み→分析中→保存済み→完了／失敗・要再試行）。"
            "案件状態（承認待ち）とは分離。カーソルは完了まで連続した位置より先へ進めない。"
        ),
    )
    p.add_argument(
        "--file",
        default=None,
        help="対象ストア。省略時は環境変数 YOTEIBAN_QUEUE / YRD_QUEUE、設定の tools.queue、それも無ければ processing.jsonl。検証時は一時ファイルを指定すること",
    )
    sub = p.add_subparsers(dest="command", required=True)

    rp = sub.add_parser("record", help="入力1件を受信済みで登録（依頼IDで冪等）")
    rp.add_argument("--request-id", required=True, help="依頼ID（入力1件の識別子）")
    rp.add_argument("--source", required=True, help="入口の種類")
    rp.add_argument("--cursor-ref", required=True, help="その入口での読み取り位置の参照")
    rp.add_argument("--received-at", required=True, help="受信時刻（例: 2026-11-17 01:00 JST）")
    rp.set_defaults(func=cmd_record)

    sp = sub.add_parser("set-status", help="処理状態を遷移する（完了には保存済みか保存不要理由が必要）")
    sp.add_argument("--request-id", required=True)
    sp.add_argument("--status", required=True, help="分析中／保存済み／完了／失敗・要再試行")
    sp.add_argument(
        "--no-save-reason",
        default=None,
        help="保存不要として完了するときの理由（この理由が無いと、未保存のまま完了できない）",
    )
    sp.add_argument(
        "--failure-reason",
        default=None,
        help="失敗・要再試行にするときの理由（処理層の失敗。承認・却下とは別）",
    )
    sp.set_defaults(func=cmd_set_status)

    pp = sub.add_parser("pending", help="未完了キュー（完了以外）。処理中と要再試行を分けて表示")
    pp.add_argument("--json", action="store_true", help="JSON で出力")
    pp.set_defaults(func=cmd_pending)

    cp = sub.add_parser(
        "safe-cursor",
        help="source ごとに、完了まで連続している最後の位置を返す（これより先へは進めない）",
    )
    cp.add_argument("--source", default=None, help="入口を1つに絞る（省略時はすべて）")
    cp.add_argument("--json", action="store_true", help="JSON で出力")
    cp.set_defaults(func=cmd_safe_cursor)

    kp = sub.add_parser("check", help="完了点検。矛盾を洗い出す（読むだけ）")
    kp.add_argument("--json", action="store_true", help="JSON で出力")
    kp.set_defaults(func=cmd_check)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.file:
        args.file = str(default_store())
    try:
        return args.func(args)
    except QueueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
