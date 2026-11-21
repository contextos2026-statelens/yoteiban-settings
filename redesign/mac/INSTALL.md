# Mac miniへの導入・停止・削除

この手順は本人が承認した後に実行します。見張り番と時計は別々のlaunchdサービスとして動きます。どちらも専用の `com.yoteiban.*` 名と `~/yoteiban-watch` の場所を使い、すでに動いている、代わりに送るボットには触れません。

## 導入前

- `python3` を用意します。iMessageを読む場合は、フルディスクアクセスはlaunchdで実際に動くPython（`status.sh` または `ps` で見える実体）に付けます。HomebrewのPythonは更新で場所が変わり許可が外れることがあるので、更新後は `status.sh` と `watch.log.jsonl` の `unreadable` を確かめます。
- Gmail・カレンダーは読み取り専用で接続します。外部AIは既定で無効です。
- 通知先が署名を検証するよう設定します。署名は送信JSON本文のHMAC-SHA256で、鍵は `YOTEIBAN_WEBHOOK_KEY` に置きます。
- 連携ボットの受け口のようにAuthorizationヘッダーで鍵を受ける先には `YOTEIBAN_WEBHOOK_AUTH` を使います。
- 連携ボットの受け口へは `YOTEIBAN_WEBHOOK_FORMAT=envelope` を設定します。`YOTEIBAN_WEBHOOK_AUTH` には送り手の鍵だけを書けばBearerは自動で付きます。`env` は `sh` で読むため、空白を含む値は必ず `"..."` で囲みます。
- `heartbeat_url`、`clock_heartbeat_url`、`due_url`、`webhook_url` は本人が用意した値を `config.json` に設定します。Git管理下の例には実値を入れません。

## インストール

リポジトリの `redesign/mac` から次を実行します。

```sh
bash scripts/install.sh
```

スクリプトは `~/yoteiban-watch/app` にプログラム、`~/yoteiban-watch/state` に状態を置きます。設定例は `config.json` として初回だけコピーし、環境変数ファイル `env` も初回だけ作ります。既存の設定は上書きしません。`config.json` と `env` は所有者だけが読める権限にします。

`config.json` で最初は `shadow: true` にします。`~/yoteiban-watch/env` にWebhook URLと鍵を記入し、必要なら外部AI鍵も設定します。鍵の値はplistに書きません。

## 影の運用から本番へ

1. `bash scripts/status.sh` で見張り番・時計の状態、最後の心拍、pending件数を確認します。
2. `~/yoteiban-watch/state/watch.log.jsonl` と心拍先を確認し、影の運用で読み取りと通知判定を確かめます。shadow中はWebhookを呼びません。
3. 本人が切り替えを承認した後、`config.json` の `shadow` を `false` にします。
4. 通知先がHMAC署名とrequest_idを検証し、重複を防ぐことを確認します。サービスは設定ファイルを次回起動時に読みます。再起動する場合は下記の停止・起動手順を使います。

時刻の確認は本文を取得せず、`due_url` から案件IDと確認時刻だけを読みます。見張り番は `heartbeat_url`、時計は `clock_heartbeat_url` に毎回GETします。心拍失敗時は各URLの末尾に `/fail` を付けてGETします。

## 状態確認・停止・再開

状態を確認します。

```sh
bash scripts/status.sh
```

停止する場合は2サービスだけを停止します。

```sh
launchctl bootout "gui/$(id -u)/com.yoteiban.watch" || true
launchctl bootout "gui/$(id -u)/com.yoteiban.clock" || true
```

再開する場合はインストールスクリプトを再実行します。設定ファイルと環境ファイルは保持されます。

## アンインストール

```sh
bash scripts/uninstall.sh
```

見張り番と時計を停止し、専用plist、アプリ、設定、環境ファイルを削除します。`~/yoteiban-watch/state` は削除しません。代わりに送るボットや他のサービスには触れません。

## データの扱い

- メール・メッセージ・カレンダーは読み取りだけです。
- Webhookには起床に必要な本文を送ります。`watch.log.jsonl` には本文を書かず、request_id・情報源・状態・文字数だけを記録します。
- 外部AIを使う場合、伏せ字にした本文だけを渡します。`ai.enabled` は初期値 `false` のままです。
- 位置情報は記録しません。


## リマインダー通知Reader（任意）

これは本人が初回設定で「使う」を選んだ場合だけ導入します。導入前に、以前の LaunchAgent `com.yoteiban.reminder-notifications` が残っていれば先に停止・無効化し、その後にログイン項目を登録します。両方を同時に動かしません。既存の見張り番 `com.yoteiban.watch` は変更しません。スクリプトは確認を求め、「使う」が選ばれた場合だけ進みます。

Readerは通知記録を読み取り専用で開き、リマインダー発の通知から元の題名と届いた時刻だけを改変せず送ります。既存の `yoteiban_watch.waker.wake()`、既存のenv（envelope形式・認証）、既存の心拍URLを使います。受け口は保存前に題名を型分類し、台帳には出発／到着の番号と時刻、その他は短い要約だけを残します。題名に場所情報が含まれる場合、その題名は受け口まで送られますが、台帳には残りません。起動時には通知DBの最新位置を基準にするため、古い通知を送り直しません。成功記録は「起こし済み」かつHTTP 200のときだけです。DBが読めないときは間隔をあけて再試行し、既存の `yoteiban_watch.heartbeat.ping()` を呼びます。

導入は、Readerのソース置き場から次を実行します。本人が「使う」と選んだ場合に限ります。

```sh
bash install.sh
```

ログイン項目の追加後、通知記録へのアクセスに必要な場合は、本人がシステム設定でフルディスクアクセスを手動で許可します。これはMacのほぼ全部のファイルを読める広い権限です。実際に通知DBを読むプログラムに付けてください。更新で場所が変わるPython実行ファイルでは許可が外れることがあるため、実行実体を確認してから選びます。自動では付与しません。位置の端末（「探す」）とリマインダーの位置情報許可も本人が設定し、自動では変更しません。

同じAppleアカウントでない場合、移動用のリスト共有が必要です。共有後に場所通知がMacへ届くかは未確認です。常駐Macがない場合の代替は時刻のリマインダーだけで、場所の出発・到着合図は使いません。このPRには位置リマインダーの自動作成や移動の進み具合、カバー方法は含めません。

試験は架空のSQLite通知fixtureだけを使い、本物の通知DB・本番env・本番Macには接続しません。既存のログイン項目登録状況や本番ルーチンは変更しません。
