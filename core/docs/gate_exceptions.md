# 関所（gate）の例外と下書きカレンダー

## 下書きカレンダーは作らない（2026-11-19 本人が決定）

- `cal_draft` は関所で拒否する。承認なしで書いてよい下書きの書き先は無い。
- 承認待ちの案はカレンダーに書かず、台帳と候補箱に置く。出発と準備の知らせはリマインダー。
- 台帳の種類名 `cal_draft` は、過去の行を読むためだけに残す。設定の `cal_draft_id` は不要（あっても使わない）。

## 承認なしで個人用カレンダーへ「追加」だけを通す2つの例外

操作に `grounds` と根拠 `basis` を付ける。1つでも欠けたら拒否。書き先は `cal_personal` だけ（許可リスト照合・ID照合・家用の読み取り専用はそのまま）。
承認・委任と一緒には使えない。直す・動かす・消す（`action` が add 以外、`payload.event_id` あり）はこの2つの例外にしない（予定ばんが置いた［仮］だけは下の `self_resolve` で扱う）。招待や送信を含む内容（`attendees` など）も通さない。

| grounds | 件名 | basis に要るもの |
|---|---|---|
| `forced_add`（強制追加） | 「［自動］」で始まる | `source_id` が設定 `trusted_source_ids` にある / `datetime_in_text: true` / `guessed`・`forwarded`・`group`・`has_instruction` がすべて `false` / `notify_style` が「全部確認」でない。「静かに」なら `fixed_time: true` のときだけで、知らせは最小 |
| `tentative`（［仮］） | 「［仮］」で始まる | `requested_by_owner: true` と `owner_words`（本人の言葉） / `from_received`・`other_request`・`needs_others`・`needs_reservation` がすべて `false` / `notify_style` が「全部確認」でない / `tentative_use` が「使わない」でない / いまの［仮］が上限（設定 `tentative_max`、既定5）未満。何も送らない |

- `notify_style` と `tentative_use` は、設定ファイルに値があればそちらを優先する。
- `payload.event_start` はタイムゾーン付きの日時（時刻あり）。
- 書く順は今のまま：`check` → 書く → 読み戻し → `result`。同じ内容の二重書きは止め、結果不明は `reconcile` まで再実行しない。
- ［仮］を本人の一言で確定・移動・削除したら、カレンダーは既存の承認の道で直し、`close_tentative`（how＝確定・移動・削除）で件数から外す。

## 予定ばんが自分で置いた［仮］だけ、聞かずに直す例外（2026-11-19 本人が決定）

予定ばんの処理だけで起きた重なり・ダブりは、本人に聞かずに直して、あとで1行で知らせる（元に戻す方法つき）。
操作に `grounds: "self_resolve"` と根拠 `basis` を付ける。書き先は `cal_personal` だけ（ID照合・家用の読み取り専用はそのまま）。承認・委任とは一緒に使えない。招待や送信を含む内容は通さない。通知の型が「全部確認」なら通さない。

| basis.kind | action | 条件 |
|---|---|---|
| `tentative_move` | `move` か `update` | `origin_op_id` が、予定ばんが例外 `tentative` で置き、読み戻し済み（成功）で、まだ閉じていない［仮］の操作。`payload.event_id` がその操作に記録された予定IDと一致。直した後も件名は「［仮］」で始まり「［自動］」ではない。`event_start` はタイムゾーン付き、`event_end` は始まりより後 |
| `duplicate_delete` | `delete` | 消す方（`origin_op_id`）と残す方（`keep_op_id`）がどちらも上の条件を満たす［仮］で、同じ入力（`input_ref`）から置かれ、件名・始まり・終わりがまったく同じ。消してよいのは後から置いた方だけ |

- 通さないもの：本人が承認した予定・本人が作った予定・［自動］（強制追加）・他の人が関わる予定・家用カレンダー・下書きカレンダー・予定IDの記録が無い［仮］・読み戻し前の［仮］。
- 照合のため、［仮］の `result` で `event_id`（書いた予定のID）を記録する（`result(..., event_id=...)`、CLI は `event_id`）。［仮］の追加では `input_ref`（もとの入力）・件名・始まり・終わりも記録する。
- `check` の返事と台帳の操作に、元に戻すための `before`（直す前の件名・始まり・終わり・予定ID）と `after`（直した後。消したら `null`）を残す。知らせは `notify_level: "1行"`。
- `result` で成功になると、元の［仮］へ反映する：動かした時刻を `current_start`・`current_end` に、消したら件数から外す（`tentative_closed`）。
