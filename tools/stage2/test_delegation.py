#!/usr/bin/env python3
"""第2段階試作 delegation.py の隔離テスト。

本番ストア・本番作業記録・生きたスキル／ルーチンには触れない。
作業用ディレクトリは /tmp 配下に作り、そこにだけ書き込む。
実送信は行わない。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
DELEGATION = HERE / "delegation.py"

# このテストが触ってよい領域の接頭辞
TMP_PREFIX = "/tmp/"

# 既存ツール（変更していないことの確認用。テストからは読めるが書かない）
TOOLS = HERE.parent
CASEBOOK = HERE / "casebook.py"
QUEUE = TOOLS / "queue.py"
WORKLOG = TOOLS / "worklog.py"


class Fail(Exception):
    pass


def ensure_tmp(path: Path) -> Path:
    resolved = path.resolve()
    if not str(resolved).startswith(TMP_PREFIX):
        raise Fail(f"隔離違反: {resolved} は /tmp 配下ではありません")
    return resolved


def run(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=stdin,
        text=True,
        capture_output=True,
    )


def run_dg(store: Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    ensure_tmp(store)
    return run([sys.executable, str(DELEGATION), "--file", str(store), *argv])


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


def save(store: Path, data: dict) -> None:
    ensure_tmp(store)
    store.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 共通の健全な委任・テンプレート
# ---------------------------------------------------------------------------

VARS_OK = json.dumps({"date": "2026-11-18"}, ensure_ascii=False)
ALLOWED = json.dumps({"date": ["2026-11-18", "2026-11-19"]}, ensure_ascii=False)
AT_OK = "2026-11-17 10:00 JST"
BODY = "候補は {date} です"


def seed_valid(
    store: Path,
    *,
    send_allowed: bool = True,
    share_allowed: bool = True,
    approve: bool = True,
    valid_from: str = "2026-11-01 00:00 JST",
    valid_until: str = "2026-11-30 23:59 JST",
    scope: str = "schedule-confirm",
    delegation_id: str = "del-001",
    template_id: str = "tmpl-001",
) -> None:
    argv = [
        "add-delegation",
        "--delegation-id",
        delegation_id,
        "--counterpart-id",
        "role-partner",
        "--scope",
        scope,
        "--valid-from",
        valid_from,
        "--valid-until",
        valid_until,
    ]
    if share_allowed:
        argv.append("--share-allowed")
    if send_allowed:
        argv.append("--send-allowed")
    must_ok(run_dg(store, argv), "委任の登録")
    targv = [
        "add-template",
        "--template-id",
        template_id,
        "--delegation-id",
        delegation_id,
        "--body",
        BODY,
        "--allowed-vars",
        ALLOWED,
    ]
    if approve:
        targv.append("--approve")
    must_ok(run_dg(store, targv), "テンプレートの登録")


def make_finalized_draft(
    store: Path,
    draft_id: str = "draft-001",
    *,
    at: str = AT_OK,
    variables: str = VARS_OK,
    scope: str = "schedule-confirm",
) -> dict:
    return must_ok(
        run_dg(
            store,
            [
                "draft",
                "--draft-id",
                draft_id,
                "--delegation-id",
                "del-001",
                "--template-id",
                "tmpl-001",
                "--scope",
                scope,
                "--variables",
                variables,
                "--at",
                at,
            ],
        ),
        f"送信案 {draft_id}",
    )


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

def test_period_out_requires_confirm(store: Path) -> None:
    seed_valid(store)
    out = must_ok(
        run_dg(
            store,
            [
                "evaluate",
                "--delegation-id",
                "del-001",
                "--template-id",
                "tmpl-001",
                "--scope",
                "schedule-confirm",
                "--variables",
                VARS_OK,
                "--at",
                "2026-12-01 00:00 JST",
            ],
        ),
        "期間外の判定",
    )
    if out.get("decision") != "要確認":
        raise Fail(f"期間外なのに判定が要確認ではない: {out}")
    if out.get("skip_confirm_ok") is not False:
        raise Fail(f"期間外なのに確認省略可になっている: {out}")
    if "有効期間外" not in " ".join(out.get("reasons") or []):
        raise Fail(f"期間外の理由が無い: {out}")
    draft = make_finalized_draft(store, "draft-period", at="2026-12-01 00:00 JST")
    if draft.get("decision") != "要確認" or draft.get("finalized") is not False:
        raise Fail(f"期間外の送信案が確定してしまった: {draft}")


def test_scope_out_requires_confirm(store: Path) -> None:
    seed_valid(store, scope="schedule-confirm")
    out = must_ok(
        run_dg(
            store,
            [
                "evaluate",
                "--delegation-id",
                "del-001",
                "--template-id",
                "tmpl-001",
                "--scope",
                "availability",
                "--variables",
                VARS_OK,
                "--at",
                AT_OK,
            ],
        ),
        "範囲外の判定",
    )
    if out.get("decision") != "要確認":
        raise Fail(f"範囲外なのに判定が要確認ではない: {out}")
    if "範囲外" not in " ".join(out.get("reasons") or []):
        raise Fail(f"範囲外の理由が無い: {out}")
    draft = make_finalized_draft(store, "draft-scope", scope="availability")
    if draft.get("decision") != "要確認" or draft.get("finalized") is not False:
        raise Fail(f"範囲外の送信案が確定してしまった: {draft}")


def test_unapproved_template_cannot_finalize(store: Path) -> None:
    seed_valid(store, approve=False)
    ev = must_ok(
        run_dg(
            store,
            [
                "evaluate",
                "--delegation-id",
                "del-001",
                "--template-id",
                "tmpl-001",
                "--scope",
                "schedule-confirm",
                "--variables",
                VARS_OK,
                "--at",
                AT_OK,
            ],
        ),
        "未承認テンプレの判定",
    )
    if ev.get("decision") != "確定不可":
        raise Fail(f"未承認なのに確定不可ではない: {ev}")
    if ev.get("finalize_ok") is not False or ev.get("template_approved") is not False:
        raise Fail(f"未承認なのに確定できることになっている: {ev}")
    draft = make_finalized_draft(store, "draft-unapproved")
    if draft.get("decision") != "確定不可" or draft.get("finalized") is not False:
        raise Fail(f"未承認テンプレの送信案が確定した: {draft}")
    must_err(
        run_dg(store, ["finalize", "--draft-id", "draft-unapproved", "--at", AT_OK]),
        "未承認の自動確定",
        "確定できません",
    )
    must_err(
        run_dg(
            store,
            ["finalize", "--draft-id", "draft-unapproved", "--confirmed", "--at", AT_OK],
        ),
        "未承認を確認付きで確定",
        "確定できません",
    )


def test_approved_in_conditions_can_skip_confirm(store: Path) -> None:
    seed_valid(store, approve=True)
    ev = must_ok(
        run_dg(
            store,
            [
                "evaluate",
                "--delegation-id",
                "del-001",
                "--template-id",
                "tmpl-001",
                "--scope",
                "schedule-confirm",
                "--variables",
                VARS_OK,
                "--at",
                AT_OK,
            ],
        ),
        "条件内の判定",
    )
    if ev.get("decision") != "確認省略可":
        raise Fail(f"条件内なのに確認省略可ではない: {ev}")
    if ev.get("skip_confirm_ok") is not True or ev.get("send_ok") is not True:
        raise Fail(f"条件内なのに省略または送信が不可: {ev}")
    draft = make_finalized_draft(store, "draft-ok")
    if draft.get("decision") != "確認省略可" or draft.get("finalized") is not True:
        raise Fail(f"条件内の送信案が自動確定されない: {draft}")
    rec = draft["record"]
    if rec.get("body") != "候補は 2026-11-18 です":
        raise Fail(f"本文の埋め込みが違う: {rec}")
    if rec.get("handoff_status") != "渡し前（未送信）":
        raise Fail(f"初期の送信状態が渡し前ではない: {rec}")


def test_out_of_conditions_and_ambiguous_require_confirm(store: Path) -> None:
    seed_valid(store)
    out = must_ok(
        run_dg(
            store,
            [
                "evaluate",
                "--delegation-id",
                "del-001",
                "--template-id",
                "tmpl-001",
                "--scope",
                "schedule-confirm",
                "--variables",
                json.dumps({"date": "2026-12-31"}, ensure_ascii=False),
                "--at",
                AT_OK,
            ],
        ),
        "条件外の判定",
    )
    if out.get("decision") != "要確認":
        raise Fail(f"条件外なのに要確認ではない: {out}")
    if "範囲外" not in " ".join(out.get("reasons") or []):
        raise Fail(f"条件外の理由が無い: {out}")

    amb = must_ok(
        run_dg(
            store,
            [
                "evaluate",
                "--delegation-id",
                "del-001",
                "--template-id",
                "tmpl-001",
                "--scope",
                "schedule-confirm",
                "--variables",
                json.dumps({"date": "2026-11-18", "extra": "x"}, ensure_ascii=False),
                "--at",
                AT_OK,
            ],
        ),
        "曖昧な判定",
    )
    if amb.get("decision") != "要確認":
        raise Fail(f"未知変数なのに要確認ではない: {amb}")
    if "曖昧" not in " ".join(amb.get("reasons") or []):
        raise Fail(f"曖昧の理由が無い: {amb}")


def test_share_only_cannot_send(store: Path) -> None:
    seed_valid(store, send_allowed=False, share_allowed=True)
    ev = must_ok(
        run_dg(
            store,
            [
                "evaluate",
                "--delegation-id",
                "del-001",
                "--template-id",
                "tmpl-001",
                "--scope",
                "schedule-confirm",
                "--variables",
                VARS_OK,
                "--at",
                AT_OK,
            ],
        ),
        "共有許可のみの判定",
    )
    if ev.get("send_ok") is not False or ev.get("send_allowed") is not False:
        raise Fail(f"共有許可だけで送信可になっている: {ev}")
    if ev.get("share_allowed") is not True:
        raise Fail(f"共有許可フラグが立っていない: {ev}")
    if ev.get("decision") != "確定不可":
        raise Fail(f"送信権限なしなのに確定不可ではない: {ev}")
    if "情報共有の許可だけでは送信できません" not in " ".join(ev.get("reasons") or []):
        raise Fail(f"共有と送信の区別が理由に無い: {ev}")
    draft = make_finalized_draft(store, "draft-share")
    if draft.get("finalized") is not False:
        raise Fail(f"送信権限なしの送信案が確定した: {draft}")
    must_err(
        run_dg(store, ["finalize", "--draft-id", "draft-share", "--confirmed", "--at", AT_OK]),
        "共有許可のみの確定",
        "確定できません",
    )
    # ストアを直接いじって確定済みにしても、引き渡しは拒否する
    data = load(store)
    data["drafts"]["draft-share"]["finalized"] = True
    save(store, data)
    must_err(
        run_dg(store, ["handoff", "--draft-id", "draft-share", "--at", AT_OK]),
        "共有許可のみの引き渡し",
        "情報共有の許可だけでは送信できません",
    )


def test_bot_confirm_required_for_sent(store: Path) -> None:
    seed_valid(store)
    draft = make_finalized_draft(store, "draft-send")
    if draft.get("finalized") is not True:
        raise Fail(f"前提の送信案が確定していない: {draft}")
    must_err(
        run_dg(
            store,
            ["confirm-sent", "--draft-id", "draft-send", "--confirmation-ref", "ack-1"],
        ),
        "渡し前に送信完了",
        "確認無しで送信完了にはできません",
    )
    ho = must_ok(
        run_dg(store, ["handoff", "--draft-id", "draft-send", "--at", AT_OK]),
        "引き渡し",
    )
    if ho.get("handoff_status") != "渡し済み（未確認）":
        raise Fail(f"引き渡し後の状態が違う: {ho}")
    must_err(
        run_dg(store, ["confirm-sent", "--draft-id", "draft-send", "--confirmation-ref", ""]),
        "確認参照なし",
        "確認",
    )
    # --confirmation-ref を省略
    must_err(
        run([sys.executable, str(DELEGATION), "--file", str(store), "confirm-sent", "--draft-id", "draft-send"]),
        "確認参照の引数なし",
        "confirmation-ref",
    )
    sent = must_ok(
        run_dg(
            store,
            [
                "confirm-sent",
                "--draft-id",
                "draft-send",
                "--confirmation-ref",
                "ack-1",
                "--at",
                "2026-11-17 10:05 JST",
            ],
        ),
        "確認付き送信完了",
    )
    if sent.get("handoff_status") != "送信完了":
        raise Fail(f"確認後も送信完了にならない: {sent}")
    replied = must_ok(
        run_dg(store, ["mark-reply", "--draft-id", "draft-send"]),
        "返事あり",
    )
    if replied.get("handoff_status") != "返事あり":
        raise Fail(f"返事ありが付かない: {replied}")
    rec = replied["record"]
    if not rec.get("reply_track") or rec["reply_track"].get("status") != "返事待ち":
        raise Fail(f"追跡行が無い: {rec}")


def test_no_rehandoff(store: Path) -> None:
    seed_valid(store)
    make_finalized_draft(store, "draft-once")
    first = must_ok(
        run_dg(store, ["handoff", "--draft-id", "draft-once", "--at", AT_OK]),
        "初回引き渡し",
    )
    if first.get("changed") is not True:
        raise Fail(f"初回引き渡しが変更扱いになっていない: {first}")
    second = must_ok(
        run_dg(store, ["handoff", "--draft-id", "draft-once", "--at", "2026-11-17 11:00 JST"]),
        "再引き渡し（未確認のまま）",
    )
    if second.get("changed") is not False:
        raise Fail(f"渡し直しを受け入れてしまった: {second}")
    if second.get("handoff_status") != "渡し済み（未確認）":
        raise Fail(f"再実行で状態が変わった: {second}")
    if "渡し直しはしません" not in (second.get("note") or ""):
        raise Fail(f"渡し直し防止の注記が無い: {second}")
    # 送信完了のあとではエラー
    must_ok(
        run_dg(
            store,
            [
                "confirm-sent",
                "--draft-id",
                "draft-once",
                "--confirmation-ref",
                "ack-2",
                "--at",
                "2026-11-17 11:05 JST",
            ],
        ),
        "送信完了",
    )
    must_err(
        run_dg(store, ["handoff", "--draft-id", "draft-once", "--at", "2026-11-17 12:00 JST"]),
        "送信完了後の渡し直し",
        "渡し直しはできません",
    )


def test_outcome_unknown_does_not_prompt_resend(store: Path) -> None:
    seed_valid(store)
    make_finalized_draft(store, "draft-unk")
    must_ok(run_dg(store, ["handoff", "--draft-id", "draft-unk", "--at", AT_OK]), "引き渡し")
    marked = must_ok(
        run_dg(
            store,
            [
                "mark-outcome-unknown",
                "--draft-id",
                "draft-unk",
                "--reason",
                "相手側の結果を確認できない",
            ],
        ),
        "実行結果不明",
    )
    if marked.get("prompt_retry") is not False or marked.get("retry_send") is not False:
        raise Fail(f"実行結果不明なのに再送を促している: {marked}")
    if marked.get("outcome_unknown") is not True:
        raise Fail(f"実行結果不明が立っていない: {marked}")
    decision = must_ok(
        run_dg(store, ["can-retry-send", "--draft-id", "draft-unk"]),
        "再送可否",
    )
    if decision.get("retry_ok") is not False or decision.get("prompt_retry") is not False:
        raise Fail(f"can-retry-send が再送を促している: {decision}")
    if "再実行しません" not in (decision.get("message") or "") and "再送しません" not in (
        decision.get("message") or ""
    ):
        raise Fail(f"再送しない旨のメッセージが無い: {decision}")
    must_err(
        run_dg(store, ["handoff", "--draft-id", "draft-unk", "--at", "2026-11-17 12:00 JST"]),
        "不明時の渡し直し",
        "引き渡しのやり直しはしません",
    )
    # 確認が来ても、不明のままで送信完了には進めない（先に照合）
    must_err(
        run_dg(
            store,
            [
                "confirm-sent",
                "--draft-id",
                "draft-unk",
                "--confirmation-ref",
                "ack-3",
            ],
        ),
        "不明時の送信完了",
        "照合",
    )


def test_check_detects_illegal(store: Path) -> None:
    """コマンド経路を迂回して不正なストアを書き、check が検出することを確かめる。"""
    seed_valid(
        store,
        send_allowed=False,
        share_allowed=True,
        valid_from="2026-11-01 00:00 JST",
        valid_until="2026-11-10 23:59 JST",
    )
    # 正常な空の check（委任と未使用テンプレだけ）は問題なし
    clean = run_dg(store, ["check", "--json"])
    if clean.returncode != 0:
        raise Fail(f"不正を入れる前の check が失敗: {clean.stdout} {clean.stderr}")

    data = load(store)
    # 1) 期間切れ委任での送信
    data["drafts"]["draft-expired"] = {
        "draft_id": "draft-expired",
        "delegation_id": "del-001",
        "template_id": "tmpl-001",
        "counterpart_id": "role-partner",
        "scope": "schedule-confirm",
        "variables": {"date": "2026-11-18"},
        "body": "候補は 2026-11-18 です",
        "decision": "要確認",
        "finalized": True,
        "confirmed_at": "2026-11-17 10:00 JST",
        "confirmed_by": "operator",
        "handoff_status": "渡し済み（未確認）",
        "handed_off_at": "2026-11-17 10:00 JST",
        "confirmation_received_at": "",
        "confirmation_ref": "",
        "reply_track": None,
        "outcome_unknown": False,
        "outcome_unknown_reason": "",
        "retry_send": False,
        "created_at": "2026-11-17 09:00:00 JST",
        "updated_at": "2026-11-17 10:00:00 JST",
    }
    # 2) 共有許可のみでの送信（同じ委任は send_allowed=False）
    # 上の draft-expired でも送信権限の問題が出る。別委任を足して権限だけを見る行も置く。
    data["delegations"]["del-share"] = {
        "delegation_id": "del-share",
        "counterpart_id": "role-other",
        "scope": ["schedule-confirm"],
        "valid_from": "2026-11-01 00:00 JST",
        "valid_until": "2026-11-30 23:59 JST",
        "share_allowed": True,
        "send_allowed": False,
        "created_at": "2026-11-17 09:00:00 JST",
        "updated_at": "2026-11-17 09:00:00 JST",
    }
    data["templates"]["tmpl-share"] = {
        "template_id": "tmpl-share",
        "delegation_id": "del-share",
        "approved": True,
        "body": "候補は {date} です",
        "usage_conditions": {"date": ["2026-11-18"]},
        "created_at": "2026-11-17 09:00:00 JST",
        "updated_at": "2026-11-17 09:00:00 JST",
    }
    data["drafts"]["draft-share-only"] = {
        "draft_id": "draft-share-only",
        "delegation_id": "del-share",
        "template_id": "tmpl-share",
        "counterpart_id": "role-other",
        "scope": "schedule-confirm",
        "variables": {"date": "2026-11-18"},
        "body": "候補は 2026-11-18 です",
        "decision": "確定不可",
        "finalized": True,
        "confirmed_at": "",
        "confirmed_by": "",
        "handoff_status": "渡し済み（未確認）",
        "handed_off_at": "2026-11-17 10:00 JST",
        "confirmation_received_at": "",
        "confirmation_ref": "",
        "reply_track": None,
        "outcome_unknown": False,
        "outcome_unknown_reason": "",
        "retry_send": False,
        "created_at": "2026-11-17 09:00:00 JST",
        "updated_at": "2026-11-17 10:00:00 JST",
    }
    # 3) 未承認テンプレの確定
    data["templates"]["tmpl-raw"] = {
        "template_id": "tmpl-raw",
        "delegation_id": "del-share",
        "approved": False,
        "body": "候補は {date} です",
        "usage_conditions": {"date": ["2026-11-18"]},
        "created_at": "2026-11-17 09:00:00 JST",
        "updated_at": "2026-11-17 09:00:00 JST",
    }
    data["drafts"]["draft-unapproved"] = {
        "draft_id": "draft-unapproved",
        "delegation_id": "del-share",
        "template_id": "tmpl-raw",
        "counterpart_id": "role-other",
        "scope": "schedule-confirm",
        "variables": {"date": "2026-11-18"},
        "body": "候補は 2026-11-18 です",
        "decision": "確定不可",
        "finalized": True,
        "confirmed_at": "",
        "confirmed_by": "",
        "handoff_status": "渡し前（未送信）",
        "handed_off_at": "",
        "confirmation_received_at": "",
        "confirmation_ref": "",
        "reply_track": None,
        "outcome_unknown": False,
        "outcome_unknown_reason": "",
        "retry_send": False,
        "created_at": "2026-11-17 09:00:00 JST",
        "updated_at": "2026-11-17 09:00:00 JST",
    }
    # 4) 確認欠落での送信完了
    data["drafts"]["draft-no-ack"] = {
        "draft_id": "draft-no-ack",
        "delegation_id": "del-share",
        "template_id": "tmpl-share",
        "counterpart_id": "role-other",
        "scope": "schedule-confirm",
        "variables": {"date": "2026-11-18"},
        "body": "候補は 2026-11-18 です",
        "decision": "要確認",
        "finalized": True,
        "confirmed_at": "",
        "confirmed_by": "",
        "handoff_status": "送信完了",
        "handed_off_at": "2026-11-17 10:00 JST",
        "confirmation_received_at": "",
        "confirmation_ref": "",
        "reply_track": None,
        "outcome_unknown": False,
        "outcome_unknown_reason": "",
        "retry_send": False,
        "created_at": "2026-11-17 09:00:00 JST",
        "updated_at": "2026-11-17 10:00:00 JST",
    }
    # 5) 実行結果不明なのに再実行フラグ
    data["drafts"]["draft-retry-flag"] = {
        "draft_id": "draft-retry-flag",
        "delegation_id": "del-share",
        "template_id": "tmpl-share",
        "counterpart_id": "role-other",
        "scope": "schedule-confirm",
        "variables": {"date": "2026-11-18"},
        "body": "候補は 2026-11-18 です",
        "decision": "要確認",
        "finalized": True,
        "confirmed_at": "",
        "confirmed_by": "",
        "handoff_status": "渡し済み（未確認）",
        "handed_off_at": "2026-11-17 10:00 JST",
        "confirmation_received_at": "",
        "confirmation_ref": "",
        "reply_track": None,
        "outcome_unknown": True,
        "outcome_unknown_reason": "結果不明",
        "retry_send": True,
        "created_at": "2026-11-17 09:00:00 JST",
        "updated_at": "2026-11-17 10:00:00 JST",
    }
    save(store, data)

    cp = run_dg(store, ["check", "--json"])
    if cp.returncode != 1:
        raise Fail(f"不正があるのに check の終了コードが 1 ではない: {cp.returncode}\n{cp.stdout}\n{cp.stderr}")
    payload = json.loads(cp.stdout)
    kinds = {issue["種別"] for issue in payload.get("問題") or []}
    needed = {
        "期間切れ": "期間切れ委任での送信",
        "送信権限": "共有許可のみでの送信",
        "未承認テンプレ": "未承認テンプレの確定",
        "確認欠落": "確認欠落での送信完了",
        "再実行フラグ": "実行結果不明なのに再送フラグ",
    }
    missing = [label for code, label in needed.items() if code not in kinds]
    if missing:
        raise Fail(f"check が検出していない項目: {missing}\n問題: {payload.get('問題')}")


def test_requires_file_flag() -> None:
    cp = run(
        [
            sys.executable,
            str(DELEGATION),
            "add-delegation",
            "--delegation-id",
            "del-x",
            "--counterpart-id",
            "role-x",
            "--scope",
            "schedule-confirm",
            "--valid-from",
            "2026-11-01 00:00 JST",
            "--valid-until",
            "2026-11-30 23:59 JST",
        ]
    )
    if cp.returncode == 0:
        raise Fail("--file なしで動いてしまった（本番パスを持ちかねない）")
    if "--file" not in (cp.stderr + cp.stdout):
        raise Fail("--file 必須の案内が無い")


def test_add_idempotent_and_audit(store: Path) -> None:
    seed_valid(store)
    again = must_ok(
        run_dg(
            store,
            [
                "add-delegation",
                "--delegation-id",
                "del-001",
                "--counterpart-id",
                "role-other",
                "--scope",
                "availability",
                "--valid-from",
                "2026-12-01 00:00 JST",
                "--valid-until",
                "2026-12-31 23:59 JST",
                "--send-allowed",
            ],
        ),
        "委任の再登録",
    )
    if again.get("created") is not False:
        raise Fail(f"再登録が新規扱いになった: {again}")
    rec = again["record"]
    if rec.get("counterpart_id") != "role-partner":
        raise Fail(f"再登録で既存が上書きされた: {rec}")
    tmpl_again = must_ok(
        run_dg(
            store,
            [
                "add-template",
                "--template-id",
                "tmpl-001",
                "--delegation-id",
                "del-001",
                "--body",
                "別の本文",
            ],
        ),
        "テンプレートの再登録",
    )
    if tmpl_again.get("created") is not False:
        raise Fail(f"テンプレートが二重作成された: {tmpl_again}")
    audit = must_ok(run_dg(store, ["audit"]), "監査ログ")
    if audit.get("件数", 0) < 2:
        raise Fail(f"監査ログが追記されていない: {audit}")
    for row in audit.get("audit") or []:
        # 識別子のみ。本文や固有名を残していないこと（キーが想定どおり）
        extra = set(row) - {
            "at",
            "actor_id",
            "action",
            "delegation_id",
            "template_id",
            "draft_id",
            "from_status",
            "to_status",
            "decision",
        }
        if extra:
            raise Fail(f"監査ログに想定外の項目がある: {extra}")
        if row.get("actor_id") != "operator":
            raise Fail(f"監査の主体が識別子ではない: {row}")


def test_existing_tools_untouched() -> None:
    """このテスト実行中に既存の本体を書き換えていないこと（存在確認）。"""
    for path, label in (
        (CASEBOOK, "casebook.py"),
        (QUEUE, "queue.py"),
        (WORKLOG, "worklog.py"),
    ):
        if not path.exists():
            raise Fail(f"既存ファイルが見つからない: {label}")
        if not path.is_file():
            raise Fail(f"既存ファイルが通常ファイルではない: {label}")


# ---------------------------------------------------------------------------
# ランナー
# ---------------------------------------------------------------------------

TESTS = [
    ("期間外は要確認", test_period_out_requires_confirm, True),
    ("範囲外は要確認", test_scope_out_requires_confirm, True),
    ("未承認テンプレは確定不可", test_unapproved_template_cannot_finalize, True),
    ("承認済み＋条件内は確認省略可", test_approved_in_conditions_can_skip_confirm, True),
    ("条件外・曖昧は要確認", test_out_of_conditions_and_ambiguous_require_confirm, True),
    ("共有許可だけでは送信不可", test_share_only_cannot_send, True),
    ("Bot間確認なしで送信完了不可", test_bot_confirm_required_for_sent, True),
    ("渡し直し防止", test_no_rehandoff, True),
    ("実行結果不明で自動再送なし", test_outcome_unknown_does_not_prompt_resend, True),
    ("check が各不正を検出", test_check_detects_illegal, True),
    ("--file 必須（既定の本番パスを持たない）", lambda _s: test_requires_file_flag(), False),
    ("登録の冪等と監査ログの追記", test_add_idempotent_and_audit, True),
    ("既存の本体ファイルが残っている", lambda _s: test_existing_tools_untouched(), False),
]


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="yoteiban-delegation-test-", dir="/tmp"))
    ensure_tmp(root)
    print(f"隔離ディレクトリ: {root}")
    passed = 0
    failed = 0
    errors: list[str] = []
    for i, (name, fn, needs_store) in enumerate(TESTS, start=1):
        workdir = root / f"t{i:02d}"
        workdir.mkdir(parents=True, exist_ok=True)
        store = workdir / "store.json"
        try:
            fn(store)
        except Exception as e:
            failed += 1
            detail = "".join(traceback.format_exception(e))
            errors.append(f"[失敗] {i}. {name}\n{detail}")
            print(f"[失敗] {i}. {name}")
        else:
            passed += 1
            print(f"[通過] {i}. {name}")
    print(f"\n結果: 通過 {passed} / 失敗 {failed} / 全 {passed + failed}")
    if errors:
        print("\n------ 失敗の詳細 ------")
        print("\n".join(errors))
    print(f"作業ディレクトリは残しています: {root}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
