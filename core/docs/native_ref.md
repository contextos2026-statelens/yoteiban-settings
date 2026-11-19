# native_ref の正規形

受け口（intake）と台帳の ingest は、必ず `yoteiban_core.refs.normalize_native_ref(source, raw)` を通します。
既存の行は書き換えません。照合のときは、保存済みの値も同じ関数で読んで比べます。

- **Gmail**（source が gmail / mail / email / gmail-…）：RFC の Message-ID。前後の空白と外側の山かっこを除き、小文字にする。例：`<ABC@example.com>` → `abc@example.com`。API の id しか無いときは、その id を小文字で使う。
- **iMessage**（source が imessage / imessage-…）：GUID があれば `guid:`＋小文字の UUID（外側の `{}` を除く）。GUID が無く ROWID だけのときは `rowid:`＋数字。GUID と ROWID を互いに変換することはできないので、見張り番は GUID があれば GUID を送る。
- **カレンダー**（source が calendar / calendar-…）：`event:`＋イベントID。更新があれば `@` のあとに更新の印（更新時刻、または消えたときの `gone`）。例：`event:abc123@2026-11-19T08:00:00Z`。イベントIDの大文字小文字は変えない。
- **ほかの入口**：前後の空白だけ除く。

照合用の入口名は `canonicalize_source` でまとめます（gmail の別名、imessage の別名、calendar の別名）。digest（input_id）もこの名前と正規形の native_ref から作ります。
