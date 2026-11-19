# 関所（gate）の例外と下書きカレンダー

## 下書きカレンダーは作らない（2026-11-19 本人が決定）

- `cal_draft` は関所で拒否する。承認なしで書いてよい下書きの書き先は無い。
- 承認待ちの案はカレンダーに書かず、台帳と候補箱に置く。出発と準備の知らせはリマインダー。
- 台帳の種類名 `cal_draft` は、過去の行を読むためだけに残す。設定の `cal_draft_id` は不要（あっても使わない）。

## 承認なしで個人用カレンダーへ「追加」だけを通す2つの例外

操作に `grounds` と根拠 `basis` を付ける。1つでも欠けたら拒否。書き先は `cal_personal` だけ（許可リスト照合・ID照合・家用の読み取り専用はそのまま）。
承認・委任と一緒には使えない。直す・動かす・消す（`action` が add 以外、`payload.event_id` あり）は例外にしない。招待や送信を含む内容（`attendees` など）も通さない。

| grounds | 件名 | basis に要るもの |
|---|---|---|
| `forced_add`（強制追加） | 「［自動］」で始まる | `source_id` が設定 `trusted_source_ids` にある / `datetime_in_text: true` / `guessed`・`forwarded`・`group`・`has_instruction` がすべて `false` / `notify_style` が「全部確認」でない。「静かに」なら `fixed_time: true` のときだけで、知らせは最小 |
| `tentative`（［仮］） | 「［仮］」で始まる | `requested_by_owner: true` と `owner_words`（本人の言葉） / `from_received`・`other_request`・`needs_others`・`needs_reservation` がすべて `false` / `notify_style` が「全部確認」でない / `tentative_use` が「使わない」でない / いまの［仮］が上限（設定 `tentative_max`、既定5）未満。何も送らない |

- `notify_style` と `tentative_use` は、設定ファイルに値があればそちらを優先する。
- `payload.event_start` はタイムゾーン付きの日時（時刻あり）。
- 書く順は今のまま：`check` → 書く → 読み戻し → `result`。同じ内容の二重書きは止め、結果不明は `reconcile` まで再実行しない。
- ［仮］を本人の一言で確定・移動・削除したら、カレンダーは既存の承認の道で直し、`close_tentative`（how＝確定・移動・削除）で件数から外す。
