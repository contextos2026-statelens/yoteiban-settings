#!/usr/bin/env python3
"""第2段階試作 casebook.py の隔離テスト。

本番ストア・本番作業記録・生きたスキル／ルーチンには触れない。
作業用ディレクトリは /tmp 配下に作り、そこにだけ書き込む。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASEBOOK = HERE / "casebook.py"
TOOLS = HERE.parent
QUEUE = TOOLS / "queue.py"
WORKLOG = TOOLS / "worklog.py"
BACKUP = Path(os.environ.get("YOTEIBAN_TOOLS_BACKUP") or os.environ.get("YRD_TOOLS_BACKUP") or "")

# このテストが触ってよい領域の接頭辞
TMP_PREFIX = "/tmp/"


class Fail(Exception):
    pass


def ensure_tmp(path: Path) -> Path:
    resolved = path.resolve()
    if not str(resolved).startswith(TMP_PREFIX):
        raise Fail(f"隔離違反: {resolved} は /tmp 配下ではありません")
    return resolved


def run(args: list[str], *, stdin: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=stdin,
        text=True,
        capture_output=True,
        cwd=str(cwd) if cwd else None,
    )


def run_cb(store: Path, argv: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    ensure_tmp(store)
    return run([sys.executable, str(CASEBOOK), "--file", str(store), *argv], stdin=stdin)


def must_ok(cp: subprocess.CompletedProcess[str], ctx: str) -> dict:
    if cp.returncode != 0:
        raise Fail(
            f"{ctx}: 終了コード {cp.returncode}\nstdout:\n{cp.stdout}\nstderr:\n{cp.stderr}"
        )
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError as e:
        raise Fail(f"{ctx}: JSON が読めません: {e}\nstdout:\n{cp.stdout}") from e


def must_err(cp: subprocess.CompletedProcess[str], ctx: str, *needles: str) -> None:
    if cp.returncode == 0:
        raise Fail(f"{ctx}: 成功してしまった（拒否されるべき）\nstdout:\n{cp.stdout}")
    if cp.returncode != 2:
        # check は 1 を返すことがあるが、遷移拒否は 2
        text = cp.stderr + cp.stdout
        if cp.returncode == 1 and "問題" in text:
            pass
        else:
            raise Fail(
                f"{ctx}: 終了コード {cp.returncode}（期待は 2）\nstderr:\n{cp.stderr}\nstdout:\n{cp.stdout}"
            )
    text = cp.stderr + cp.stdout
    for n in needles:
        if n not in text:
            raise Fail(f"{ctx}: エラー文に「{n}」がありません\n{text}")


def load(store: Path) -> dict:
    ensure_tmp(store)
    return json.loads(store.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# casebook テスト
# ---------------------------------------------------------------------------

def test_record_idempotent(store: Path) -> None:
    first = must_ok(
        run_cb(
            store,
            [
                "record",
                "--request-id",
                "req-001",
                "--source",
                "mail",
                "--received-at",
                "2026-11-17 01:00 JST",
            ],
        ),
        "初回 record",
    )
    if first.get("created") is not True:
        raise Fail(f"初回 record の created が true ではない: {first}")
    if first.get("processing_status") != "受信済み":
        raise Fail(f"初回の処理状態が受信済みではない: {first}")

    second = must_ok(
        run_cb(
            store,
            [
                "record",
                "--request-id",
                "req-001",
                "--source",
                "mail",
                "--received-at",
                "2026-11-17 01:00 JST",
            ],
        ),
        "再 record",
    )
    if second.get("created") is not False:
        raise Fail(f"再 record の created が false ではない: {second}")
    if second.get("processing_status") != "受信済み":
        raise Fail("再登録で処理状態が変わった")

    data = load(store)
    if len(data["requests"]) != 1:
        raise Fail(f"依頼が二重作成されている: {list(data['requests'])}")


def test_one_request_many_cases(store: Path) -> None:
    must_ok(
        run_cb(
            store,
            [
                "record",
                "--request-id",
                "req-002",
                "--source",
                "chat",
                "--received-at",
                "2026-11-17 01:10 JST",
            ],
        ),
        "record req-002",
    )
    out = must_ok(
        run_cb(
            store,
            ["add-cases", "--request-id", "req-002", "--mark-saved"],
            stdin=json.dumps(
                [
                    {"case_key": "meet", "summary": "会合の候補"},
                    {"case_key": "travel", "summary": "移動の候補"},
                ],
                ensure_ascii=False,
            ),
        ),
        "add-cases 2件",
    )
    if out.get("created_count") != 2:
        raise Fail(f"2件作られるはずが {out}")
    data = load(store)
    req = data["requests"]["req-002"]
    if len(req["case_ids"]) != 2:
        raise Fail(f"1依頼に案件が2件ない: {req}")
    if set(req["case_ids"]) != {"req-002#meet", "req-002#travel"}:
        raise Fail(f"安定IDが想定と違う: {req['case_ids']}")
    if req["processing_status"] != "保存済み":
        raise Fail(f"--mark-saved なのに処理状態が {req['processing_status']}")
    if len(data["cases"]) != 2:
        raise Fail(f"案件ストアが2件ではない: {list(data['cases'])}")


def test_add_case_idempotent_no_duplicates(store: Path) -> None:
    """途中停止後の再実行を模す: 先に1件だけ作り、同じ集合を再投入しても二重にならない。"""
    must_ok(
        run_cb(
            store,
            [
                "record",
                "--request-id",
                "req-003",
                "--source",
                "mail",
                "--received-at",
                "2026-11-17 01:20 JST",
            ],
        ),
        "record req-003",
    )
    first = must_ok(
        run_cb(
            store,
            [
                "add-case",
                "--request-id",
                "req-003",
                "--case-key",
                "alpha",
                "--summary",
                "候補A",
                "--mark-saved",
            ],
        ),
        "add-case alpha 初回",
    )
    if first.get("created") is not True:
        raise Fail("alpha 初回が created true ではない")

    # 再実行（同じキー + まだ無かったキー）
    again = must_ok(
        run_cb(
            store,
            ["add-cases", "--request-id", "req-003", "--mark-saved"],
            stdin=json.dumps(
                [
                    {"case_key": "alpha", "summary": "候補A"},
                    {"case_key": "beta", "summary": "候補B"},
                ],
                ensure_ascii=False,
            ),
        ),
        "再実行 add-cases alpha+beta",
    )
    if again.get("created_count") != 1 or again.get("existing_count") != 1:
        raise Fail(f"再実行の内訳が想定外: {again}")
    if again["created_ids"] != ["req-003#beta"]:
        raise Fail(f"新規は beta だけのはず: {again}")
    if again["existing_ids"] != ["req-003#alpha"]:
        raise Fail(f"既存は alpha だけのはず: {again}")

    data = load(store)
    ids = data["requests"]["req-003"]["case_ids"]
    if ids != ["req-003#alpha", "req-003#beta"]:
        raise Fail(f"二重作成または順序の崩れ: {ids}")
    if len(data["cases"]) != 2:
        raise Fail(f"案件数が2ではない: {list(data['cases'])}")

    # 内容ハッシュでも冪等
    h1 = must_ok(
        run_cb(
            store,
            [
                "add-case",
                "--request-id",
                "req-003",
                "--content",
                "正規化した内容X",
                "--summary",
                "ハッシュ案件",
            ],
        ),
        "content ハッシュ初回",
    )
    h2 = must_ok(
        run_cb(
            store,
            [
                "add-case",
                "--request-id",
                "req-003",
                "--content",
                "正規化した内容X",
                "--summary",
                "ハッシュ案件",
            ],
        ),
        "content ハッシュ再実行",
    )
    if h1["created"] is not True or h2["created"] is not False:
        raise Fail(f"内容ハッシュの冪等が壊れている: {h1} / {h2}")
    if h1["case_id"] != h2["case_id"]:
        raise Fail("同じ内容なのに案件IDが違う")
    if len(load(store)["cases"]) != 3:
        raise Fail("ハッシュ案件が二重作成された")


def test_illegal_transitions_rejected(store: Path) -> None:
    must_ok(
        run_cb(
            store,
            [
                "record",
                "--request-id",
                "req-004",
                "--source",
                "mail",
                "--received-at",
                "2026-11-17 01:30 JST",
            ],
        ),
        "record req-004",
    )
    must_ok(
        run_cb(
            store,
            [
                "add-case",
                "--request-id",
                "req-004",
                "--case-key",
                "only",
                "--mark-saved",
            ],
        ),
        "add-case only",
    )
    cid = "req-004#only"

    # 処理系統に承認待ちを入れようとする
    must_err(
        run_cb(store, ["set-processing", "--request-id", "req-004", "--status", "承認待ち"]),
        "処理へ承認待ち",
        "処理の成否ではありません",
    )
    # 処理系統に送信完了
    must_err(
        run_cb(store, ["set-processing", "--request-id", "req-004", "--status", "送信完了"]),
        "処理へ送信完了",
        "処理の成否ではありません",
    )
    # 未保存のまま完了（この依頼は mark-saved 済みなので別依頼で試す）
    must_ok(
        run_cb(
            store,
            [
                "record",
                "--request-id",
                "req-004b",
                "--source",
                "mail",
                "--received-at",
                "2026-11-17 01:31 JST",
            ],
        ),
        "record req-004b",
    )
    must_err(
        run_cb(store, ["set-processing", "--request-id", "req-004b", "--status", "完了"]),
        "未保存の完了",
        "完了にできません",
    )
    # 案件系統に処理の語
    must_err(
        run_cb(store, ["set-case", "--case-id", cid, "--status", "失敗・要再試行"]),
        "案件へ処理の語",
        "案件の状態ではありません",
    )
    # 提案前から実行完了は不可
    must_err(
        run_cb(store, ["set-case", "--case-id", cid, "--status", "実行完了"]),
        "提案前から実行完了",
        "遷移できません",
    )
    # 正常な遷移は通る
    for st in ("提案中", "承認待ち", "承認済み"):
        must_ok(run_cb(store, ["set-case", "--case-id", cid, "--status", st]), f"set-case {st}")

    data = load(store)
    if data["requests"]["req-004"]["processing_status"] == "承認待ち":
        raise Fail("処理状態が承認待ちに汚染された")
    if data["cases"][cid]["case_status"] != "承認済み":
        raise Fail("正常遷移後の案件状態が承認済みではない")


def test_outcome_unknown_does_not_prompt_retry(store: Path) -> None:
    must_ok(
        run_cb(
            store,
            [
                "record",
                "--request-id",
                "req-005",
                "--source",
                "mail",
                "--received-at",
                "2026-11-17 01:40 JST",
            ],
        ),
        "record req-005",
    )
    must_ok(
        run_cb(
            store,
            [
                "add-case",
                "--request-id",
                "req-005",
                "--case-key",
                "cal",
                "--mark-saved",
            ],
        ),
        "add-case cal",
    )
    cid = "req-005#cal"
    for st in ("提案中", "承認待ち", "承認済み", "実行中"):
        must_ok(run_cb(store, ["set-case", "--case-id", cid, "--status", st]), f"進める {st}")
    marked = must_ok(
        run_cb(
            store,
            [
                "mark-outcome-unknown",
                "--case-id",
                cid,
                "--reason",
                "変更結果を確認できない",
            ],
        ),
        "mark-outcome-unknown",
    )
    if marked.get("prompt_retry") is not False or marked.get("retry_execution") is not False:
        raise Fail(f"不明時に再実行を促している: {marked}")
    if marked.get("case_status") != "実行結果不明（要照合）":
        raise Fail(f"状態が変わっていない: {marked}")

    decision = must_ok(
        run_cb(store, ["can-retry-execution", "--case-id", cid]),
        "can-retry-execution",
    )
    if decision.get("retry_ok") is not False or decision.get("prompt_retry") is not False:
        raise Fail(f"再実行可否が安全側ではない: {decision}")
    msg = decision.get("message") or ""
    if "再実行しません" not in msg:
        raise Fail(f"再実行しない旨がメッセージにない: {decision}")
    forbidden = decision.get("forbidden") or []
    if "外部送信の再実行" not in forbidden or "カレンダー変更の再実行" not in forbidden:
        raise Fail(f"禁止事項が足りない: {decision}")

    # 実行中へ戻そうとしても拒否
    must_err(
        run_cb(store, ["set-case", "--case-id", cid, "--status", "実行中"]),
        "不明から実行中",
        "遷移できません",
    )
    # 照合なしで実行完了も拒否
    must_err(
        run_cb(store, ["set-case", "--case-id", cid, "--status", "実行完了"]),
        "照合なしで実行完了",
        "照合済み",
    )
    # 引き渡しのやり直しも拒否
    must_err(
        run_cb(store, ["handoff", "--case-id", cid]),
        "不明時の引き渡しやり直し",
        "やり直しはしません",
    )
    # 照合済みなら実行完了だけ可（再実行ではない）
    done = must_ok(
        run_cb(
            store,
            ["set-case", "--case-id", cid, "--status", "実行完了", "--reconciled"],
        ),
        "照合済みで実行完了",
    )
    if done.get("case_status") != "実行完了":
        raise Fail(f"照合後に実行完了になっていない: {done}")


def test_bot_confirm_required_for_sent(store: Path) -> None:
    must_ok(
        run_cb(
            store,
            [
                "record",
                "--request-id",
                "req-006",
                "--source",
                "chat",
                "--received-at",
                "2026-11-17 01:50 JST",
            ],
        ),
        "record req-006",
    )
    must_ok(
        run_cb(
            store,
            [
                "add-case",
                "--request-id",
                "req-006",
                "--case-key",
                "msg",
                "--mark-saved",
            ],
        ),
        "add-case msg",
    )
    cid = "req-006#msg"

    # 渡し前のまま送信完了は不可
    must_err(
        run_cb(
            store,
            ["confirm-sent", "--case-id", cid, "--confirmation-ref", "ack-1"],
        ),
        "未引き渡しで送信完了",
        "確認無しで送信完了にはできません",
    )

    handed = must_ok(run_cb(store, ["handoff", "--case-id", cid]), "handoff")
    if handed.get("handoff_status") != "渡し済み（未確認）":
        raise Fail(f"引き渡し後の状態が違う: {handed}")

    # 渡し直しはしない（冪等・changed false）
    again = must_ok(run_cb(store, ["handoff", "--case-id", cid]), "handoff 再実行")
    if again.get("changed") is not False:
        raise Fail(f"渡し直してしまった: {again}")
    if "渡し直しはしません" not in (again.get("note") or ""):
        raise Fail(f"渡し直ししない旨がない: {again}")

    # 確認参照なしはパーサエラーまたは業務エラー
    cp = run_cb(store, ["confirm-sent", "--case-id", cid])
    if cp.returncode == 0:
        raise Fail("確認参照なしで送信完了できた")

    confirmed = must_ok(
        run_cb(
            store,
            ["confirm-sent", "--case-id", cid, "--confirmation-ref", "ack-1"],
        ),
        "confirm-sent",
    )
    if confirmed.get("handoff_status") != "送信完了":
        raise Fail(f"確認後も送信完了ではない: {confirmed}")

    # 送信完了後の渡し直しは拒否
    must_err(
        run_cb(store, ["handoff", "--case-id", cid]),
        "送信完了後の渡し直し",
        "渡し直しはできません",
    )

    # 返事待ち行は送信完了後だけ
    # 別案件で、送信前に mark-reply が拒否されることを見る
    must_ok(
        run_cb(
            store,
            [
                "add-case",
                "--request-id",
                "req-006",
                "--case-key",
                "msg2",
                "--mark-saved",
            ],
        ),
        "add-case msg2",
    )
    must_err(
        run_cb(store, ["mark-reply", "--case-id", "req-006#msg2"]),
        "送信前の追跡行",
        "送信完了のあとだけ",
    )
    replied = must_ok(run_cb(store, ["mark-reply", "--case-id", cid]), "mark-reply")
    if replied.get("handoff_status") != "返事あり":
        raise Fail(f"返事ありになっていない: {replied}")
    data = load(store)
    track = data["cases"][cid].get("reply_track")
    if not isinstance(track, dict) or track.get("status") != "返事待ち":
        raise Fail(f"追跡行が無い: {track}")


def test_check_detects_illegal(store: Path) -> None:
    """check が系統の混同や確認欠落を検出すること。不正データはテストが直接書き込む。"""
    ensure_tmp(store)
    planted = {
        "version": 1,
        "requests": {
            "req-bad": {
                "request_id": "req-bad",
                "source": "mail",
                "received_at": "2026-11-17 02:00 JST",
                "processing_status": "承認待ち",
                "saved": False,
                "no_save_reason": "",
                "failure_reason": "",
                "case_ids": ["req-bad#x"],
                "created_at": "2026-11-17 02:00:00 JST",
                "updated_at": "2026-11-17 02:00:00 JST",
            },
            "req-done-bad": {
                "request_id": "req-done-bad",
                "source": "mail",
                "received_at": "2026-11-17 02:01 JST",
                "processing_status": "完了",
                "saved": False,
                "no_save_reason": "",
                "failure_reason": "",
                "case_ids": [],
                "created_at": "2026-11-17 02:01:00 JST",
                "updated_at": "2026-11-17 02:01:00 JST",
            },
        },
        "cases": {
            "req-bad#x": {
                "case_id": "req-bad#x",
                "request_id": "req-bad",
                "case_key": "x",
                "summary": "",
                "case_status": "失敗・要再試行",
                "handoff_status": "送信完了",
                "handed_off_at": "",
                "confirmation_received_at": "",
                "confirmation_ref": "",
                "reply_track": {
                    "status": "返事待ち",
                    "created_at": "2026-11-17 02:00:00 JST",
                },
                "outcome_unknown_reason": "",
                "retry_execution": False,
                "created_at": "2026-11-17 02:00:00 JST",
                "updated_at": "2026-11-17 02:00:00 JST",
            },
            "req-bad#y": {
                "case_id": "req-bad#y",
                "request_id": "req-missing",
                "case_key": "y",
                "summary": "",
                "case_status": "実行結果不明（要照合）",
                "handoff_status": "渡し前（未送信）",
                "handed_off_at": "",
                "confirmation_received_at": "",
                "confirmation_ref": "",
                "reply_track": {
                    "status": "返事待ち",
                    "created_at": "2026-11-17 02:00:00 JST",
                },
                "outcome_unknown_reason": "不明",
                "retry_execution": True,
                "created_at": "2026-11-17 02:00:00 JST",
                "updated_at": "2026-11-17 02:00:00 JST",
            },
        },
    }
    store.write_text(json.dumps(planted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cp = run_cb(store, ["check", "--json"])
    if cp.returncode != 1:
        raise Fail(f"不正ストアなのに check が {cp.returncode}\n{cp.stdout}\n{cp.stderr}")
    result = json.loads(cp.stdout)
    if result.get("問題件数", 0) < 5:
        raise Fail(f"問題件数が少なすぎる: {result}")
    text = json.dumps(result, ensure_ascii=False)
    expected_bits = [
        "系統の混同",
        "承認待ち",
        "確認欠落",
        "返事待ちの時期",
        "再実行フラグ",
        "完了条件",
    ]
    missing = [b for b in expected_bits if b not in text]
    if missing:
        raise Fail(f"check の検出漏れ: {missing}\n{text}")

    # 正常ストアでは問題 0
    clean = store.with_name("clean.json")
    ensure_tmp(clean)
    must_ok(
        run_cb(
            clean,
            [
                "record",
                "--request-id",
                "req-ok",
                "--source",
                "mail",
                "--received-at",
                "2026-11-17 02:10 JST",
            ],
        ),
        "clean record",
    )
    must_ok(
        run_cb(
            clean,
            ["set-processing", "--request-id", "req-ok", "--status", "完了", "--no-save-reason", "予定に関係なし"],
        ),
        "clean 完了",
    )
    chk = run_cb(clean, ["check", "--json"])
    if chk.returncode != 0:
        raise Fail(f"正常ストアで check が失敗: {chk.stdout} {chk.stderr}")
    payload = json.loads(chk.stdout)
    if payload.get("問題件数") != 0:
        raise Fail(f"正常ストアなのに問題あり: {payload}")


def test_requires_file_flag() -> None:
    cp = run([sys.executable, str(CASEBOOK), "record", "--request-id", "x", "--source", "mail", "--received-at", "2026-11-17 01:00 JST"])
    if cp.returncode == 0:
        raise Fail("--file なしで動いてしまった（本番パスを持ちかねない）")
    if "--file" not in (cp.stderr + cp.stdout):
        raise Fail("--file 必須の案内が無い")


# ---------------------------------------------------------------------------
# 既存ツールを壊していないことの確認（/tmp のみ、本番ファイルは読めても書かない）
# ---------------------------------------------------------------------------

def sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def test_existing_tools_unchanged() -> None:
    names = ("queue.py", "worklog.py", "README.md")
    for name in names:
        live = TOOLS / name
        bak = BACKUP / name
        if not live.exists() or not bak.exists():
            raise Fail(f"比較対象が無い: {live} / {bak}")
        if sha256(live) != sha256(bak):
            raise Fail(f"{name} のハッシュがバックアップと違う（現行ツールが変わった）")


def test_queue_smoke(work: Path) -> None:
    work = ensure_tmp(work)
    work.mkdir(parents=True, exist_ok=True)
    qfile = ensure_tmp(work / "processing.jsonl")
    def q(*argv: str) -> subprocess.CompletedProcess[str]:
        return run([sys.executable, str(QUEUE), "--file", str(qfile), *argv])

    r1 = must_ok(
        q("record", "--request-id", "q-1", "--source", "mail", "--cursor-ref", "10", "--received-at", "2026-11-17 01:00 JST"),
        "queue record",
    )
    if r1.get("created") is not True or r1.get("status") != "受信済み":
        raise Fail(f"queue record 初回が想定外: {r1}")
    r2 = must_ok(
        q("record", "--request-id", "q-1", "--source", "mail", "--cursor-ref", "10", "--received-at", "2026-11-17 01:00 JST"),
        "queue record 冪等",
    )
    if r2.get("created") is not False:
        raise Fail(f"queue の冪等が壊れた: {r2}")
    must_err(
        q("set-status", "--request-id", "q-1", "--status", "承認待ち"),
        "queue 承認待ち拒否",
        "処理状態ではありません",
    )
    must_ok(q("set-status", "--request-id", "q-1", "--status", "分析中"), "queue 分析中")
    must_ok(q("set-status", "--request-id", "q-1", "--status", "保存済み"), "queue 保存済み")
    must_ok(q("set-status", "--request-id", "q-1", "--status", "完了"), "queue 完了")
    pending = run([sys.executable, str(QUEUE), "--file", str(qfile), "pending", "--json"])
    if pending.returncode != 0:
        raise Fail(f"queue pending 失敗: {pending.stderr}")
    payload = json.loads(pending.stdout)
    if payload["件数"]["処理中"] != 0 or payload["件数"]["要再試行"] != 0:
        raise Fail(f"完了済みなのに pending に残る: {payload}")
    sc = must_ok(
        run([sys.executable, str(QUEUE), "--file", str(qfile), "safe-cursor", "--json"]),
        "queue safe-cursor",
    )
    # 1 source のときオブジェクト、複数なら配列。--source 無しで1件なら配列のことも
    rows = sc if isinstance(sc, list) else [sc]
    if not any(row.get("safe_cursor") == "10" for row in rows):
        raise Fail(f"safe-cursor が 10 ではない: {sc}")
    chk = run([sys.executable, str(QUEUE), "--file", str(qfile), "check", "--json"])
    if chk.returncode != 0:
        raise Fail(f"queue check が失敗: {chk.stdout} {chk.stderr}")


def test_worklog_smoke(work: Path) -> None:
    work = ensure_tmp(work)
    work.mkdir(parents=True, exist_ok=True)
    wfile = ensure_tmp(work / "log.jsonl")
    wfile.write_text("", encoding="utf-8")
    payload = {
        "time_start": "2026-11-17 01:00 JST",
        "time_end": "2026-11-17 01:05 JST",
        "trigger": "隔離テスト",
        "kind": "設定",
        "input": "出どころ＋要約1行",
        "skills_read": ["予定ばんの運用ルール"],
        "tools_used": {"Shell": 1},
        "thinking": "確かめ→考え→案",
        "actions": ["記録のみ"],
        "output": "要約1行",
        "user_change": "",
        "result": "完了",
        "notes": "",
    }
    ok = run(
        [sys.executable, str(WORKLOG), "--file", str(wfile), "append"],
        stdin=json.dumps(payload, ensure_ascii=False),
    )
    if ok.returncode != 0:
        raise Fail(f"worklog append 失敗: {ok.stderr}\n{ok.stdout}")
    bad = dict(payload)
    bad["unknown_field"] = "no"
    ng = run(
        [sys.executable, str(WORKLOG), "--file", str(wfile), "append"],
        stdin=json.dumps(bad, ensure_ascii=False),
    )
    if ng.returncode == 0:
        raise Fail("未知項目を worklog が受け入れてしまった")
    val = run([sys.executable, str(WORKLOG), "--file", str(wfile), "validate"])
    if val.returncode != 0:
        raise Fail(f"worklog validate 失敗: {val.stdout} {val.stderr}")
    # 本番ファイルの行数が、このテストで増えていないこと（パスを触っていないことの確認は呼び出し側）
    if not str(wfile).startswith(TMP_PREFIX):
        raise Fail("worklog テストが /tmp 以外を書いた")


# ---------------------------------------------------------------------------
# ランナー
# ---------------------------------------------------------------------------

TESTS = [
    ("冪等な依頼登録", "casebook", test_record_idempotent, True),
    ("1依頼から複数案件", "casebook", test_one_request_many_cases, True),
    ("案件作成の冪等（途中停止後の再実行で二重作成なし）", "casebook", test_add_case_idempotent_no_duplicates, True),
    ("不正な状態遷移の拒否", "casebook", test_illegal_transitions_rejected, True),
    ("実行結果不明のとき再実行を促さない", "casebook", test_outcome_unknown_does_not_prompt_retry, True),
    ("Bot間確認なしで送信完了にできない", "casebook", test_bot_confirm_required_for_sent, True),
    ("check が不正を検出する", "casebook", test_check_detects_illegal, True),
    ("--file 必須（既定の本番パスを持たない）", "casebook", lambda _s: test_requires_file_flag(), True),
    ("現行ツールのハッシュがバックアップと一致", "compat", lambda _s: test_existing_tools_unchanged(), False),
    ("queue.py の隔離スモーク（振る舞い維持）", "compat", test_queue_smoke, True),
    ("worklog.py の隔離スモーク（振る舞い維持）", "compat", test_worklog_smoke, True),
]


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="yoteiban-stage2-test-", dir="/tmp"))
    ensure_tmp(root)
    print(f"隔離ディレクトリ: {root}")
    passed = 0
    failed = 0
    errors: list[str] = []
    for i, (name, group, fn, needs_store) in enumerate(TESTS, start=1):
        workdir = root / f"t{i:02d}-{group}"
        workdir.mkdir(parents=True, exist_ok=True)
        store = workdir / "store.json"
        try:
            fn(store)
        except Exception as e:
            failed += 1
            detail = "".join(traceback.format_exception(e))
            errors.append(f"[失敗] {i}. {name}\n{detail}")
            print(f"[失敗] {i}. [{group}] {name}")
        else:
            passed += 1
            print(f"[通過] {i}. [{group}] {name}")
    print(f"\n結果: 通過 {passed} / 失敗 {failed} / 全 {passed + failed}")
    if errors:
        print("\n------ 失敗の詳細 ------")
        print("\n".join(errors))
    # 失敗時も作業ディレクトリは残す（調査用）。成功時も残す（痕跡の確認用）。
    print(f"作業ディレクトリは残しています: {root}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
