# Mac常駐・Readerの本人操作ガイド

この手順は本人が使うことを選び、接続準備を承認した後に進めます。鍵・URL・会話本文はチャットやターミナル出力に貼らず、Mac上の設定ファイルまたは外部サービスの入力欄だけに入れます。

## 1. 端末と実行環境を確認

1. Mac miniで `bash redesign/mac/scripts/preflight.sh` を実行します。点検結果は `~/yoteiban-watch/state/preflight.json` に所有者限定で保存されます。設定の値や秘密は表示しません。
2. `bash redesign/mac/scripts/status.sh` で選ばれたPythonの実体、見張り番・時計、最後の心拍、Readerの導入状況を確認します。
3. `bash redesign/mac/scripts/install.sh --dry-run` で予定される操作だけを確認します。出力を見てよければ本人の承認後に `bash redesign/mac/scripts/install.sh` を実行します。config.jsonとenvは初回に無い場合だけ作成されます。更新前のアプリとplistはstate/backupsに控えます。

## 2. Messagesとフルディスクアクセス

1. Macの「メッセージ」を開き、本人のApple Accountでサインイン済みか、必要な会話がMacに同期されているかを確認します。
2. 見張り番を使う場合、`status.sh` に表示された実行Pythonを控えます。Readerも同じ `state/python-path` を使います。
3. macOS Ventura以降は「システム設定」→「プライバシーとセキュリティ」→「フルディスクアクセス」を開きます。古いmacOSでは「システム環境設定」内の「セキュリティとプライバシー」→「プライバシー」から開きます。
4. `+` から手順2のPython実体を追加して有効にします。権限は付与する本人が画面で操作します。実行ファイルを更新した場合は、同じ画面に表示される実体と `status.sh` を再確認します。
5. 設定を変えた後は、対象のログイン項目を終了して再度起動し、`doctor --only preflight` とReader/見張り番の状態を確認します。権限を自動で付けたり、他のラベルを停止したりしません。

## 3. Webhookと鍵

- `config.json` の `webhook_url` と心拍URLは本人がMac上で設定します。`env` には `YOTEIBAN_WEBHOOK_KEY`、必要なら `YOTEIBAN_WEBHOOK_AUTH` を設定します。外部AIを使う選択をした場合だけ `YOTEIBAN_AI_KEY` を設定します。
- 入力欄がある導入画面では秘密値をその欄へ直接渡し、チャットには表示しません。外部監視サービスへの登録は本人がサービス画面でチェックを作り、間隔・猶予・通知先を指定し、対応するURLをMacのprivate configへ入れます。有料プランを登録しません。
- WebhookへのHTTP成功は受け口への到達を示します。台帳の `input_id` が返ってきて初めて「登録成功」です。4xxは再送せず、429は短い有効な `Retry-After` があるときだけ最大5分の範囲で再試行します。

## 4. Readerと個別心拍

1. Readerを使う場合は `bash redesign/mac/reminder_reader/install.sh --dry-run` を先に確認し、本人が「使う」と選び承認した後に `bash redesign/mac/reminder_reader/install.sh 使う` を実行します。見張り番が未導入でも必要なReaderモジュール、config/envの雛形、同じPython選択器を配置します。既存設定は上書きしません。
2. 「システム設定」→「一般」→「ログイン項目」で「Yoteiban Reminder Reader」が登録され有効かを本人が確認します。
3. 監視サービスを分けるには `reader_heartbeat_url` を使います。空欄なら見張り番と同じ `heartbeat_url` に送りますが、個別の停止監視にはなりません。`status.sh` は「共用」と表示します。
4. 通知記録へのアクセスは手順2と同じフルディスクアクセス設定です。iPhoneの通知・位置許可、同じApple Account、リスト共有は本人が確認します。Macロック中・再起動後も通知が届くかは実機で確かめます。

## 5. 接続テストと復旧

1. まず影響のない `python -m yoteiban_watch.doctor --config ~/yoteiban-watch/config.json --only configuration` と `--only signature` を実行します。
2. 架空の1件を本番受け口へ送るテストは、本人がこの1件の送信を許可した場合だけ `--only webhook --send-webhook-test` を実行します。成功HTTPと台帳登録は別々に表示します。
3. 心拍通知の試験は本人が通知を1回発生させることを許可した後だけ `--only heartbeat --send-heartbeat-fail` を実行します。本人に通知が届いたことを確認してから、`--only heartbeat --confirm-heartbeat-delivery` で接続成功にします。
4. Readerの実機受信は、本人が承認したテスト用リマインダーを1件作り、通知DBから受け口・台帳まで一度だけ届いたことを確認します。架空イベント試験は実機接続の証拠にしません。
5. `status.sh` と `python -m yoteiban_watch.doctor --config ... --only reader` を確認します。失敗した工程の `detail_code` だけを確認し、その工程を `--only <工程>` で再試験します。鍵、URL、本文はログに出しません。

通知が本人に届かない場合は、外部監視サービスが使えなくても朝のまとめで「昨夜の台帳が無い／最終心拍が古い」を知らせる運用にできます。
