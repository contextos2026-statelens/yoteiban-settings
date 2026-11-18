# core
予定ばんの共通データ層と設定を置きます。
`yoteiban_core.db` が SQLite 接続、スキーマ移行、初期設定を担当します。
SQL の版は `schema/` に番号順で追加します。
設定例は `config.example.json` にあります。
秘密情報は環境変数から読み込みます。
試験は `cd core && python3 -m unittest discover -s tests` で実行します。
