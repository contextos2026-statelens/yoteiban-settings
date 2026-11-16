# 第2段階試作（箱内のみ）

依頼（入力1件）と案件（1依頼から複数可）を、3系統の状態を分けて持つ試作モジュール。

- 本体: `casebook.py`（Python 3、標準ライブラリのみ）
- 隔離テスト: `test_casebook.py`
- **本番適用はしない。** スキル／ルーチンへの配線、Notion・カレンダー・外部送信、現行の `queue.py` / `worklog.py` の変更は行わない。
- 対象ストアは `--file` 必須。既定の本番パスは持たない。検証は必ず `/tmp` 配下の一時ファイルを指定する。

既存ツール（`queue.py` / `worklog.py`）は、入力1件の処理状態と作業記録を扱う第1段階の道具である。本試作はそれらを置き換えない。処理状態の語彙は `queue.py` に合わせ、案件状態と引き渡し・送信状態を別フィールドで足している。

---

## 3系統（混同しない）

1. **処理の成否**（機械。依頼1件）  
   `受信済み` → `分析中` → `保存済み` → `完了` ／ `失敗・要再試行`  
   「Webhook成功／入口の読み取り成功」は、この層の「完了」ではない。
2. **案件の状態**（人の判断。案件1件）  
   `提案前` → `提案中` → `承認待ち` → `承認済み` → `実行中` → `実行完了` ／ `実行結果不明（要照合）` ／ `却下` ／ `期限切れ` ／ `保留`  
   **「承認待ち」はここだけ。**
3. **引き渡し・送信状態**  
   `渡し前（未送信）` → `渡し済み（未確認）` → `送信完了` → `返事あり`  
   「返事待ち」の追跡行は、送信完了のあとだけ作る。相手ボットからの確認が無ければ `送信完了` にできない。渡し直しはしない。

不正な遷移（例: 処理の成否に「承認待ち」を入れる、確認無しで送信完了にする、実行結果不明から実行中へ戻す）は拒否する。

---

## ストア

JSON の辞書1ファイル。原子的に書き換える（一時ファイルへ書いてから rename）。依頼ID・案件IDをキーにする。

案件IDは `{依頼ID}#{案件キー}`。案件キーは呼び出し側が安定した値を渡す（内容ハッシュも可）。同じキーでの再作成は `created: false` を返し、既存を二重に作らない。

---

## サブコマンド

共通: `--file /tmp/casebook.json`（必須）

### record（依頼の登録・冪等）

```bash
python3 casebook.py --file /tmp/casebook.json record \
  --request-id req-001 --source mail --received-at "2026-11-17 01:00 JST"
```

処理状態は `受信済み`。同じ依頼IDの再登録は `created: false`（既存を返す。状態は上書きしない）。

### add-case / add-cases（案件の作成・冪等）

```bash
python3 casebook.py --file /tmp/casebook.json add-case \
  --request-id req-001 --case-key meet --summary "会合の候補" --mark-saved

python3 casebook.py --file /tmp/casebook.json add-cases \
  --request-id req-001 --mark-saved <<'JSON'
[{"case_key":"meet","summary":"会合の候補"},{"case_key":"travel","summary":"移動の候補"}]
JSON
```

`--case-key` の代わりに `--content`（または配列側の `content`）を渡すと、内容のハッシュをキーにする。保存直後に止まっても、同じキーの再実行では既存を返し、足りない分だけ追加する。

### set-processing / set-case（系統ごとの遷移）

```bash
python3 casebook.py --file /tmp/casebook.json set-processing --request-id req-001 --status 分析中
python3 casebook.py --file /tmp/casebook.json set-processing --request-id req-001 --status 完了 --no-save-reason "予定に関係なし"
python3 casebook.py --file /tmp/casebook.json set-case --case-id 'req-001#meet' --status 承認待ち
```

処理の完了には、保存済みか保存不要の理由が必要。案件を `実行結果不明（要照合）` から `実行完了` にするときだけ `--reconciled`（照合済み）が要る。再実行ではない。

### handoff / confirm-sent / mark-reply（Bot間の完了確認）

```bash
python3 casebook.py --file /tmp/casebook.json handoff --case-id 'req-001#meet'
python3 casebook.py --file /tmp/casebook.json confirm-sent --case-id 'req-001#meet' --confirmation-ref ack-1
python3 casebook.py --file /tmp/casebook.json mark-reply --case-id 'req-001#meet'
```

`handoff` は `渡し済み（未確認）` にする。確認参照付きの `confirm-sent` だけが `送信完了` にできる。`mark-reply` は送信完了のあとだけ、返事待ちの追跡行を作る。

### mark-outcome-unknown / can-retry-execution（安全側）

```bash
python3 casebook.py --file /tmp/casebook.json mark-outcome-unknown \
  --case-id 'req-001#meet' --reason "変更結果を確認できない"
python3 casebook.py --file /tmp/casebook.json can-retry-execution --case-id 'req-001#meet'
```

`実行結果不明（要照合）` のとき、`can-retry-execution` は `retry_ok: false` / `prompt_retry: false` を返し、外部送信・カレンダー変更の再実行を促さない。実行中への戻しや引き渡しのやり直しは拒否する。

### check / show（読むだけ）

```bash
python3 casebook.py --file /tmp/casebook.json check
python3 casebook.py --file /tmp/casebook.json check --json
python3 casebook.py --file /tmp/casebook.json show --request-id req-001
```

`check` は3系統の混同、確認無しの送信完了、送信前の追跡行、実行結果不明なのに再実行フラグが立っていること、依頼と案件の対応ずれなどを検出し、件数と内容を返す。問題があれば終了コード 1。

---

## テスト

本番ストアは使わない。テスト自身が `/tmp` に作業用ディレクトリを作り、そこへだけ書く。

```bash
python3 /home/box/agent-data/yoteiban-tools/stage2/test_casebook.py
```

対象: 依頼登録の冪等、1依頼→複数案件、案件作成の冪等、不正遷移の拒否、実行結果不明時に再実行を促さない、確認無しで送信完了にできない、check の検出、`--file` 必須、現行ツールのハッシュ一致、`queue.py` / `worklog.py` の隔離スモーク。

---

## やらないこと

- スキル・ルーチン・記憶・Notion・Git・外部送信・カレンダーの変更
- 既存の `queue.py` / `worklog.py` / `README.md` の書き換え
- 既定パスへの書き込み（`--file` が無いときは動かない）
- 固有値（氏名、連絡先、カレンダーID、表の識別子、入口の固有名、送る相手やボット名など）をコードや記録に書くこと

---

## 外部送信の委任管理（試作）

本体: `delegation.py`。隔離テスト: `test_delegation.py`。

**これは箱内の試作である。本番には適用しない。** スキル／ルーチンへの配線、Notion・カレンダー・外部への実送信は行わない。送信前に文面を本人が確認する規則は、検証が終わるまで維持する。既存の `casebook.py` / `queue.py` / `worklog.py` は変更しない。

対象ストアは `--file` 必須。既定の本番パスは持たない。検証は必ず `/tmp` 配下の一時ファイルを指定する。書き込みは一時ファイルへ書いてから rename する。

引き渡し・送信の語彙は `casebook.py` と同じく、次の4つを混同しない。

`渡し前（未送信）` → `渡し済み（未確認）` → `送信完了` → `返事あり`

相手側からの完了確認が無ければ `送信完了` にできない。確認が無いまま渡し直しはしない。実行結果が確認できないときは `実行結果不明（要照合）` とし、自動では再送しない。

### 判定の考え方

- 委任には、相手の識別子（役割名など。呼び出し側が渡す）、内容の範囲、有効期間、**情報共有の許可**、**送信権限**を持つ。共有と送信は別フィールドである。共有許可だけでは送信できない。
- 期間外・範囲外、使用条件の範囲外、条件が曖昧なときは `要確認` を返す。自動送信しない。
- テンプレートが未承認なら、送信案は `確定不可` であり、自動では確定できない。
- 承認済みテンプレートかつ変数が使用条件の範囲内で、期間・範囲・送信権限を満たすときは `確認省略可`。送信案は自動で確定できる（実送信はしない）。
- 許可が曖昧なら確認する。自動送信しない。

### サブコマンド

共通: `--file /tmp/delegation.json`（必須）。操作の主体は `--actor`（識別子。既定は `operator`）。

```bash
python3 delegation.py --file /tmp/delegation.json add-delegation \
  --delegation-id del-001 --counterpart-id role-partner \
  --scope schedule-confirm --valid-from "2026-11-01 00:00 JST" \
  --valid-until "2026-11-30 23:59 JST" --share-allowed --send-allowed

python3 delegation.py --file /tmp/delegation.json add-template \
  --template-id tmpl-001 --delegation-id del-001 \
  --body "候補は {date} です" --allowed-vars '{"date":["2026-11-18","2026-11-19"]}' --approve

python3 delegation.py --file /tmp/delegation.json evaluate \
  --delegation-id del-001 --template-id tmpl-001 --scope schedule-confirm \
  --variables '{"date":"2026-11-18"}' --at "2026-11-17 10:00 JST"

python3 delegation.py --file /tmp/delegation.json draft \
  --draft-id draft-001 --delegation-id del-001 --template-id tmpl-001 \
  --scope schedule-confirm --variables '{"date":"2026-11-18"}' --at "2026-11-17 10:00 JST"

python3 delegation.py --file /tmp/delegation.json finalize --draft-id draft-001
python3 delegation.py --file /tmp/delegation.json handoff --draft-id draft-001
python3 delegation.py --file /tmp/delegation.json confirm-sent --draft-id draft-001 --confirmation-ref ack-1
python3 delegation.py --file /tmp/delegation.json mark-reply --draft-id draft-001
python3 delegation.py --file /tmp/delegation.json mark-outcome-unknown --draft-id draft-001 --reason "結果を確認できない"
python3 delegation.py --file /tmp/delegation.json can-retry-send --draft-id draft-001
python3 delegation.py --file /tmp/delegation.json check --json
python3 delegation.py --file /tmp/delegation.json audit
```

`finalize` は、判定が `確認省略可` のときだけ自動で確定する。`要確認` のときは `--confirmed`（文面を人が見た）が要る。未承認テンプレートや送信権限が無いときは、確認付きでも確定できない。`handoff` は確定済みかつ送信権限があり、期間・範囲を満たすときにだけ `渡し済み（未確認）` にする。実送信はしない。

`check` は、期間切れの委任での送信、共有許可のみでの送信、未承認テンプレートでの確定、確認欠落での送信完了、実行結果不明なのに再実行フラグが立っていることなどを検出し、問題があれば終了コード 1 を返す。監査ログは追記専用で、識別子のみを残す。

### テスト

```bash
python3 /home/box/agent-data/yoteiban-tools/stage2/test_delegation.py
```

テスト自身が `/tmp` に作業用ディレクトリを作り、そこへだけ書く。対象: 期間外・範囲外は要確認、未承認テンプレは確定不可、承認済みかつ条件内は確認省略可、共有許可だけでは送信不可、Bot間確認なしで送信完了にできない、渡し直し防止、実行結果不明で自動再送なし、check の検出、`--file` 必須。
